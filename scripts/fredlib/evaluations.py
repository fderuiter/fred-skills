from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .common import (
    ROOT,
    SUITE_DELIVERABLES,
    WORKFLOW_DELIVERABLE_SCHEMAS,
    WORKFLOW_PHASE_SCHEMAS,
    WORKFLOW_SKILL_DELIVERABLES,
    _infer_suite_name,
    _load_canonical_validator,
    _load_suite_contracts,
    _load_workflow_companions,
    _skill_to_suite_map,
    _workflow_phase_order_map,
    atomic_json,
    digest,
    package_files,
    read_json,
    relative_path,
    version,
)

_SUITE_TO_CONTRACT: dict[str, str] = {
    "repository-audits": "audit",
    "software-architecture": "architecture",
    "code-review": "review",
    "automated-testing": "testing",
    "spec-driven-development": "spec",
    "repository-workflows": "workflow",
}

_CONTRACT_TO_SUITE: dict[str, str] = {
    contract: suite for suite, contract in _SUITE_TO_CONTRACT.items()
}

_VALID_CONTRACTS: frozenset[str] = frozenset(_SUITE_TO_CONTRACT.values())

_AUDIT_STATUSES: frozenset[str] = frozenset(
    {
        "confirmed",
        "supported",
        "needs-validation",
        "hardening",
        "refuted",
    }
)

_ACTIONABLE_AUDIT_STATUSES: frozenset[str] = frozenset({"confirmed", "supported"})

_PROMPT_CONFIG_LINES: dict[str, tuple[str, ...]] = {
    "with_skill": (
        "A skill is available at ./skill/SKILL.md. Use it only if it applies to the user's request.",
        "Inspect only this case's source and optional skill; do not inspect grading data, parent workspaces, or other cases.",
    ),
    "without_skill": (
        "Inspect only this case's source; do not inspect grading data, parent workspaces, or other cases.",
    ),
}

_GLOB_CHAR_TOKENS: dict[str, str] = {
    "*": "[^/]*",
    "?": "[^/]",
}

_VALID_TESTING_ORACLES: frozenset[str] = frozenset(
    {"state", "exception", "output", "boundary"}
)

_WORKFLOW_DIRECT_ASSERTION_FIELDS: tuple[str, ...] = (
    "finding_id",
    "reproduction_status",
    "confidence_rating",
    "test_file",
    "original_reproduced",
    "regression_passed",
    "safe_controls_passed",
    "result",
    "next_action",
    "compression_level",
    "commit_type",
    "git_mutated",
)

_BENCHMARK_LIMITATIONS: list[str] = [
    "Property-anchor matching is a bounded deterministic rubric, not semantic proof; review raw evidence and traces.",
    "The adapter has normal process access. A fresh directory prevents accidental expected-answer leakage, not a security sandbox.",
    "Log files are streamed without a disk quota. POSIX timeouts kill the process group; Windows stops the direct child only. Run untrusted adapters in an external sandbox with resource limits.",
    "The provided deterministic reference adapter tests this harness, not an LLM. Label model/host versions in real adapter output.",
]

_LOCATION_PAIR_LEN = 2
_MAX_COMMIT_SUBJECT_LEN = 50
_DFS_VISITING = 1
_DFS_VISITED = 2
_MAX_REPEATS = 10
_MAX_TIMEOUT_SECONDS = 3600
_MAX_RESULT_BYTES = 2_000_000


class _SuiteValidator(Protocol):
    def validate(self, data: Any, schema: Mapping[str, Any]) -> list[str]: ...


@dataclass
class _GradeOutcome:
    reported: set[str]
    grounded: set[str]
    expected: set[str]
    uncertain_count: int
    assertions_ok: bool
    normalized_records: list[dict[str, str]] = field(default_factory=list)
    malformed_error: str | None = None


@dataclass
class _AnchorContext:
    anchors: list[dict[str, Any]]
    use_anchors: bool
    claimed: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _RunContext:
    output: Path
    command: list[str]
    timeout: int
    root: Path
    max_tokens: int | None


@dataclass(frozen=True)
class _CaseContext:
    case: dict[str, Any]
    contract: str
    schema_path: Path
    writable_paths: list[str] | None


@dataclass
class _AdapterExecution:
    state: str = "executed"
    error: str | None = None
    payload: dict[str, Any] | None = None
    actor: dict[str, Any] = field(default_factory=lambda: {"kind": "unknown"})
    usage: Any = None


def _load_suite_validator(contract: str) -> _SuiteValidator:
    if contract not in _VALID_CONTRACTS:
        raise ValueError(f"Unsupported evaluation contract: {contract!r}")
    return _load_canonical_validator(contract, root=ROOT)


def _resolve_case_contract(case: dict[str, Any], suite_map: dict[str, str]) -> str:
    explicit = case.get("contract")
    if explicit is not None:
        if not isinstance(explicit, str) or explicit not in _VALID_CONTRACTS:
            raise ValueError(f"Unsupported evaluation contract: {explicit!r}")
        return explicit
    skill_name = str(case.get("skill", ""))
    suite_name = suite_map.get(skill_name)
    if suite_name not in _SUITE_TO_CONTRACT:
        suite_name = _infer_suite_name(skill_name)
    return _SUITE_TO_CONTRACT.get(suite_name, "audit")


def _resolve_workflow_phase_schema_rel(skill_name: str, root: Path) -> str | None:
    deliverables = WORKFLOW_SKILL_DELIVERABLES.get(skill_name) or ()
    companions = _load_workflow_companions(root)
    for deliverable_name in deliverables:
        schema_rel = companions.get(
            deliverable_name
        ) or WORKFLOW_DELIVERABLE_SCHEMAS.get(deliverable_name)
        if isinstance(schema_rel, str):
            return schema_rel
    phase_id = _workflow_phase_order_map(root).get(skill_name)
    if phase_id is not None and phase_id in WORKFLOW_PHASE_SCHEMAS:
        phase_deliverable, default_rel = WORKFLOW_PHASE_SCHEMAS[phase_id]
        return companions.get(phase_deliverable, default_rel)
    return None


def _is_regular_schema_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _first_regular_schema(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if _is_regular_schema_file(candidate):
            return candidate
    return None


def _resolve_explicit_schema(explicit: Any, root: Path) -> Path:
    if not isinstance(explicit, str) or not explicit.strip():
        raise ValueError("Evaluation case schema must be a relative path string")
    schema_path = root / relative_path(explicit)
    if not _is_regular_schema_file(schema_path):
        raise ValueError(f"Missing evaluation schema: {explicit}")
    return schema_path


def _resolve_suite_schema(
    contract: str, skill_name: str, skill_dir: Path, root: Path
) -> Path:
    suite_name = _CONTRACT_TO_SUITE[contract]
    suite_contracts = _load_suite_contracts(root)
    _, default_schema = SUITE_DELIVERABLES[suite_name]
    schema_rel = str(suite_contracts.get(suite_name, {}).get("schema", default_schema))
    found = _first_regular_schema(
        [
            root / relative_path(schema_rel),
            skill_dir / "assets" / Path(schema_rel).name,
        ]
    )
    if found is None:
        raise ValueError(f"Missing output schema for skill: {skill_name}")
    return found


def _resolve_workflow_schema(skill_name: str, skill_dir: Path, root: Path) -> Path:
    candidates: list[Path] = []
    wf_schema_rel = _resolve_workflow_phase_schema_rel(skill_name, root)
    if wf_schema_rel is not None:
        candidates.append(root / relative_path(wf_schema_rel))
        candidates.append(skill_dir / "assets" / Path(wf_schema_rel).name)
    candidates.append(skill_dir / "assets" / f"{skill_name}.schema.json")
    asset_schemas = sorted((skill_dir / "assets").glob("*.schema.json"))
    candidates.extend(
        p for p in asset_schemas if not p.name.startswith("evaluation-scenarios")
    )
    found = _first_regular_schema(candidates)
    if found is None:
        raise ValueError(f"Missing output schema for skill: {skill_name}")
    return found


def _resolve_case_schema(case: dict[str, Any], contract: str, root: Path) -> Path:
    explicit = case.get("schema")
    if explicit is not None:
        return _resolve_explicit_schema(explicit, root)
    skill_name = str(case.get("skill", ""))
    skill_dir = root / "skills" / relative_path(skill_name)
    if contract != "workflow":
        return _resolve_suite_schema(contract, skill_name, skill_dir, root)
    return _resolve_workflow_schema(skill_name, skill_dir, root)


def _build_prompt_request(
    case: dict[str, Any],
    configuration: str,
) -> str:
    config_lines = _PROMPT_CONFIG_LINES.get(
        configuration, _PROMPT_CONFIG_LINES["without_skill"]
    )
    lines = [
        str(case["prompt"]).strip(),
        "",
        "Inspect ./source and write your structured JSON deliverable to result.json.",
        *config_lines,
    ]
    return "\n".join(lines)


def _unwrap_adapter_result(raw: Any) -> tuple[dict[str, Any], dict[str, Any], Any]:
    if not isinstance(raw, dict):
        raise TypeError("Adapter result must be a JSON object")
    raw_actor = raw.get("actor", {"kind": "unknown"})
    actor = raw_actor if isinstance(raw_actor, dict) else {"kind": "unknown"}
    usage = raw.get("usage")
    if "deliverable" in raw:
        deliverable = raw["deliverable"]
        if not isinstance(deliverable, dict):
            raise TypeError("Adapter deliverable field must be a JSON object")
        extra_keys = set(raw) - {"deliverable", "actor", "usage"}
        if extra_keys:
            raise ValueError(
                f"Unexpected wrapper fields alongside deliverable: {sorted(extra_keys)}"
            )
        return deliverable, actor, usage
    stripped = {k: v for k, v in raw.items() if k not in {"actor", "usage"}}
    return stripped, actor, usage


def _validate_contract_payload(
    payload: dict[str, Any], contract: str, schema_path: Path | None
) -> None:
    if schema_path is None:
        raise ValueError(f"Missing schema path for {contract} contract")
    schema_doc = read_json(schema_path)
    if not isinstance(schema_doc, Mapping):
        raise TypeError(f"Invalid schema document: {schema_path.name}")
    validator = _load_suite_validator(contract)
    errors = validator.validate(payload, schema_doc)
    if errors:
        raise ValueError(
            f"Adapter result failed {schema_path.name} validation: {errors[0]}"
        )


def _has_unsafe_path_segments(cleaned: str) -> bool:
    if not cleaned or cleaned.startswith("/"):
        return True
    return any(part in {"..", ".git"} for part in cleaned.split("/"))


def _normalize_writable_pattern(pattern: str) -> str:
    if not isinstance(pattern, str) or not pattern.strip() or "\\" in pattern:
        raise ValueError(f"Invalid writable_paths glob pattern: {pattern!r}")
    cleaned = pattern.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if _has_unsafe_path_segments(cleaned):
        raise ValueError(f"Unsafe writable_paths glob pattern: {pattern!r}")
    return cleaned


def _consume_glob_bracket(norm: str, idx: int) -> tuple[str, int]:
    end = norm.find("]", idx + 1)
    if end == -1:
        return re.escape("["), idx + 1
    inner = norm[idx + 1 : end]
    if inner.startswith("!"):
        inner = "^" + inner[1:]
    return f"[{inner}]", end + 1


def _consume_glob_token(norm: str, idx: int) -> tuple[str, int]:
    if norm.startswith("**/", idx):
        return "(?:.+/)?", idx + 3
    if norm.startswith("**", idx):
        return ".*", idx + 2
    ch = norm[idx]
    if ch == "[":
        return _consume_glob_bracket(norm, idx)
    return _GLOB_CHAR_TOKENS.get(ch, re.escape(ch)), idx + 1


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    norm = _normalize_writable_pattern(pattern)
    parts: list[str] = ["^"]
    idx = 0
    while idx < len(norm):
        token_regex, idx = _consume_glob_token(norm, idx)
        parts.append(token_regex)
    parts.append(r"\Z")
    return re.compile("".join(parts))


def _matches_writable_glob(rel_posix: str, pattern: str) -> bool:
    return bool(_glob_to_regex(pattern).match(rel_posix))


def _is_approved_writable_path(rel_posix: str, patterns: list[str]) -> bool:
    return any(_matches_writable_glob(rel_posix, pat) for pat in patterns)


def _evaluate_source_changes(
    inputs_before: dict[str, str],
    source_dir: Path,
    writable_paths: list[str] | None,
) -> tuple[bool, set[str], str | None]:
    patterns = list(writable_paths or [])
    for pat in patterns:
        _normalize_writable_pattern(pat)
    try:
        inputs_after = package_files(source_dir)
    except (OSError, ValueError):
        return True, set(), "Adapter modified the read-only evaluation source"
    changed = {
        rel
        for rel in (set(inputs_before) | set(inputs_after))
        if inputs_before.get(rel) != inputs_after.get(rel)
    }
    if not changed:
        return False, set(), None
    if not patterns:
        return True, changed, "Adapter modified the read-only evaluation source"
    unapproved = sorted(
        rel for rel in changed if not _is_approved_writable_path(rel, patterns)
    )
    if unapproved:
        return (
            True,
            changed,
            f"Adapter modified unapproved source path: {unapproved[0]}",
        )
    return True, changed, None


def _case_anchors(case: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(case, dict):
        return []
    raw = case.get("expected_anchors")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _build_anchor_context(case: dict[str, Any] | None) -> _AnchorContext:
    return _AnchorContext(
        anchors=_case_anchors(case),
        use_anchors=isinstance(case, dict) and "expected_anchors" in case,
    )


def _anchor_label(index: int, anchor: dict[str, Any]) -> str:
    explicit = anchor.get("anchor_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return f"anchor-{index + 1}"


def _claim_anchor(
    anchors: list[dict[str, Any]],
    claimed: set[int],
    matcher: Callable[..., bool],
    *args: Any,
) -> str | None:
    for idx, anchor in enumerate(anchors):
        if idx not in claimed and matcher(*args, anchor):
            claimed.add(idx)
            return _anchor_label(idx, anchor)
    return None


def _resolve_anchored_key(
    ctx: _AnchorContext,
    anchored_fallback: str,
    plain_fallback: str,
    matcher: Callable[..., bool],
    *args: Any,
) -> str:
    if not ctx.use_anchors:
        return plain_fallback
    matched = _claim_anchor(ctx.anchors, ctx.claimed, matcher, *args)
    return matched if matched is not None else anchored_fallback


def _value_matches_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return actual in expected
    return bool(actual == expected)


def _stripped_str(mapping: Mapping[str, Any], key: str) -> str:
    val = mapping.get(key)
    return val.strip() if isinstance(val, str) else ""


def _stripped_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _expected_keys_from_case(case: dict[str, Any]) -> set[str]:
    if "expected_anchors" in case:
        return {
            _anchor_label(idx, anchor) for idx, anchor in enumerate(_case_anchors(case))
        }
    base = {str(k) for k in case.get("expected_keys", [])}
    assertions = case.get("expected_assertions")
    if isinstance(assertions, dict):
        vals = assertions.get("changed_files")
        if isinstance(vals, list):
            base.update(str(v) for v in vals)
    return base


def _make_normalized_record(key: str, status: str, evidence: str) -> dict[str, str]:
    normalized_status = status if status in _AUDIT_STATUSES else "supported"
    return {
        "key": key,
        "status": normalized_status,
        "evidence": evidence.strip(),
    }


def _audit_finding_locations(
    finding: dict[str, Any], evidence_map: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    raw_ids = finding.get("evidence_ids")
    if not isinstance(raw_ids, list):
        return []
    pairs: set[tuple[str, str]] = set()
    for eid in raw_ids:
        if isinstance(eid, str) and eid in evidence_map:
            ev = evidence_map[eid]
            pairs.add((str(ev["path"]), str(ev["symbol"])))
    return sorted(pairs)


def _parse_expected_locations(raw_locs: Any) -> list[tuple[str, str]] | None:
    if not isinstance(raw_locs, list):
        return None
    pairs: set[tuple[str, str]] = set()
    for pair in raw_locs:
        if isinstance(pair, list | tuple) and len(pair) == _LOCATION_PAIR_LEN:
            pairs.add((str(pair[0]), str(pair[1])))
    return sorted(pairs)


def _audit_locations_match(
    locations: list[tuple[str, str]], anchor: dict[str, Any]
) -> bool:
    if "locations" not in anchor:
        return True
    expected_locs = _parse_expected_locations(anchor["locations"])
    if expected_locs is None:
        return False
    if anchor.get("locations_subset"):
        return set(expected_locs).issubset(set(locations))
    return locations == expected_locs


def _audit_anchor_matches(
    finding: dict[str, Any],
    locations: list[tuple[str, str]],
    anchor: dict[str, Any],
) -> bool:
    if "topic" in anchor and not _value_matches_expected(
        finding.get("topic"), anchor["topic"]
    ):
        return False
    if "root_cause" in anchor and not _value_matches_expected(
        finding.get("root_cause"), anchor["root_cause"]
    ):
        return False
    return _audit_locations_match(locations, anchor)


def _is_valid_audit_evidence(ev: Any) -> bool:
    if not isinstance(ev, dict):
        return False
    return all(isinstance(ev.get(field), str) for field in ("id", "path", "symbol"))


def _build_audit_evidence_map(raw_evidence: list[Any]) -> dict[str, dict[str, Any]]:
    evidence_map: dict[str, dict[str, Any]] = {}
    for ev in raw_evidence:
        if not _is_valid_audit_evidence(ev):
            raise TypeError("Malformed audit evidence record")
        evidence_map[ev["id"]] = ev
    return evidence_map


def _extract_known_contracts(raw_contracts: list[Any]) -> set[str]:
    known: set[str] = set()
    for contract in raw_contracts:
        if isinstance(contract, dict) and isinstance(contract.get("id"), str):
            known.add(contract["id"])
    return known


def _validate_audit_finding_item(item: Any) -> tuple[str, str, str]:
    if not isinstance(item, dict):
        raise TypeError("Malformed audit finding record")
    if "key" in item or "category" in item:
        raise ValueError("Audit findings must not include non-schema key/category")
    raw_status = item.get("status")
    if not isinstance(raw_status, str) or raw_status not in _AUDIT_STATUSES:
        raise ValueError("Malformed audit finding status")
    topic = _stripped_str(item, "topic")
    root_cause = _stripped_str(item, "root_cause")
    fid = _stripped_str(item, "id")
    if not topic or not root_cause or not fid:
        raise ValueError("Malformed audit finding identity")
    return str(item["status"]), str(item["topic"]), str(item["root_cause"])


def _resolve_audit_record_key(
    item: dict[str, Any],
    locations: list[tuple[str, str]],
    ctx: _AnchorContext,
    fallback_pair: tuple[str, str],
) -> str:
    anchored_fallback, plain_fallback = fallback_pair
    if not ctx.use_anchors:
        return plain_fallback
    if item.get("status") in _ACTIONABLE_AUDIT_STATUSES:
        matched = _claim_anchor(
            ctx.anchors, ctx.claimed, _audit_anchor_matches, item, locations
        )
        if matched is not None:
            return matched
    return anchored_fallback


def _audit_contracts_valid(contract_ids: Any, known_contracts: set[str]) -> bool:
    if not isinstance(contract_ids, list) or not contract_ids:
        return False
    return all(isinstance(cid, str) and cid in known_contracts for cid in contract_ids)


def _collect_audit_evidence_snippets(
    ev_ids: Any, evidence_map: dict[str, dict[str, Any]]
) -> list[str]:
    if not isinstance(ev_ids, list):
        return []
    snippets: list[str] = []
    for eid in ev_ids:
        ev = evidence_map.get(eid) if isinstance(eid, str) else None
        if ev is None:
            continue
        path_str = _stripped_str(ev, "path")
        ctx_str = _stripped_str(ev, "context")
        if path_str and ctx_str:
            snippets.append(f"{ev['path']}:{ev['symbol']} ({ctx_str})")
    return snippets


def _format_audit_finding_evidence(
    item: dict[str, Any],
    raw_status: str,
    evidence_map: dict[str, dict[str, Any]],
    known_contracts: set[str],
) -> str:
    ev_snippets = _collect_audit_evidence_snippets(
        item.get("evidence_ids"), evidence_map
    )
    joined_snippets = "; ".join(ev_snippets)
    if raw_status in _ACTIONABLE_AUDIT_STATUSES:
        contracts_ok = _audit_contracts_valid(item.get("contract_ids"), known_contracts)
        return joined_snippets if (joined_snippets and contracts_ok) else ""
    fallback_note = str(item.get("counterevidence") or item.get("actual") or "").strip()
    return joined_snippets or fallback_note


def _project_audit_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    items = payload.get("findings")
    raw_evidence = payload.get("evidence")
    raw_contracts = payload.get("contracts")
    if not all(isinstance(x, list) for x in (items, raw_evidence, raw_contracts)):
        raise TypeError("Malformed audit ledger response")
    assert isinstance(items, list)
    assert isinstance(raw_evidence, list)
    assert isinstance(raw_contracts, list)

    evidence_map = _build_audit_evidence_map(raw_evidence)
    known_contracts = _extract_known_contracts(raw_contracts)
    ctx = _build_anchor_context(case)
    seen_fingerprints: dict[str, int] = {}
    records: list[dict[str, str]] = []

    for idx, item in enumerate(items):
        raw_status, topic, root_cause = _validate_audit_finding_item(item)
        locations = _audit_finding_locations(item, evidence_map)
        fp = digest({"topic": topic, "root_cause": root_cause, "locations": locations})
        seen_count = seen_fingerprints.get(fp, 0) + 1
        seen_fingerprints[fp] = seen_count
        plain_key = root_cause if seen_count == 1 else f"{root_cause}:{seen_count}"
        record_key = _resolve_audit_record_key(
            item, locations, ctx, (f"audit:{idx + 1}:{fp[:12]}", plain_key)
        )
        evidence_text = _format_audit_finding_evidence(
            item, raw_status, evidence_map, known_contracts
        )
        records.append(_make_normalized_record(record_key, raw_status, evidence_text))
    return records


def _anchor_paths_match(item: dict[str, Any], anchor: dict[str, Any]) -> bool:
    expected_paths = anchor.get("paths")
    if not isinstance(expected_paths, list):
        return True
    raw_paths = item.get("paths")
    actual_paths = {str(p) for p in raw_paths} if isinstance(raw_paths, list) else set()
    return {str(p) for p in expected_paths}.issubset(actual_paths)


def _min_list_field_matches(
    item: dict[str, Any], field_name: str, anchor: dict[str, Any], anchor_key: str
) -> bool:
    if anchor_key not in anchor:
        return True
    values = item.get(field_name)
    count = len(values) if isinstance(values, list) else 0
    return count >= int(anchor[anchor_key])


def _architecture_boundary_matches(
    boundary: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") not in {None, "boundary"}:
        return False
    if "type" in anchor and not _value_matches_expected(
        boundary.get("type"), anchor["type"]
    ):
        return False
    if not _min_list_field_matches(boundary, "invariants", anchor, "min_invariants"):
        return False
    return _anchor_paths_match(boundary, anchor)


def _architecture_decision_matches(
    decision: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") != "decision":
        return False
    if not _min_list_field_matches(
        decision, "consequences", anchor, "min_consequences"
    ):
        return False
    return _anchor_paths_match(decision, anchor)


def _architecture_statuses(doc_status: Any) -> tuple[str, str]:
    if doc_status == "draft":
        return "needs-validation", "needs-validation"
    if doc_status == "accepted":
        return "supported", "confirmed"
    return "supported", "supported"


def _project_boundary_record(
    idx: int, boundary: Any, status: str, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(boundary, dict) or not isinstance(boundary.get("name"), str):
        return None
    name = boundary["name"]
    evidence = _stripped_str(boundary, "description") or "; ".join(
        _stripped_str_list(boundary.get("invariants"))
    )
    key = _resolve_anchored_key(
        ctx,
        f"boundary:{idx + 1}:{name}",
        str(name),
        _architecture_boundary_matches,
        boundary,
    )
    return _make_normalized_record(key, status, evidence)


def _project_decision_record(
    idx: int, decision: Any, status: str, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
        return None
    did = decision["id"]
    dec_text = _stripped_str(decision, "decision")
    rationale = _stripped_str(decision, "context") or "; ".join(
        _stripped_str_list(decision.get("consequences"))
    )
    evidence = f"{dec_text} ({rationale})" if (dec_text and rationale) else ""
    key = _resolve_anchored_key(
        ctx,
        f"decision:{idx + 1}:{did}",
        str(did),
        _architecture_decision_matches,
        decision,
    )
    return _make_normalized_record(key, status, evidence)


def _project_architecture_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    boundary_status, decision_status = _architecture_statuses(payload.get("status"))
    ctx = _build_anchor_context(case)
    records: list[dict[str, str]] = []
    for idx, boundary in enumerate(payload.get("boundaries", [])):
        rec = _project_boundary_record(idx, boundary, boundary_status, ctx)
        if rec is not None:
            records.append(rec)
    for idx, decision in enumerate(payload.get("decisions", [])):
        rec = _project_decision_record(idx, decision, decision_status, ctx)
        if rec is not None:
            records.append(rec)
    return records


def _review_finding_matches(finding: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if "file" in anchor and not _value_matches_expected(
        finding.get("file"), anchor["file"]
    ):
        return False
    if "severity" in anchor and not _value_matches_expected(
        finding.get("severity"), anchor["severity"]
    ):
        return False
    return not ("line" in anchor and finding.get("line") != anchor["line"])


def _review_severity_status(severity: Any) -> str:
    if severity == "advisory":
        return "needs-validation"
    if severity in {"critical", "high"}:
        return "confirmed"
    return "supported"


def _project_single_review_finding(
    idx: int, finding: Any, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
        return None
    fid = finding["id"]
    status = _review_severity_status(finding.get("severity"))
    file_ref = _stripped_str(finding, "file")
    desc = _stripped_str(finding, "description")
    evidence = f"{file_ref}: {desc}" if (file_ref and desc) else ""
    key = _resolve_anchored_key(
        ctx, f"review:{idx + 1}:{fid}", str(fid), _review_finding_matches, finding
    )
    return _make_normalized_record(key, status, evidence)


def _project_review_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    ctx = _build_anchor_context(case)
    records: list[dict[str, str]] = []
    for idx, finding in enumerate(payload.get("findings", [])):
        rec = _project_single_review_finding(idx, finding, ctx)
        if rec is not None:
            records.append(rec)
    return records


def _testing_case_matches(tc: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if "oracle_type" in anchor and not _value_matches_expected(
        tc.get("oracle_type"), anchor["oracle_type"]
    ):
        return False
    if "verified" in anchor and tc.get("verified") is not anchor["verified"]:
        return False
    return not (
        "file" in anchor and not _value_matches_expected(tc.get("file"), anchor["file"])
    )


def _testing_verified_status(verified: Any) -> str:
    if verified is True:
        return "confirmed"
    if verified is False:
        return "needs-validation"
    return "supported"


def _project_single_testing_case(
    idx: int, tc: Any, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(tc, dict) or not isinstance(tc.get("id"), str):
        return None
    tid = tc["id"]
    status = _testing_verified_status(tc.get("verified"))
    desc = _stripped_str(tc, "description")
    oracle = tc.get("oracle_type")
    evidence = (
        f"{oracle}: {desc}" if (desc and oracle in _VALID_TESTING_ORACLES) else ""
    )
    key = _resolve_anchored_key(
        ctx, f"test:{idx + 1}:{tid}", str(tid), _testing_case_matches, tc
    )
    return _make_normalized_record(key, status, evidence)


def _project_testing_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    ctx = _build_anchor_context(case)
    records: list[dict[str, str]] = []
    for idx, tc in enumerate(payload.get("test_cases", [])):
        rec = _project_single_testing_case(idx, tc, ctx)
        if rec is not None:
            records.append(rec)
    return records


_SPEC_EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "interview_decision": ("category",),
    "requirement": ("priority",),
    "convergence_check": ("status", "target"),
}


def _spec_item_matches(kind: str, item: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if anchor.get("kind") != kind:
        return False
    for field_name in _SPEC_EXPECTED_FIELDS.get(kind, ()):
        if field_name in anchor and not _value_matches_expected(
            item.get(field_name), anchor[field_name]
        ):
            return False
    return True


def _project_spec_decision(
    idx: int, decision: Any, in_progress: bool, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
        return None
    did = decision["id"]
    question = _stripped_str(decision, "question")
    chosen = _stripped_str(decision, "decision")
    evidence = f"{question} -> {chosen}" if (question and chosen) else ""
    status = "needs-validation" if in_progress else "confirmed"
    key = _resolve_anchored_key(
        ctx,
        f"interview_decision:{idx + 1}:{did}",
        str(did),
        _spec_item_matches,
        "interview_decision",
        decision,
    )
    return _make_normalized_record(key, status, evidence)


def _project_spec_requirement(
    idx: int, req: Any, in_progress: bool, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(req, dict) or not isinstance(req.get("id"), str):
        return None
    rid = req["id"]
    desc = _stripped_str(req, "description")
    prio = _stripped_str(req, "priority")
    evidence = f"[{req['priority']}] {desc}" if (desc and prio) else desc
    status = "needs-validation" if in_progress else "confirmed"
    key = _resolve_anchored_key(
        ctx,
        f"requirement:{idx + 1}:{rid}",
        str(rid),
        _spec_item_matches,
        "requirement",
        req,
    )
    return _make_normalized_record(key, status, evidence)


def _spec_convergence_status(check: dict[str, Any], in_progress: bool) -> str:
    if in_progress:
        return "needs-validation"
    return "confirmed" if check.get("status") == "aligned" else "supported"


def _project_spec_convergence_check(
    idx: int, check: Any, in_progress: bool, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(check, dict) or not isinstance(check.get("id"), str):
        return None
    cid = check["id"]
    target = _stripped_str(check, "target")
    ev_text = _stripped_str(check, "evidence")
    evidence = f"{target}: {ev_text}" if (target and ev_text) else ""
    status = _spec_convergence_status(check, in_progress)
    key = _resolve_anchored_key(
        ctx,
        f"convergence_check:{idx + 1}:{cid}",
        str(cid),
        _spec_item_matches,
        "convergence_check",
        check,
    )
    return _make_normalized_record(key, status, evidence)


def _project_spec_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    in_progress = payload.get("status") in {"draft", "interviewing"}
    ctx = _build_anchor_context(case)
    records: list[dict[str, str]] = []
    sections: tuple[tuple[str, Callable[..., dict[str, str] | None]], ...] = (
        ("interview_decisions", _project_spec_decision),
        ("requirements", _project_spec_requirement),
        ("convergence_checks", _project_spec_convergence_check),
    )
    for field_name, projector in sections:
        for idx, item in enumerate(payload.get(field_name, [])):
            rec = projector(idx, item, in_progress, ctx)
            if rec is not None:
                records.append(rec)
    return records


def _wf_match_finding_verification(
    props: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if "reproduction_status" not in anchor:
        return True
    return _value_matches_expected(
        props.get("reproduction_status"), anchor["reproduction_status"]
    )


def _wf_match_regression(props: dict[str, Any], anchor: dict[str, Any]) -> bool:
    test_file = str(props.get("test_file", ""))
    if "test_file" in anchor and test_file != anchor["test_file"]:
        return False
    if "test_glob" in anchor:
        return bool(test_file) and _matches_writable_glob(
            test_file, str(anchor["test_glob"])
        )
    return True


def _wf_match_task(props: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if "file_scopes" in anchor and list(props.get("file_scopes", [])) != list(
        anchor["file_scopes"]
    ):
        return False
    if "has_blockers" in anchor:
        return bool(props.get("has_blockers")) is bool(anchor["has_blockers"])
    return True


def _wf_match_exact_fields(
    field_names: tuple[str, ...],
) -> Callable[[dict[str, Any], dict[str, Any]], bool]:
    def _matcher(props: dict[str, Any], anchor: dict[str, Any]) -> bool:
        return all(
            field_name not in anchor or props.get(field_name) == anchor[field_name]
            for field_name in field_names
        )

    return _matcher


def _wf_match_always(_props: dict[str, Any], _anchor: dict[str, Any]) -> bool:
    return True


_WORKFLOW_ANCHOR_MATCHERS: dict[
    str, Callable[[dict[str, Any], dict[str, Any]], bool]
] = {
    "finding_verification": _wf_match_finding_verification,
    "regression": _wf_match_regression,
    "task": _wf_match_task,
    "file_change": _wf_match_exact_fields(("path",)),
    "verification": _wf_match_exact_fields(("result",)),
    "transition": _wf_match_exact_fields(
        ("fingerprint", "previous_state", "new_state")
    ),
    "handoff_file": _wf_match_exact_fields(("path",)),
    "handoff_untouched": _wf_match_always,
    "commit": _wf_match_exact_fields(("commit_type",)),
}


def _workflow_anchor_matches(
    kind: str, props: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") != kind:
        return False
    matcher = _WORKFLOW_ANCHOR_MATCHERS.get(kind)
    return matcher(props, anchor) if matcher is not None else True


def _wf_finding_verification_evidence(payload: dict[str, Any]) -> str:
    ev_parts = [
        text
        for text in (_stripped_str(payload, "result"), _stripped_str(payload, "notes"))
        if text
    ]
    if ev_parts:
        return "; ".join(ev_parts)
    if payload.get("commands"):
        return "; ".join(str(c) for c in payload["commands"])
    if payload.get("safe_control_outcomes"):
        return "safe_control_outcomes recorded"
    return ""


def _project_wf_finding_verification(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    fid = str(payload.get("finding_id", ""))
    if not fid or fid == "none":
        return []
    raw_status = str(payload.get("reproduction_status", "needs-validation"))
    evidence = _wf_finding_verification_evidence(payload)
    if not ctx.use_anchors:
        return [_make_normalized_record(fid, raw_status, evidence)]
    matched = (
        _claim_anchor(
            ctx.anchors,
            ctx.claimed,
            _workflow_anchor_matches,
            "finding_verification",
            {"reproduction_status": raw_status},
        )
        if raw_status in _ACTIONABLE_AUDIT_STATUSES
        else None
    )
    key = matched if matched is not None else f"verify:{fid}"
    return [_make_normalized_record(key, raw_status, evidence)]


def _project_wf_regression(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    fid = str(payload.get("finding_id", ""))
    if not fid or fid == "none":
        return []
    test_file = str(payload.get("test_file", ""))
    required_fields = (
        "failure_boundaries",
        "test_command_traces",
        "assertion_expectations",
    )
    grounded_ok = all(bool(payload.get(f)) for f in required_fields)
    ev = "; ".join(str(b) for b in payload["failure_boundaries"]) if grounded_ok else ""
    key = _resolve_anchored_key(
        ctx,
        f"regression:{fid}",
        fid,
        _workflow_anchor_matches,
        "regression",
        {"test_file": test_file},
    )
    return [_make_normalized_record(key, "confirmed", ev)]


def _project_single_wf_task(
    idx: int, task: Any, ctx: _AnchorContext
) -> dict[str, str] | None:
    if not isinstance(task, dict):
        return None
    tid = task.get("task_id")
    if not isinstance(tid, str) or tid == "noop":
        return None
    scopes = task.get("file_scopes")
    criteria = task.get("acceptance_criteria")
    has_blockers = bool(task.get("blocked_by") or task.get("dependencies"))
    ev = f"scopes={scopes}; criteria={criteria}" if (scopes and criteria) else ""
    props = {
        "file_scopes": scopes if isinstance(scopes, list) else [],
        "has_blockers": has_blockers,
    }
    key = _resolve_anchored_key(
        ctx, f"task:{idx + 1}:{tid}", tid, _workflow_anchor_matches, "task", props
    )
    return _make_normalized_record(key, "supported", ev)


def _project_wf_tasks(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for idx, task in enumerate(payload.get("task_breakdowns", [])):
        rec = _project_single_wf_task(idx, task, ctx)
        if rec is not None:
            records.append(rec)
    return records


def _extract_file_change_path(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("path", ""))
    return ""


def _project_wf_file_changes(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    exec_ok = any(
        isinstance(er, dict) and er.get("passed") is True
        for er in payload.get("execution_results", [])
    )
    verified_diff = bool(payload.get("diff_summaries")) and exec_ok
    records: list[dict[str, str]] = []
    for idx, item in enumerate(payload.get("file_changes", [])):
        path_val = _extract_file_change_path(item)
        if not path_val or path_val == "none":
            continue
        ev = f"{path_val}: verified diff" if verified_diff else ""
        key = _resolve_anchored_key(
            ctx,
            f"change:{idx + 1}:{path_val}",
            path_val,
            _workflow_anchor_matches,
            "file_change",
            {"path": path_val},
        )
        records.append(_make_normalized_record(key, "confirmed", ev))
    return records


def _project_wf_fix_verification(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    res_label = str(payload.get("result", ""))
    if not res_label or res_label == "abstained":
        return []
    cmds = payload.get("commands")
    ev = "; ".join(str(c) for c in cmds) if cmds else ""
    status = "confirmed" if res_label == "verified-fixed" else "supported"
    key = _resolve_anchored_key(
        ctx,
        f"verification:{res_label}",
        res_label,
        _workflow_anchor_matches,
        "verification",
        {"result": res_label},
    )
    return [_make_normalized_record(key, status, ev)]


def _extract_fingerprint_lookup(
    raw_matches: Any,
) -> tuple[set[Any], dict[str, str]]:
    matched_ids: set[Any] = set()
    fp_by_fid: dict[str, str] = {}
    if not isinstance(raw_matches, list):
        return matched_ids, fp_by_fid
    for match in raw_matches:
        if not isinstance(match, dict):
            continue
        fid = match.get("finding_id")
        matched_ids.add(fid)
        if isinstance(fid, str) and isinstance(match.get("fingerprint"), str):
            fp_by_fid[fid] = match["fingerprint"]
    return matched_ids, fp_by_fid


def _transition_status_and_evidence(
    trans: dict[str, Any], fid: str, new_state: str, matched_ids: set[Any]
) -> tuple[str, str]:
    proof = _stripped_str(trans, "verification_proof")
    if new_state == "verified-fixed":
        return "confirmed", proof
    reason = _stripped_str(trans, "reason")
    fallback = f"matched:{fid}" if fid in matched_ids else ""
    return "supported", proof or reason or fallback


def _project_wf_remediation_transitions(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    matched_ids, fp_by_fid = _extract_fingerprint_lookup(
        payload.get("finding_fingerprint_matches", [])
    )
    records: list[dict[str, str]] = []
    for idx, trans in enumerate(payload.get("remediation_state_transitions", [])):
        if not isinstance(trans, dict):
            continue
        fid = str(trans.get("finding_id", ""))
        new_state = str(trans.get("new_state", ""))
        if not fid or not new_state:
            continue
        props = {
            "fingerprint": str(trans.get("fingerprint") or fp_by_fid.get(fid, "")),
            "previous_state": str(trans.get("previous_state", "")),
            "new_state": new_state,
        }
        status, ev = _transition_status_and_evidence(trans, fid, new_state, matched_ids)
        key = _resolve_anchored_key(
            ctx,
            f"transition:{idx + 1}:{fid}:{new_state}",
            f"{fid}:{new_state}",
            _workflow_anchor_matches,
            "transition",
            props,
        )
        records.append(_make_normalized_record(key, status, ev))
    return records


def _project_wf_handoff_untouched(
    action_text: str, grounded_ok: bool, ctx: _AnchorContext
) -> list[dict[str, str]]:
    ev = f"untouched: {action_text}" if grounded_ok else ""
    matched_key = (
        _claim_anchor(
            ctx.anchors, ctx.claimed, _workflow_anchor_matches, "handoff_untouched", {}
        )
        if ctx.use_anchors
        else None
    )
    if matched_key is not None:
        return [_make_normalized_record(matched_key, "supported", ev)]
    return [_make_normalized_record("untouched", "refuted", ev)]


def _project_wf_handoff(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    action_text = _stripped_str(payload, "next_action")
    abstained = not action_text or action_text.lower() == "abstained"
    has_facts_and_cmds = bool(payload.get("verified_facts")) and bool(
        payload.get("commands_run")
    )
    grounded_ok = has_facts_and_cmds and not abstained
    touched = [
        p
        for p in payload.get("touched_files", [])
        if isinstance(p, str) and p not in {"", "none"}
    ]
    if not touched:
        if abstained:
            return []
        return _project_wf_handoff_untouched(action_text, grounded_ok, ctx)
    records: list[dict[str, str]] = []
    for idx, path_str in enumerate(touched):
        ev = f"{path_str}: {action_text}" if grounded_ok else ""
        key = _resolve_anchored_key(
            ctx,
            f"handoff:{idx + 1}:{path_str}",
            path_str,
            _workflow_anchor_matches,
            "handoff_file",
            {"path": path_str},
        )
        records.append(_make_normalized_record(key, "confirmed", ev))
    return records


def _is_grounded_commit(payload: dict[str, Any], subj: str) -> bool:
    if not (0 < len(subj) <= _MAX_COMMIT_SUBJECT_LEN):
        return False
    return (
        bool(payload.get("preserved_exact_spans"))
        and payload.get("git_mutated") is False
    )


def _project_wf_commit(
    payload: dict[str, Any], ctx: _AnchorContext
) -> list[dict[str, str]]:
    subj = str(payload.get("commit_subject", ""))
    ctype = str(payload.get("commit_type", ""))
    if not subj or subj.startswith("chore: abstain") or not ctype:
        return []
    ev = subj if _is_grounded_commit(payload, subj) else ""
    key = _resolve_anchored_key(
        ctx,
        f"commit:{ctype}",
        ctype,
        _workflow_anchor_matches,
        "commit",
        {"commit_type": ctype},
    )
    return [_make_normalized_record(key, "confirmed", ev)]


def _has_all_keys(keys: tuple[str, ...]) -> Callable[[dict[str, Any]], bool]:
    def _predicate(payload: dict[str, Any]) -> bool:
        return all(key in payload for key in keys)

    return _predicate


_WORKFLOW_RECORD_PROJECTORS: tuple[
    tuple[
        Callable[[dict[str, Any]], bool],
        Callable[[dict[str, Any], _AnchorContext], list[dict[str, str]]],
    ],
    ...,
] = (
    (_has_all_keys(("reproduction_status",)), _project_wf_finding_verification),
    (_has_all_keys(("failure_boundaries",)), _project_wf_regression),
    (_has_all_keys(("task_breakdowns",)), _project_wf_tasks),
    (_has_all_keys(("file_changes",)), _project_wf_file_changes),
    (
        _has_all_keys(("original_reproduced", "safe_controls_passed")),
        _project_wf_fix_verification,
    ),
    (
        _has_all_keys(("remediation_state_transitions",)),
        _project_wf_remediation_transitions,
    ),
    (_has_all_keys(("dead_ends", "verified_facts")), _project_wf_handoff),
    (_has_all_keys(("commit_subject", "compression_level")), _project_wf_commit),
)


def _project_workflow_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    ctx = _build_anchor_context(case)
    for predicate, projector in _WORKFLOW_RECORD_PROJECTORS:
        if predicate(payload):
            return projector(payload, ctx)
    return []


_CONTRACT_RECORD_PROJECTORS: dict[
    str, Callable[[dict[str, Any], dict[str, Any] | None], list[dict[str, str]]]
] = {
    "architecture": _project_architecture_records,
    "review": _project_review_records,
    "testing": _project_testing_records,
    "spec": _project_spec_records,
    "workflow": _project_workflow_records,
}


def _project_normalized_records(
    contract: str, payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    projector = _CONTRACT_RECORD_PROJECTORS.get(contract, _project_audit_records)
    return projector(payload, case)


def _score_normalized_records(
    records: list[dict[str, str]],
    *,
    report_needs_validation: bool = False,
    cap_uncertain_at_one: bool = False,
) -> tuple[set[str], set[str], int]:
    reportable = (
        _ACTIONABLE_AUDIT_STATUSES | {"needs-validation"}
        if report_needs_validation
        else _ACTIONABLE_AUDIT_STATUSES
    )
    reported = {rec["key"] for rec in records if rec["status"] in reportable}
    grounded = {rec["key"] for rec in records if rec["evidence"].strip()}
    uncertain = sum(rec["status"] == "needs-validation" for rec in records)
    if cap_uncertain_at_one:
        uncertain = min(uncertain, 1)
    return reported, grounded, uncertain


def _grade_audit(payload: dict[str, Any], case: dict[str, Any]) -> _GradeOutcome:
    expected = _expected_keys_from_case(case)
    try:
        records = _project_audit_records(payload, case)
    except (ValueError, TypeError):
        return _GradeOutcome(
            reported=set(),
            grounded=set(),
            expected=expected,
            uncertain_count=0,
            assertions_ok=False,
            malformed_error="Malformed findings response",
        )
    reported, grounded, uncertain = _score_normalized_records(records)
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=True,
        normalized_records=records,
    )


def _count_nested_list_entries(items: list[dict[str, Any]], key: str) -> int:
    total = 0
    for item in items:
        entries = item.get(key)
        if isinstance(entries, list):
            total += len(entries)
    return total


def _architecture_assertions_hold(
    payload: dict[str, Any],
    assertions: Any,
    boundaries: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> bool:
    if not isinstance(assertions, dict):
        return True
    if "status" in assertions and payload.get("status") != assertions["status"]:
        return False
    if "min_invariants" in assertions and _count_nested_list_entries(
        boundaries, "invariants"
    ) < int(assertions["min_invariants"]):
        return False
    return not (
        "min_consequences" in assertions
        and _count_nested_list_entries(decisions, "consequences")
        < int(assertions["min_consequences"])
    )


def _grade_architecture(payload: dict[str, Any], case: dict[str, Any]) -> _GradeOutcome:
    records = _project_architecture_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True, cap_uncertain_at_one=True
    )
    boundaries = [b for b in payload.get("boundaries", []) if isinstance(b, dict)]
    decisions = [d for d in payload.get("decisions", []) if isinstance(d, dict)]
    assertions_ok = _architecture_assertions_hold(
        payload, case.get("expected_assertions") or {}, boundaries, decisions
    )
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=_expected_keys_from_case(case),
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _all_findings_have_recommendations(findings: list[dict[str, Any]]) -> bool:
    return all(bool(_stripped_str(finding, "recommendation")) for finding in findings)


def _review_assertions_hold(
    payload: dict[str, Any], assertions: Any, findings: list[dict[str, Any]]
) -> bool:
    if not isinstance(assertions, dict):
        return True
    if "verdict" in assertions and payload.get("verdict") != assertions["verdict"]:
        return False
    expected_files = assertions.get("files")
    if isinstance(expected_files, list):
        reported_files = {f.get("file") for f in findings}
        if not set(expected_files).issubset(reported_files):
            return False
    if assertions.get("require_recommendations"):
        return _all_findings_have_recommendations(findings)
    return True


def _grade_review(payload: dict[str, Any], case: dict[str, Any]) -> _GradeOutcome:
    records = _project_review_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True
    )
    findings = [f for f in payload.get("findings", []) if isinstance(f, dict)]
    assertions_ok = _review_assertions_hold(
        payload, case.get("expected_assertions") or {}, findings
    )
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=_expected_keys_from_case(case),
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _is_changed_regular_file(
    rel: Any, changed_paths: set[str], source_dir: Path
) -> bool:
    if not isinstance(rel, str) or rel not in changed_paths:
        return False
    return (source_dir / rel).is_file()


def _glob_matches_changed_file(
    pattern: Any, changed_paths: set[str], source_dir: Path
) -> bool:
    if not isinstance(pattern, str):
        return False
    return any(
        _matches_writable_glob(rel, pattern) and (source_dir / rel).is_file()
        for rel in changed_paths
    )


def _check_generated_paths(
    assertions: Mapping[str, Any], changed_paths: set[str], source_dir: Path
) -> bool:
    gen_files = assertions.get("generated_files")
    files_ok = not isinstance(gen_files, list) or all(
        _is_changed_regular_file(rel, changed_paths, source_dir) for rel in gen_files
    )
    gen_globs = assertions.get("generated_globs")
    globs_ok = not isinstance(gen_globs, list) or all(
        _glob_matches_changed_file(pat, changed_paths, source_dir) for pat in gen_globs
    )
    return files_ok and globs_ok


def _testing_assertions_hold(
    payload: dict[str, Any],
    assertions: Any,
    test_cases: list[dict[str, Any]],
    changed_paths: set[str],
    source_dir: Path,
) -> bool:
    if not isinstance(assertions, dict):
        return True
    for field_name in ("component", "framework"):
        if (
            field_name in assertions
            and payload.get(field_name) != assertions[field_name]
        ):
            return False
    if "verified" in assertions and any(
        tc.get("verified") is not assertions["verified"] for tc in test_cases
    ):
        return False
    return _check_generated_paths(assertions, changed_paths, source_dir)


def _grade_testing(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_testing_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True
    )
    test_cases = [tc for tc in payload.get("test_cases", []) if isinstance(tc, dict)]
    assertions_ok = _testing_assertions_hold(
        payload,
        case.get("expected_assertions") or {},
        test_cases,
        changed_paths,
        source_dir,
    )
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=_expected_keys_from_case(case),
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _spec_assertions_hold(
    payload: dict[str, Any],
    assertions: Any,
    changed_paths: set[str],
    source_dir: Path,
) -> bool:
    if not isinstance(assertions, dict):
        return True
    if "status" in assertions and payload.get("status") != assertions["status"]:
        return False
    if "new_file_scopes" in assertions and payload.get("new_file_scopes") != list(
        assertions["new_file_scopes"]
    ):
        return False
    return _check_generated_paths(assertions, changed_paths, source_dir)


def _grade_spec(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_spec_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True, cap_uncertain_at_one=True
    )
    assertions_ok = _spec_assertions_hold(
        payload, case.get("expected_assertions") or {}, changed_paths, source_dir
    )
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=_expected_keys_from_case(case),
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _non_noop_tasks(raw_tasks: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tasks, list):
        return []
    result: list[dict[str, Any]] = []
    for task in raw_tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("task_id")
        if isinstance(tid, str) and tid != "noop":
            result.append(task)
    return result


def _task_dependencies(task: dict[str, Any]) -> list[str]:
    deps: list[str] = []
    for dep_key in ("blocked_by", "dependencies"):
        raw_deps = task.get(dep_key)
        if isinstance(raw_deps, list):
            deps.extend(str(d) for d in raw_deps)
    return deps


def _build_task_graph(tasks: list[dict[str, Any]]) -> dict[str, list[str]] | None:
    active_tasks = _non_noop_tasks(tasks)
    known = {str(t["task_id"]) for t in active_tasks}
    graph: dict[str, list[str]] = {}
    for task in active_tasks:
        deps = _task_dependencies(task)
        if any(dep not in known for dep in deps):
            return None
        graph[str(task["task_id"])] = deps
    return graph


def _dfs_acyclic(node: str, graph: dict[str, list[str]], state: dict[str, int]) -> bool:
    mark = state.get(node, 0)
    if mark == _DFS_VISITING:
        return False
    if mark == _DFS_VISITED:
        return True
    state[node] = _DFS_VISITING
    for dep in graph.get(node, []):
        if not _dfs_acyclic(dep, graph, state):
            return False
    state[node] = _DFS_VISITED
    return True


def _tasks_are_acyclic(tasks: list[dict[str, Any]]) -> bool:
    graph = _build_task_graph(tasks)
    if graph is None:
        return False
    state: dict[str, int] = {}
    return all(_dfs_acyclic(tid, graph, state) for tid in graph)


def _task_precedes_dependents(
    task: dict[str, Any], order_index: dict[str, int]
) -> bool:
    tid = str(task["task_id"])
    if tid not in order_index:
        return False
    task_idx = order_index[tid]
    for dep_id in _task_dependencies(task):
        if dep_id not in order_index or order_index[dep_id] >= task_idx:
            return False
    return True


def _dependency_order_matches_scopes(
    payload: dict[str, Any], expected_scopes: list[Any]
) -> bool:
    raw_tasks = _non_noop_tasks(payload.get("task_breakdowns", []))
    dep_order = payload.get("dependency_order")
    if not isinstance(dep_order, list) or len(dep_order) != len(raw_tasks):
        return False
    order_index = {str(tid): idx for idx, tid in enumerate(dep_order)}
    if len(order_index) != len(raw_tasks):
        return False
    if not all(_task_precedes_dependents(task, order_index) for task in raw_tasks):
        return False
    scopes_by_tid = {
        str(task["task_id"]): list(task.get("file_scopes", [])) for task in raw_tasks
    }
    ordered_actual = [scopes_by_tid[str(tid)] for tid in dep_order]
    return ordered_actual == expected_scopes


def _workflow_uncertain_count(payload: dict[str, Any], base_uncertain: int) -> int:
    if "reproduction_status" in payload:
        if payload.get("reproduction_status") == "needs-validation":
            return max(base_uncertain, 1)
        return base_uncertain
    if "remediation_state_transitions" in payload:
        ambiguous = sum(
            isinstance(m, dict) and m.get("match_status") == "ambiguous"
            for m in payload.get("finding_fingerprint_matches", [])
        )
        return base_uncertain + ambiguous
    if "dead_ends" in payload and "verified_facts" in payload:
        return len(payload.get("assumptions", []))
    return base_uncertain


def _workflow_direct_fields_match(
    payload: dict[str, Any], assertions: Mapping[str, Any]
) -> bool:
    return all(
        field_name not in assertions
        or payload.get(field_name) == assertions[field_name]
        for field_name in _WORKFLOW_DIRECT_ASSERTION_FIELDS
    )


def _workflow_handoff_assertions_match(
    payload: dict[str, Any], assertions: Mapping[str, Any]
) -> bool:
    if "touched_files" in assertions and list(payload.get("touched_files", [])) != list(
        assertions["touched_files"]
    ):
        return False
    if "repository_dirty" in assertions:
        repo_obj = payload.get("repository")
        dirty_val = repo_obj.get("dirty") if isinstance(repo_obj, dict) else None
        if dirty_val is not assertions["repository_dirty"]:
            return False
    if "abstained" in assertions:
        is_abstained = _stripped_str(payload, "next_action").lower() == "abstained"
        if is_abstained is not bool(assertions["abstained"]):
            return False
    return True


def _workflow_counts_and_scopes_match(
    payload: dict[str, Any], assertions: Mapping[str, Any]
) -> bool:
    expected_scopes = assertions.get("ordered_file_scopes")
    if isinstance(expected_scopes, list) and not _dependency_order_matches_scopes(
        payload, expected_scopes
    ):
        return False
    if "dead_end_count" in assertions and len(payload.get("dead_ends", [])) != int(
        assertions["dead_end_count"]
    ):
        return False
    if "max_subject_length" in assertions and len(
        str(payload.get("commit_subject", ""))
    ) > int(assertions["max_subject_length"]):
        return False
    return "unmapped_count" not in assertions or len(
        payload.get("unmapped_records", [])
    ) == int(assertions["unmapped_count"])


def _safe_controls_assertion_holds(
    payload: dict[str, Any], assertions: Mapping[str, Any]
) -> bool:
    if "safe_controls_all_passed" not in assertions:
        return True
    outcomes = payload.get("safe_control_outcomes", [])
    all_passed = bool(outcomes) and all(
        isinstance(c, dict) and c.get("passed") is True for c in outcomes
    )
    return all_passed is assertions["safe_controls_all_passed"]


def _changed_files_assertion_holds(
    payload: dict[str, Any], assertions: Mapping[str, Any], changed_paths: set[str]
) -> bool:
    raw_expected = assertions.get("changed_files")
    if not isinstance(raw_expected, list):
        return True
    expected_changed = set(raw_expected)
    payload_changed = {
        _extract_file_change_path(item) for item in payload.get("file_changes", [])
    } - {"", "none"}
    return changed_paths == expected_changed and payload_changed == expected_changed


def _is_passing_execution_result(er: Any) -> bool:
    if not isinstance(er, dict):
        return False
    return er.get("passed") is True and er.get("exit_code") == 0


def _executions_and_dag_assertions_hold(
    payload: dict[str, Any], assertions: Mapping[str, Any]
) -> bool:
    if assertions.get("all_executions_passed"):
        exec_results = payload.get("execution_results", [])
        if not exec_results or not all(
            _is_passing_execution_result(er) for er in exec_results
        ):
            return False
    if "acyclic" in assertions:
        raw_tasks = [
            t for t in payload.get("task_breakdowns", []) if isinstance(t, dict)
        ]
        if _tasks_are_acyclic(raw_tasks) is not assertions["acyclic"]:
            return False
    return True


def _workflow_assertions_hold(
    payload: dict[str, Any],
    assertions: Any,
    changed_paths: set[str],
    source_dir: Path,
) -> bool:
    if not isinstance(assertions, dict):
        return True
    payload_checks = (
        _workflow_direct_fields_match,
        _workflow_handoff_assertions_match,
        _workflow_counts_and_scopes_match,
        _safe_controls_assertion_holds,
        _executions_and_dag_assertions_hold,
    )
    if not all(check(payload, assertions) for check in payload_checks):
        return False
    if not _changed_files_assertion_holds(payload, assertions, changed_paths):
        return False
    return _check_generated_paths(assertions, changed_paths, source_dir)


def _grade_workflow(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_workflow_records(payload, case)
    reported, grounded, base_uncertain = _score_normalized_records(records)
    uncertain = _workflow_uncertain_count(payload, base_uncertain)
    assertions_ok = _workflow_assertions_hold(
        payload, case.get("expected_assertions") or {}, changed_paths, source_dir
    )
    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=_expected_keys_from_case(case),
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


_STATELESS_SUITE_GRADERS: dict[
    str, Callable[[dict[str, Any], dict[str, Any]], _GradeOutcome]
] = {
    "audit": _grade_audit,
    "architecture": _grade_architecture,
    "review": _grade_review,
}

_SOURCE_SUITE_GRADERS: dict[
    str,
    Callable[[dict[str, Any], dict[str, Any], set[str], Path], _GradeOutcome],
] = {
    "testing": _grade_testing,
    "spec": _grade_spec,
    "workflow": _grade_workflow,
}


def _dispatch_suite_grader(
    contract: str,
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    source_grader = _SOURCE_SUITE_GRADERS.get(contract)
    if source_grader is not None:
        return source_grader(payload, case, changed_paths, source_dir)
    stateless_grader = _STATELESS_SUITE_GRADERS.get(contract, _grade_audit)
    return stateless_grader(payload, case)


def _extract_tokens(usage: Any) -> int | None:
    tokens = usage.get("tokens") if isinstance(usage, dict) else None
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        return None
    return tokens


def _mean_or_none(values: Sequence[float | int]) -> float | None:
    return statistics.mean(values) if values else None


def _summarize_configuration_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    token_values = [row["tokens"] for row in rows if row["tokens"] is not None]
    exact_rate = sum(row["exact_match"] for row in rows) / len(rows) if rows else None
    return {
        "runs": len(rows),
        "exact_match_rate": exact_rate,
        "mean_seconds": _mean_or_none([row["seconds"] for row in rows]),
        "failures": sum(row["state"] != "executed" for row in rows),
        "tokens_reported_runs": len(token_values),
        "total_tokens": sum(token_values) if token_values else None,
        "mean_tokens": _mean_or_none(token_values),
    }


def _index_runs_by_case_repeat(
    results: list[dict[str, Any]], configuration: str
) -> dict[tuple[Any, Any], dict[str, Any]]:
    return {
        (row["case"], row["repeat"]): row
        for row in results
        if row["configuration"] == configuration
    }


def _paired_token_deltas(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[int]:
    deltas: list[int] = []
    for base, skill in pairs:
        b_tok = base["tokens"]
        s_tok = skill["tokens"]
        if b_tok is not None and s_tok is not None:
            deltas.append(s_tok - b_tok)
    return deltas


def _compute_skill_lift(results: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_by_key = _index_runs_by_case_repeat(results, "without_skill")
    assisted_by_key = _index_runs_by_case_repeat(results, "with_skill")
    pairs = [
        (baseline_by_key[key], assisted_by_key[key])
        for key in baseline_by_key
        if key in assisted_by_key
    ]
    token_deltas = _paired_token_deltas(pairs)
    return {
        "paired_runs": len(pairs),
        "exact_match_rate_delta": _mean_or_none(
            [float(s["exact_match"]) - float(b["exact_match"]) for b, s in pairs]
        ),
        "mean_seconds_delta": _mean_or_none(
            [s["seconds"] - b["seconds"] for b, s in pairs]
        ),
        "failures_delta": sum(
            int(s["state"] != "executed") - int(b["state"] != "executed")
            for b, s in pairs
        ),
        "true_positives_delta": sum(
            s["true_positives"] - b["true_positives"] for b, s in pairs
        ),
        "false_positives_delta": sum(
            s["false_positives"] - b["false_positives"] for b, s in pairs
        ),
        "false_negatives_delta": sum(
            s["false_negatives"] - b["false_negatives"] for b, s in pairs
        ),
        "paired_token_runs": len(token_deltas),
        "total_tokens_delta": sum(token_deltas) if token_deltas else None,
        "mean_tokens_delta": _mean_or_none(token_deltas),
    }


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for configuration in ("without_skill", "with_skill"):
        rows = [row for row in results if row["configuration"] == configuration]
        summary[configuration] = _summarize_configuration_runs(rows)
    summary["skill_lift"] = _compute_skill_lift(results)
    return summary


def _is_bounded_int(value: Any, low: int, high: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _is_valid_max_tokens(max_tokens: int | None) -> bool:
    if max_tokens is None:
        return True
    return (
        isinstance(max_tokens, int)
        and not isinstance(max_tokens, bool)
        and max_tokens >= 1
    )


def _validate_evaluate_args(
    command: list[str] | None,
    repeats: int,
    timeout: int,
    max_tokens: int | None,
) -> list[str]:
    valid = (
        bool(command)
        and _is_bounded_int(repeats, 1, _MAX_REPEATS)
        and _is_bounded_int(timeout, 1, _MAX_TIMEOUT_SECONDS)
        and _is_valid_max_tokens(max_tokens)
    )
    if not valid or command is None:
        raise ValueError(
            "Evaluation requires an explicit JSON argv adapter, 1–10 repeats, a bounded timeout, and a positive max_tokens limit when set"
        )
    return command


def _prepare_output_dir(output: Path) -> Path:
    resolved = output.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError("Choose a new or empty evaluation output directory")
    return resolved


def _select_evaluation_cases(
    manifest: Mapping[str, Any], cases: list[str] | None
) -> list[dict[str, Any]]:
    selected = [case for case in manifest["cases"] if not cases or case["id"] in cases]
    if cases and set(cases) - {case["id"] for case in selected}:
        raise ValueError("Unknown evaluation case ID")
    return selected


def _stage_case_workspace(
    run_ctx: _RunContext,
    case: dict[str, Any],
    configuration: str,
    repeat: int,
) -> tuple[Path, Path, Path, dict[str, str]]:
    ignore_bytecode = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    work = run_ctx.output / case["id"] / configuration / str(repeat + 1)
    work.mkdir(parents=True)
    fixture = run_ctx.root / "evals" / relative_path(case["fixture"])
    source_dir = work / "source"
    shutil.copytree(fixture, source_dir, ignore=ignore_bytecode)
    if configuration == "with_skill":
        skill = run_ctx.root / "skills" / relative_path(case["skill"])
        shutil.copytree(skill, work / "skill", ignore=ignore_bytecode)
    request = _build_prompt_request(case, configuration)
    prompt = work / "prompt.md"
    prompt.write_text(request + "\n", encoding="utf-8")
    return work, source_dir, prompt, package_files(source_dir)


def _build_adapter_argv(command: list[str], prompt: Path, work: Path) -> list[str]:
    substitutions = {
        "{prompt_file}": str(prompt),
        "{output_file}": str(work / "result.json"),
        "{case_dir}": str(work),
    }
    argv: list[str] = []
    for argument in command:
        rendered = argument
        for key, value in substitutions.items():
            rendered = rendered.replace(key, value)
        argv.append(rendered)
    return argv


def _terminate_timed_out_process(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform != "win32":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    else:
        proc.kill()
    proc.wait()


def _run_adapter_subprocess(argv: list[str], work: Path, timeout: int) -> int:
    # Stream logs to disk rather than retaining arbitrary adapter output in memory.
    with (
        (work / "stdout.log").open("wb") as stdout,
        (work / "stderr.log").open("wb") as stderr,
    ):
        proc = subprocess.Popen(
            argv,
            cwd=work,
            stdout=stdout,
            stderr=stderr,
            start_new_session=sys.platform != "win32",
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_timed_out_process(proc)
            raise
    return int(proc.returncode or 0)


def _load_validated_adapter_output(
    result_file: Path, contract: str, schema_path: Path
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    if result_file.stat().st_size > _MAX_RESULT_BYTES:
        raise ValueError("Adapter result exceeds the 2 MB limit")
    raw_result = read_json(result_file)
    payload, actor, usage = _unwrap_adapter_result(raw_result)
    _validate_contract_payload(payload, contract, schema_path)
    return payload, actor, usage


def _execute_case_adapter(
    argv: list[str], work: Path, timeout: int, case_ctx: _CaseContext
) -> _AdapterExecution:
    exec_res = _AdapterExecution()
    result_file = work / "result.json"
    try:
        returncode = _run_adapter_subprocess(argv, work, timeout)
        if returncode:
            exec_res.state = "failed"
            exec_res.error = f"Adapter exited {returncode}"
        elif result_file.exists():
            payload, actor, usage = _load_validated_adapter_output(
                result_file, case_ctx.contract, case_ctx.schema_path
            )
            exec_res.payload = payload
            exec_res.actor = actor
            exec_res.usage = usage
        else:
            exec_res.state = "failed"
            exec_res.error = "Adapter did not write result.json"
    except subprocess.TimeoutExpired:
        exec_res.state = "timeout"
        exec_res.error = "Adapter exceeded its time budget"
    except (OSError, ValueError, TypeError) as exc:
        exec_res.state = "failed"
        exec_res.error = str(exc)
    return exec_res


def _grade_executed_run(
    exec_res: _AdapterExecution,
    case_ctx: _CaseContext,
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    if exec_res.payload is None or exec_res.state != "executed":
        return _GradeOutcome(
            reported=set(),
            grounded=set(),
            expected=_expected_keys_from_case(case_ctx.case),
            uncertain_count=0,
            assertions_ok=False,
        )
    outcome = _dispatch_suite_grader(
        case_ctx.contract,
        exec_res.payload,
        case_ctx.case,
        changed_paths,
        source_dir,
    )
    if outcome.malformed_error is not None:
        exec_res.state = "failed"
        exec_res.error = outcome.malformed_error
    return outcome


def _enforce_token_budget(
    exec_res: _AdapterExecution, max_tokens: int | None
) -> int | None:
    tokens = _extract_tokens(exec_res.usage)
    if max_tokens is None or tokens is None or exec_res.state != "executed":
        return tokens
    if tokens > max_tokens:
        exec_res.state = "failed"
        exec_res.error = (
            f"Adapter token usage ({tokens}) exceeded max_tokens ({max_tokens})"
        )
    return tokens


def _is_exact_match_outcome(state: str, outcome: _GradeOutcome) -> bool:
    if state != "executed" or not outcome.assertions_ok:
        return False
    return outcome.reported == outcome.expected and outcome.reported <= outcome.grounded


def _evaluate_single_run(
    run_ctx: _RunContext,
    case_ctx: _CaseContext,
    configuration: str,
    repeat: int,
) -> dict[str, Any]:
    work, source_dir, prompt, inputs_before = _stage_case_workspace(
        run_ctx, case_ctx.case, configuration, repeat
    )
    argv = _build_adapter_argv(run_ctx.command, prompt, work)
    start = time.monotonic()
    exec_res = _execute_case_adapter(argv, work, run_ctx.timeout, case_ctx)

    inputs_changed, changed_paths, mutation_error = _evaluate_source_changes(
        inputs_before, source_dir, case_ctx.writable_paths
    )
    if mutation_error is not None:
        exec_res.state = "failed"
        exec_res.error = mutation_error

    # Grading happens only after adapter execution, outside its workdir.
    outcome = _grade_executed_run(exec_res, case_ctx, changed_paths, source_dir)
    tokens = _enforce_token_budget(exec_res, run_ctx.max_tokens)

    reported = outcome.reported
    grounded = outcome.grounded
    expected = outcome.expected
    record = {
        "case": case_ctx.case["id"],
        "configuration": configuration,
        "repeat": repeat + 1,
        "state": exec_res.state,
        "error": exec_res.error,
        "seconds": round(time.monotonic() - start, 4),
        "inputs_changed": inputs_changed,
        "true_positives": len(reported & expected),
        "false_positives": len(reported - expected),
        "false_negatives": len(expected - reported),
        "unsubstantiated_claims": len(reported - grounded),
        "uncertain_count": outcome.uncertain_count,
        "normalized_records": outcome.normalized_records,
        "actor": exec_res.actor,
        "exact_match": _is_exact_match_outcome(exec_res.state, outcome),
        "tokens": tokens,
        "result_path": (work / "result.json").relative_to(run_ctx.output).as_posix(),
    }
    atomic_json(work / "grading.json", record)
    return record


def _build_case_context(
    case: dict[str, Any], suite_map: dict[str, str], root: Path
) -> _CaseContext:
    contract = _resolve_case_contract(case, suite_map)
    schema_path = _resolve_case_schema(case, contract, root)
    return _CaseContext(
        case=case,
        contract=contract,
        schema_path=schema_path,
        writable_paths=case.get("writable_paths"),
    )


def _run_case_configurations(
    run_ctx: _RunContext, case_ctx: _CaseContext, repeats: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for configuration in ("without_skill", "with_skill"):
        for repeat in range(repeats):
            rows.append(_evaluate_single_run(run_ctx, case_ctx, configuration, repeat))
    return rows


def _run_selected_cases(
    run_ctx: _RunContext, selected: list[dict[str, Any]], repeats: int
) -> list[dict[str, Any]]:
    suite_map = _skill_to_suite_map(run_ctx.root)
    results: list[dict[str, Any]] = []
    for case in selected:
        case_ctx = _build_case_context(case, suite_map, run_ctx.root)
        results.extend(_run_case_configurations(run_ctx, case_ctx, repeats))
    return results


def evaluate(
    output: Path,
    command: list[str] | None = None,
    cases: list[str] | None = None,
    repeats: int = 1,
    timeout: int = 120,
    root: Path = ROOT,
    max_tokens: int | None = None,
) -> dict:
    """Run a user-supplied adapter without a shell; never pass grader answers to it."""
    valid_command = _validate_evaluate_args(command, repeats, timeout, max_tokens)
    resolved_output = _prepare_output_dir(output)
    manifest = read_json(root / "evals/cases.json")
    selected = _select_evaluation_cases(manifest, cases)
    run_ctx = _RunContext(
        output=resolved_output,
        command=valid_command,
        timeout=timeout,
        root=root,
        max_tokens=max_tokens,
    )
    results = _run_selected_cases(run_ctx, selected, repeats)
    benchmark = {
        "schema_version": "1.0",
        "toolkit_version": version(root),
        "adapter_argv": valid_command,
        "cases_digest": digest(manifest),
        "runs": results,
        "summary": _build_summary(results),
        "limitations": list(_BENCHMARK_LIMITATIONS),
    }
    atomic_json(resolved_output / "benchmark.json", benchmark)
    return benchmark

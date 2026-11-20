from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

from .common import (
    ROOT,
    SUITE_DELIVERABLES,
    WORKFLOW_DELIVERABLE_SCHEMAS,
    WORKFLOW_PHASE_SCHEMAS,
    WORKFLOW_SKILL_DELIVERABLES,
    _infer_suite_name,
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

_CONTRACT_VALIDATOR_SCRIPTS: dict[str, tuple[str, str]] = {
    "audit": ("fred_audit_validator", "scripts/validate_audit.py"),
    "architecture": (
        "fred_architecture_validator",
        "scripts/validate_architecture.py",
    ),
    "review": ("fred_review_validator", "scripts/validate_review.py"),
    "testing": ("fred_testing_validator", "scripts/validate_testing.py"),
    "spec": ("fred_spec_validator", "scripts/validate_spec.py"),
    "workflow": ("fred_workflow_validator", "scripts/validate_workflow.py"),
}


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


def _load_suite_validator(contract: str) -> _SuiteValidator:
    entry = _CONTRACT_VALIDATOR_SCRIPTS.get(contract)
    if entry is None:
        raise ValueError(f"Unsupported evaluation contract: {contract!r}")
    module_name, script_rel = entry
    cached = sys.modules.get(module_name)
    if isinstance(cached, ModuleType) and callable(getattr(cached, "validate", None)):
        return cast(_SuiteValidator, cached)
    script = ROOT / script_rel
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to load suite validator from {script}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.modules[spec.name] = module
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    finally:
        sys.dont_write_bytecode = previous
    return cast(_SuiteValidator, module)


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
    deliverables = WORKFLOW_SKILL_DELIVERABLES.get(skill_name)
    companions = _load_workflow_companions(root)
    if deliverables:
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


def _resolve_case_schema(case: dict[str, Any], contract: str, root: Path) -> Path:
    explicit = case.get("schema")
    if explicit is not None:
        if not isinstance(explicit, str):
            raise ValueError("Evaluation case schema must be a relative path string")
        schema_path = root / relative_path(explicit)
        if schema_path.is_symlink() or not schema_path.is_file():
            raise ValueError(f"Missing evaluation schema: {explicit}")
        return schema_path

    skill_name = str(case.get("skill", ""))
    skill_dir = root / "skills" / relative_path(skill_name)

    if contract != "workflow":
        suite_name = _CONTRACT_TO_SUITE[contract]
        suite_contracts = _load_suite_contracts(root)
        _, default_schema = SUITE_DELIVERABLES[suite_name]
        schema_rel = str(
            suite_contracts.get(suite_name, {}).get("schema", default_schema)
        )
        canonical = root / relative_path(schema_rel)
        if canonical.is_file() and not canonical.is_symlink():
            return canonical
        local_copy = skill_dir / "assets" / Path(schema_rel).name
        if local_copy.is_file() and not local_copy.is_symlink():
            return local_copy
        raise ValueError(f"Missing output schema for skill: {skill_name}")

    wf_schema_rel = _resolve_workflow_phase_schema_rel(skill_name, root)
    if wf_schema_rel is not None:
        wf_canonical = root / relative_path(wf_schema_rel)
        if wf_canonical.is_file() and not wf_canonical.is_symlink():
            return wf_canonical
        wf_local = skill_dir / "assets" / Path(wf_schema_rel).name
        if wf_local.is_file() and not wf_local.is_symlink():
            return wf_local

    exact_skill_schema = skill_dir / "assets" / f"{skill_name}.schema.json"
    if exact_skill_schema.is_file() and not exact_skill_schema.is_symlink():
        return exact_skill_schema

    candidates = sorted(
        p
        for p in (skill_dir / "assets").glob("*.schema.json")
        if not p.name.startswith("evaluation-scenarios") and not p.is_symlink()
    )
    if not candidates:
        raise ValueError(f"Missing output schema for skill: {skill_name}")
    return candidates[0]


def _build_prompt_request(
    case: dict[str, Any],
    configuration: str,
) -> str:
    lines = [
        str(case["prompt"]).strip(),
        "",
        "Inspect ./source and write your structured JSON deliverable to result.json.",
    ]
    if configuration == "with_skill":
        lines.append(
            "A skill is available at ./skill/SKILL.md. Use it only if it applies to the user's request."
        )
        lines.append(
            "Inspect only this case's source and optional skill; do not inspect grading data, parent workspaces, or other cases."
        )
    else:
        lines.append(
            "Inspect only this case's source; do not inspect grading data, parent workspaces, or other cases."
        )
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


def _normalize_writable_pattern(pattern: str) -> str:
    if not isinstance(pattern, str) or not pattern.strip() or "\\" in pattern:
        raise ValueError(f"Invalid writable_paths glob pattern: {pattern!r}")
    cleaned = pattern.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if (
        not cleaned
        or cleaned.startswith("/")
        or any(part in {"..", ".git"} for part in cleaned.split("/"))
    ):
        raise ValueError(f"Unsafe writable_paths glob pattern: {pattern!r}")
    return cleaned


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    norm = _normalize_writable_pattern(pattern)
    parts: list[str] = ["^"]
    idx = 0
    length = len(norm)
    while idx < length:
        if norm[idx : idx + 3] == "**/":
            parts.append("(?:.+/)?")
            idx += 3
            continue
        if norm[idx : idx + 2] == "**":
            parts.append(".*")
            idx += 2
            continue
        ch = norm[idx]
        if ch == "*":
            parts.append("[^/]*")
        elif ch == "?":
            parts.append("[^/]")
        elif ch == "[":
            end = norm.find("]", idx + 1)
            if end == -1:
                parts.append(re.escape(ch))
            else:
                inner = norm[idx + 1 : end]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                idx = end
        else:
            parts.append(re.escape(ch))
        idx += 1
    parts.append(r"\Z")
    return re.compile("".join(parts))


def _matches_writable_glob(rel_posix: str, pattern: str) -> bool:
    return bool(_glob_to_regex(pattern).match(rel_posix))


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
        rel
        for rel in changed
        if not any(_matches_writable_glob(rel, pat) for pat in patterns)
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


def _value_matches_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return actual in expected
    return bool(actual == expected)


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
    return sorted(
        {
            (str(evidence_map[eid]["path"]), str(evidence_map[eid]["symbol"]))
            for eid in raw_ids
            if isinstance(eid, str) and eid in evidence_map
        }
    )


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
    if "locations" in anchor:
        raw_locs = anchor["locations"]
        if not isinstance(raw_locs, list):
            return False
        expected_locs = sorted(
            {
                (str(pair[0]), str(pair[1]))
                for pair in raw_locs
                if isinstance(pair, list | tuple) and len(pair) == 2
            }
        )
        if anchor.get("locations_subset"):
            if not set(expected_locs).issubset(set(locations)):
                return False
        elif locations != expected_locs:
            return False
    return True


def _project_audit_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    items = payload.get("findings")
    raw_evidence = payload.get("evidence")
    raw_contracts = payload.get("contracts")
    if (
        not isinstance(items, list)
        or not isinstance(raw_evidence, list)
        or not isinstance(raw_contracts, list)
    ):
        raise TypeError("Malformed audit ledger response")

    evidence_map: dict[str, dict[str, Any]] = {}
    for ev in raw_evidence:
        if (
            not isinstance(ev, dict)
            or not isinstance(ev.get("id"), str)
            or not isinstance(ev.get("path"), str)
            or not isinstance(ev.get("symbol"), str)
        ):
            raise TypeError("Malformed audit evidence record")
        evidence_map[ev["id"]] = ev

    known_contracts = {
        str(c["id"])
        for c in raw_contracts
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    }

    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    seen_fingerprints: dict[str, int] = {}
    records: list[dict[str, str]] = []

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise TypeError("Malformed audit finding record")
        if "key" in item or "category" in item:
            raise ValueError("Audit findings must not include non-schema key/category")
        raw_status = item.get("status")
        if not isinstance(raw_status, str) or raw_status not in _AUDIT_STATUSES:
            raise ValueError("Malformed audit finding status")
        topic = item.get("topic")
        root_cause = item.get("root_cause")
        fid = item.get("id")
        if (
            not isinstance(topic, str)
            or not topic.strip()
            or not isinstance(root_cause, str)
            or not root_cause.strip()
            or not isinstance(fid, str)
            or not fid.strip()
        ):
            raise ValueError("Malformed audit finding identity")

        locations = _audit_finding_locations(item, evidence_map)
        fp = digest(
            {
                "topic": topic,
                "root_cause": root_cause,
                "locations": locations,
            }
        )
        seen_count = seen_fingerprints.get(fp, 0) + 1
        seen_fingerprints[fp] = seen_count

        matched_key: str | None = None
        if use_anchors and raw_status in _ACTIONABLE_AUDIT_STATUSES:
            matched_key = _claim_anchor(
                anchors,
                claimed,
                _audit_anchor_matches,
                item,
                locations,
            )
        if matched_key is not None:
            record_key = matched_key
        elif not use_anchors:
            base_key = root_cause if seen_count == 1 else f"{root_cause}:{seen_count}"
            record_key = base_key
        else:
            record_key = f"audit:{idx + 1}:{fp[:12]}"

        ev_ids = item.get("evidence_ids")
        contract_ids = item.get("contract_ids")
        resolved_ev = (
            [
                evidence_map[eid]
                for eid in ev_ids
                if isinstance(eid, str) and eid in evidence_map
            ]
            if isinstance(ev_ids, list)
            else []
        )
        contracts_ok = (
            isinstance(contract_ids, list)
            and bool(contract_ids)
            and all(
                isinstance(cid, str) and cid in known_contracts for cid in contract_ids
            )
        )
        ev_snippets = [
            f"{ev['path']}:{ev['symbol']} ({str(ev.get('context', '')).strip()})"
            for ev in resolved_ev
            if str(ev.get("path", "")).strip() and str(ev.get("context", "")).strip()
        ]
        if raw_status in _ACTIONABLE_AUDIT_STATUSES:
            evidence_text = (
                "; ".join(ev_snippets) if (ev_snippets and contracts_ok) else ""
            )
        else:
            fallback_note = str(
                item.get("counterevidence") or item.get("actual") or ""
            ).strip()
            evidence_text = "; ".join(ev_snippets) or fallback_note
        records.append(_make_normalized_record(record_key, raw_status, evidence_text))
    return records


def _architecture_boundary_matches(
    boundary: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") not in {None, "boundary"}:
        return False
    if "type" in anchor and not _value_matches_expected(
        boundary.get("type"), anchor["type"]
    ):
        return False
    if "min_invariants" in anchor:
        invs = boundary.get("invariants")
        count = len(invs) if isinstance(invs, list) else 0
        if count < int(anchor["min_invariants"]):
            return False
    if "paths" in anchor and isinstance(anchor["paths"], list):
        b_paths = boundary.get("paths")
        actual_paths = {str(p) for p in b_paths} if isinstance(b_paths, list) else set()
        if not {str(p) for p in anchor["paths"]}.issubset(actual_paths):
            return False
    return True


def _architecture_decision_matches(
    decision: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") != "decision":
        return False
    if "min_consequences" in anchor:
        cons = decision.get("consequences")
        count = len(cons) if isinstance(cons, list) else 0
        if count < int(anchor["min_consequences"]):
            return False
    if "paths" in anchor and isinstance(anchor["paths"], list):
        d_paths = decision.get("paths")
        actual_paths = {str(p) for p in d_paths} if isinstance(d_paths, list) else set()
        if not {str(p) for p in anchor["paths"]}.issubset(actual_paths):
            return False
    return True


def _project_architecture_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    doc_status = payload.get("status")
    record_status = "needs-validation" if doc_status == "draft" else "supported"
    decision_status = (
        "confirmed"
        if doc_status == "accepted"
        else ("needs-validation" if doc_status == "draft" else "supported")
    )
    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    records: list[dict[str, str]] = []

    for idx, boundary in enumerate(payload.get("boundaries", [])):
        if not isinstance(boundary, dict) or not isinstance(boundary.get("name"), str):
            continue
        desc = (
            boundary["description"].strip()
            if isinstance(boundary.get("description"), str)
            else ""
        )
        inv_parts = (
            [
                inv.strip()
                for inv in boundary["invariants"]
                if isinstance(inv, str) and inv.strip()
            ]
            if isinstance(boundary.get("invariants"), list)
            else []
        )
        evidence = desc or "; ".join(inv_parts)
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _architecture_boundary_matches,
                boundary,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"boundary:{idx + 1}:{boundary['name']}"
                if use_anchors
                else str(boundary["name"])
            )
        )
        records.append(_make_normalized_record(key, record_status, evidence))

    for idx, decision in enumerate(payload.get("decisions", [])):
        if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
            continue
        dec_text = (
            decision["decision"].strip()
            if isinstance(decision.get("decision"), str)
            else ""
        )
        ctx_text = (
            decision["context"].strip()
            if isinstance(decision.get("context"), str)
            else ""
        )
        cons_parts = (
            [
                c.strip()
                for c in decision["consequences"]
                if isinstance(c, str) and c.strip()
            ]
            if isinstance(decision.get("consequences"), list)
            else []
        )
        rationale = ctx_text or "; ".join(cons_parts)
        evidence = f"{dec_text} ({rationale})" if (dec_text and rationale) else ""
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _architecture_decision_matches,
                decision,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"decision:{idx + 1}:{decision['id']}"
                if use_anchors
                else str(decision["id"])
            )
        )
        records.append(_make_normalized_record(key, decision_status, evidence))
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


def _project_review_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    records: list[dict[str, str]] = []
    for idx, finding in enumerate(payload.get("findings", [])):
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            continue
        sev = finding.get("severity")
        status = (
            "needs-validation"
            if sev == "advisory"
            else "confirmed"
            if sev in {"critical", "high"}
            else "supported"
        )
        file_ref = (
            finding["file"].strip() if isinstance(finding.get("file"), str) else ""
        )
        desc = (
            finding["description"].strip()
            if isinstance(finding.get("description"), str)
            else ""
        )
        evidence = f"{file_ref}: {desc}" if (file_ref and desc) else ""
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _review_finding_matches,
                finding,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"review:{idx + 1}:{finding['id']}"
                if use_anchors
                else str(finding["id"])
            )
        )
        records.append(_make_normalized_record(key, status, evidence))
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


def _project_testing_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    records: list[dict[str, str]] = []
    valid_oracles = {"state", "exception", "output", "boundary"}
    for idx, tc in enumerate(payload.get("test_cases", [])):
        if not isinstance(tc, dict) or not isinstance(tc.get("id"), str):
            continue
        verified = tc.get("verified")
        status = (
            "confirmed"
            if verified is True
            else "needs-validation"
            if verified is False
            else "supported"
        )
        desc = (
            tc["description"].strip() if isinstance(tc.get("description"), str) else ""
        )
        oracle = tc.get("oracle_type")
        evidence = f"{oracle}: {desc}" if (desc and oracle in valid_oracles) else ""
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _testing_case_matches,
                tc,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (f"test:{idx + 1}:{tc['id']}" if use_anchors else str(tc["id"]))
        )
        records.append(_make_normalized_record(key, status, evidence))
    return records


def _spec_item_matches(kind: str, item: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if anchor.get("kind") != kind:
        return False
    if kind == "interview_decision":
        if "category" in anchor and not _value_matches_expected(
            item.get("category"), anchor["category"]
        ):
            return False
    elif kind == "requirement":
        if "priority" in anchor and not _value_matches_expected(
            item.get("priority"), anchor["priority"]
        ):
            return False
    elif kind == "convergence_check":
        if "status" in anchor and not _value_matches_expected(
            item.get("status"), anchor["status"]
        ):
            return False
        if "target" in anchor and not _value_matches_expected(
            item.get("target"), anchor["target"]
        ):
            return False
    return True


def _project_spec_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    doc_status = payload.get("status")
    in_progress = doc_status in {"draft", "interviewing"}
    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    records: list[dict[str, str]] = []

    for idx, decision in enumerate(payload.get("interview_decisions", [])):
        if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
            continue
        question = (
            decision["question"].strip()
            if isinstance(decision.get("question"), str)
            else ""
        )
        chosen = (
            decision["decision"].strip()
            if isinstance(decision.get("decision"), str)
            else ""
        )
        evidence = f"{question} -> {chosen}" if (question and chosen) else ""
        status = "needs-validation" if in_progress else "confirmed"
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _spec_item_matches,
                "interview_decision",
                decision,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"interview_decision:{idx + 1}:{decision['id']}"
                if use_anchors
                else str(decision["id"])
            )
        )
        records.append(_make_normalized_record(key, status, evidence))

    for idx, req in enumerate(payload.get("requirements", [])):
        if not isinstance(req, dict) or not isinstance(req.get("id"), str):
            continue
        desc = (
            req["description"].strip()
            if isinstance(req.get("description"), str)
            else ""
        )
        prio = req.get("priority")
        evidence = (
            f"[{prio}] {desc}"
            if (desc and isinstance(prio, str) and prio.strip())
            else desc
        )
        status = "needs-validation" if in_progress else "confirmed"
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _spec_item_matches,
                "requirement",
                req,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"requirement:{idx + 1}:{req['id']}" if use_anchors else str(req["id"])
            )
        )
        records.append(_make_normalized_record(key, status, evidence))

    for idx, check in enumerate(payload.get("convergence_checks", [])):
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            continue
        target = check["target"].strip() if isinstance(check.get("target"), str) else ""
        ev_text = (
            check["evidence"].strip() if isinstance(check.get("evidence"), str) else ""
        )
        evidence = f"{target}: {ev_text}" if (target and ev_text) else ""
        status = (
            "needs-validation"
            if in_progress
            else ("confirmed" if check.get("status") == "aligned" else "supported")
        )
        matched_key = (
            _claim_anchor(
                anchors,
                claimed,
                _spec_item_matches,
                "convergence_check",
                check,
            )
            if use_anchors
            else None
        )
        key = (
            matched_key
            if matched_key is not None
            else (
                f"convergence_check:{idx + 1}:{check['id']}"
                if use_anchors
                else str(check["id"])
            )
        )
        records.append(_make_normalized_record(key, status, evidence))
    return records


def _workflow_anchor_matches(
    kind: str, props: dict[str, Any], anchor: dict[str, Any]
) -> bool:
    if anchor.get("kind") != kind:
        return False
    if kind == "finding_verification":
        if "reproduction_status" in anchor and not _value_matches_expected(
            props.get("reproduction_status"), anchor["reproduction_status"]
        ):
            return False
    elif kind == "regression":
        test_file = str(props.get("test_file", ""))
        if "test_file" in anchor and test_file != anchor["test_file"]:
            return False
        if "test_glob" in anchor and (
            not test_file
            or not _matches_writable_glob(test_file, str(anchor["test_glob"]))
        ):
            return False
    elif kind == "task":
        if "file_scopes" in anchor and list(props.get("file_scopes", [])) != list(
            anchor["file_scopes"]
        ):
            return False
        if "has_blockers" in anchor and bool(props.get("has_blockers")) is not bool(
            anchor["has_blockers"]
        ):
            return False
    elif kind == "file_change":
        if "path" in anchor and props.get("path") != anchor["path"]:
            return False
    elif kind == "verification":
        if "result" in anchor and props.get("result") != anchor["result"]:
            return False
    elif kind == "transition":
        if (
            "fingerprint" in anchor
            and props.get("fingerprint") != anchor["fingerprint"]
        ):
            return False
        if (
            "previous_state" in anchor
            and props.get("previous_state") != anchor["previous_state"]
        ):
            return False
        if "new_state" in anchor and props.get("new_state") != anchor["new_state"]:
            return False
    elif kind == "handoff_file":
        if "path" in anchor and props.get("path") != anchor["path"]:
            return False
    elif kind == "handoff_untouched":
        return True
    elif (
        kind == "commit"
        and "commit_type" in anchor
        and props.get("commit_type") != anchor["commit_type"]
    ):
        return False
    return True


def _project_workflow_records(
    payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    anchors = _case_anchors(case)
    use_anchors = isinstance(case, dict) and "expected_anchors" in case
    claimed: set[int] = set()
    records: list[dict[str, str]] = []

    if "reproduction_status" in payload:
        fid = str(payload.get("finding_id", ""))
        raw_status = str(payload.get("reproduction_status", "needs-validation"))
        ev_parts: list[str] = []
        for text_key in ("result", "notes"):
            val = payload.get(text_key)
            if isinstance(val, str) and val.strip():
                ev_parts.append(val.strip())
        if not ev_parts and payload.get("commands"):
            ev_parts.append("; ".join(str(c) for c in payload["commands"]))
        if not ev_parts and payload.get("safe_control_outcomes"):
            ev_parts.append("safe_control_outcomes recorded")
        if fid and fid != "none":
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "finding_verification",
                    {"reproduction_status": raw_status},
                )
                if (use_anchors and raw_status in _ACTIONABLE_AUDIT_STATUSES)
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (f"verify:{fid}" if use_anchors else fid)
            )
            records.append(
                _make_normalized_record(key, raw_status, "; ".join(ev_parts))
            )
    elif "failure_boundaries" in payload:
        fid = str(payload.get("finding_id", ""))
        if fid and fid != "none":
            test_file = str(payload.get("test_file", ""))
            grounded_ok = (
                bool(payload.get("failure_boundaries"))
                and bool(payload.get("test_command_traces"))
                and bool(payload.get("assertion_expectations"))
            )
            ev = (
                "; ".join(str(b) for b in payload["failure_boundaries"])
                if grounded_ok
                else ""
            )
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "regression",
                    {"test_file": test_file},
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (f"regression:{fid}" if use_anchors else fid)
            )
            records.append(_make_normalized_record(key, "confirmed", ev))
    elif "task_breakdowns" in payload:
        for idx, task in enumerate(payload.get("task_breakdowns", [])):
            if (
                not isinstance(task, dict)
                or not isinstance(task.get("task_id"), str)
                or task["task_id"] == "noop"
            ):
                continue
            scopes = task.get("file_scopes")
            criteria = task.get("acceptance_criteria")
            has_blockers = bool(task.get("blocked_by") or task.get("dependencies"))
            ev = (
                f"scopes={scopes}; criteria={criteria}" if (scopes and criteria) else ""
            )
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "task",
                    {
                        "file_scopes": scopes if isinstance(scopes, list) else [],
                        "has_blockers": has_blockers,
                    },
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (
                    f"task:{idx + 1}:{task['task_id']}"
                    if use_anchors
                    else str(task["task_id"])
                )
            )
            records.append(_make_normalized_record(key, "supported", ev))
    elif "file_changes" in payload:
        exec_ok = any(
            isinstance(er, dict) and er.get("passed") is True
            for er in payload.get("execution_results", [])
        )
        has_diffs = bool(payload.get("diff_summaries"))
        for idx, item in enumerate(payload.get("file_changes", [])):
            if not isinstance(item, str | dict):
                continue
            path_val = item if isinstance(item, str) else str(item.get("path", ""))
            if not path_val or path_val == "none":
                continue
            ev = f"{path_val}: verified diff" if (has_diffs and exec_ok) else ""
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "file_change",
                    {"path": path_val},
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (f"change:{idx + 1}:{path_val}" if use_anchors else path_val)
            )
            records.append(_make_normalized_record(key, "confirmed", ev))
    elif "original_reproduced" in payload and "safe_controls_passed" in payload:
        res_label = str(payload.get("result", ""))
        if res_label and res_label != "abstained":
            cmds = payload.get("commands")
            ev = "; ".join(str(c) for c in cmds) if cmds else ""
            status = "confirmed" if res_label == "verified-fixed" else "supported"
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "verification",
                    {"result": res_label},
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (f"verification:{res_label}" if use_anchors else res_label)
            )
            records.append(_make_normalized_record(key, status, ev))
    elif "remediation_state_transitions" in payload:
        matched_ids = {
            m.get("finding_id")
            for m in payload.get("finding_fingerprint_matches", [])
            if isinstance(m, dict)
        }
        fp_by_fid = {
            str(m["finding_id"]): str(m["fingerprint"])
            for m in payload.get("finding_fingerprint_matches", [])
            if isinstance(m, dict)
            and isinstance(m.get("finding_id"), str)
            and isinstance(m.get("fingerprint"), str)
        }
        for idx, trans in enumerate(payload.get("remediation_state_transitions", [])):
            if not isinstance(trans, dict):
                continue
            fid = str(trans.get("finding_id", ""))
            new_state = str(trans.get("new_state", ""))
            prev_state = str(trans.get("previous_state", ""))
            if not fid or not new_state:
                continue
            fp = str(trans.get("fingerprint") or fp_by_fid.get(fid, ""))
            proof = (
                trans["verification_proof"].strip()
                if isinstance(trans.get("verification_proof"), str)
                else ""
            )
            reason = (
                trans["reason"].strip() if isinstance(trans.get("reason"), str) else ""
            )
            if new_state == "verified-fixed":
                ev = proof
                status = "confirmed"
            else:
                ev = proof or reason or (f"matched:{fid}" if fid in matched_ids else "")
                status = "supported"
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "transition",
                    {
                        "fingerprint": fp,
                        "previous_state": prev_state,
                        "new_state": new_state,
                    },
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (
                    f"transition:{idx + 1}:{fid}:{new_state}"
                    if use_anchors
                    else f"{fid}:{new_state}"
                )
            )
            records.append(_make_normalized_record(key, status, ev))
    elif "dead_ends" in payload and "verified_facts" in payload:
        next_act = payload.get("next_action")
        action_text = next_act.strip() if isinstance(next_act, str) else ""
        abstained = not action_text or action_text.lower() == "abstained"
        grounded_ok = (
            bool(payload.get("verified_facts"))
            and bool(payload.get("commands_run"))
            and not abstained
        )
        touched = [
            p
            for p in payload.get("touched_files", [])
            if isinstance(p, str) and p and p != "none"
        ]
        if touched:
            for idx, p in enumerate(touched):
                ev = f"{p}: {action_text}" if grounded_ok else ""
                matched_key = (
                    _claim_anchor(
                        anchors,
                        claimed,
                        _workflow_anchor_matches,
                        "handoff_file",
                        {"path": p},
                    )
                    if use_anchors
                    else None
                )
                key = (
                    matched_key
                    if matched_key is not None
                    else (f"handoff:{idx + 1}:{p}" if use_anchors else p)
                )
                records.append(_make_normalized_record(key, "confirmed", ev))
        elif not abstained:
            ev = f"untouched: {action_text}" if grounded_ok else ""
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "handoff_untouched",
                    {},
                )
                if use_anchors
                else None
            )
            if matched_key is not None:
                records.append(_make_normalized_record(matched_key, "supported", ev))
            else:
                records.append(_make_normalized_record("untouched", "refuted", ev))
    elif "commit_subject" in payload and "compression_level" in payload:
        subj = str(payload.get("commit_subject", ""))
        ctype = str(payload.get("commit_type", ""))
        if subj and not subj.startswith("chore: abstain") and ctype:
            grounded_ok = (
                0 < len(subj) <= 50
                and bool(payload.get("preserved_exact_spans"))
                and payload.get("git_mutated") is False
            )
            ev = subj if grounded_ok else ""
            matched_key = (
                _claim_anchor(
                    anchors,
                    claimed,
                    _workflow_anchor_matches,
                    "commit",
                    {"commit_type": ctype},
                )
                if use_anchors
                else None
            )
            key = (
                matched_key
                if matched_key is not None
                else (f"commit:{ctype}" if use_anchors else ctype)
            )
            records.append(_make_normalized_record(key, "confirmed", ev))
    return records


def _project_normalized_records(
    contract: str, payload: dict[str, Any], case: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    if contract == "architecture":
        return _project_architecture_records(payload, case)
    if contract == "review":
        return _project_review_records(payload, case)
    if contract == "testing":
        return _project_testing_records(payload, case)
    if contract == "spec":
        return _project_spec_records(payload, case)
    if contract == "workflow":
        return _project_workflow_records(payload, case)
    return _project_audit_records(payload, case)


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


def _grade_architecture(payload: dict[str, Any], case: dict[str, Any]) -> _GradeOutcome:
    records = _project_architecture_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True, cap_uncertain_at_one=True
    )
    boundaries = [b for b in payload.get("boundaries", []) if isinstance(b, dict)]
    decisions = [d for d in payload.get("decisions", []) if isinstance(d, dict)]

    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if "status" in assertions and payload.get("status") != assertions["status"]:
            assertions_ok = False
        if "min_invariants" in assertions:
            inv_total = sum(
                len(b.get("invariants", []))
                for b in boundaries
                if isinstance(b.get("invariants"), list)
            )
            if inv_total < int(assertions["min_invariants"]):
                assertions_ok = False
        if "min_consequences" in assertions:
            cons_total = sum(
                len(d.get("consequences", []))
                for d in decisions
                if isinstance(d.get("consequences"), list)
            )
            if cons_total < int(assertions["min_consequences"]):
                assertions_ok = False

    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _grade_review(payload: dict[str, Any], case: dict[str, Any]) -> _GradeOutcome:
    records = _project_review_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True
    )
    findings = [f for f in payload.get("findings", []) if isinstance(f, dict)]
    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if "verdict" in assertions and payload.get("verdict") != assertions["verdict"]:
            assertions_ok = False
        if isinstance(assertions.get("files"), list):
            reported_files = {f.get("file") for f in findings}
            if not set(assertions["files"]).issubset(reported_files):
                assertions_ok = False
        if assertions.get("require_recommendations") and any(
            not isinstance(f.get("recommendation"), str)
            or not f["recommendation"].strip()
            for f in findings
        ):
            assertions_ok = False

    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _check_generated_paths(
    assertions: Mapping[str, Any], changed_paths: set[str], source_dir: Path
) -> bool:
    gen_files = assertions.get("generated_files")
    if isinstance(gen_files, list):
        for rel in gen_files:
            if (
                not isinstance(rel, str)
                or rel not in changed_paths
                or not (source_dir / rel).is_file()
            ):
                return False
    gen_globs = assertions.get("generated_globs")
    if isinstance(gen_globs, list):
        for pat in gen_globs:
            if not isinstance(pat, str) or not any(
                _matches_writable_glob(rel, pat) and (source_dir / rel).is_file()
                for rel in changed_paths
            ):
                return False
    return True


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
    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if (
            "component" in assertions
            and payload.get("component") != assertions["component"]
        ):
            assertions_ok = False
        if (
            "framework" in assertions
            and payload.get("framework") != assertions["framework"]
        ):
            assertions_ok = False
        if "verified" in assertions and any(
            tc.get("verified") is not assertions["verified"] for tc in test_cases
        ):
            assertions_ok = False
        if not _check_generated_paths(assertions, changed_paths, source_dir):
            assertions_ok = False

    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


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
    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if "status" in assertions and payload.get("status") != assertions["status"]:
            assertions_ok = False
        if "new_file_scopes" in assertions and payload.get("new_file_scopes") != list(
            assertions["new_file_scopes"]
        ):
            assertions_ok = False
        if not _check_generated_paths(assertions, changed_paths, source_dir):
            assertions_ok = False

    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _tasks_are_acyclic(tasks: list[dict[str, Any]]) -> bool:
    graph: dict[str, list[str]] = {}
    known = {
        str(t["task_id"])
        for t in tasks
        if isinstance(t.get("task_id"), str) and t["task_id"] != "noop"
    }
    for task in tasks:
        tid = task.get("task_id")
        if not isinstance(tid, str) or tid == "noop":
            continue
        deps: list[str] = []
        for dep_key in ("blocked_by", "dependencies"):
            raw_deps = task.get(dep_key)
            if isinstance(raw_deps, list):
                deps.extend(str(d) for d in raw_deps)
        if any(dep not in known for dep in deps):
            return False
        graph[tid] = deps

    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return False
        if mark == 2:
            return True
        state[node] = 1
        for dep in graph.get(node, []):
            if not visit(dep):
                return False
        state[node] = 2
        return True

    return all(visit(tid) for tid in graph)


def _dependency_order_matches_scopes(
    payload: dict[str, Any], expected_scopes: list[Any]
) -> bool:
    raw_tasks = [
        t
        for t in payload.get("task_breakdowns", [])
        if isinstance(t, dict)
        and isinstance(t.get("task_id"), str)
        and t["task_id"] != "noop"
    ]
    dep_order = payload.get("dependency_order")
    if not isinstance(dep_order, list) or len(dep_order) != len(raw_tasks):
        return False
    order_index = {str(tid): idx for idx, tid in enumerate(dep_order)}
    if len(order_index) != len(raw_tasks):
        return False
    scopes_by_tid: dict[str, list[Any]] = {}
    for task in raw_tasks:
        tid = str(task["task_id"])
        if tid not in order_index:
            return False
        scopes_by_tid[tid] = list(task.get("file_scopes", []))
        for dep_key in ("blocked_by", "dependencies"):
            for dep in task.get(dep_key, []):
                dep_id = str(dep)
                if dep_id not in order_index or order_index[dep_id] >= order_index[tid]:
                    return False
    ordered_actual = [scopes_by_tid[str(tid)] for tid in dep_order]
    return ordered_actual == expected_scopes


def _grade_workflow(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_workflow_records(payload, case)
    reported, grounded, uncertain = _score_normalized_records(records)
    if (
        "reproduction_status" in payload
        and payload.get("reproduction_status") == "needs-validation"
    ):
        uncertain = max(uncertain, 1)
    elif "remediation_state_transitions" in payload:
        uncertain += sum(
            isinstance(m, dict) and m.get("match_status") == "ambiguous"
            for m in payload.get("finding_fingerprint_matches", [])
        )
    elif "dead_ends" in payload and "verified_facts" in payload:
        uncertain = len(payload.get("assumptions", []))

    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        for direct_field in (
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
        ):
            if (
                direct_field in assertions
                and payload.get(direct_field) != assertions[direct_field]
            ):
                assertions_ok = False
        if (
            "ordered_file_scopes" in assertions
            and isinstance(assertions["ordered_file_scopes"], list)
            and not _dependency_order_matches_scopes(
                payload, assertions["ordered_file_scopes"]
            )
        ):
            assertions_ok = False
        if "touched_files" in assertions and list(
            payload.get("touched_files", [])
        ) != list(assertions["touched_files"]):
            assertions_ok = False
        if "repository_dirty" in assertions:
            repo_obj = payload.get("repository")
            dirty_val = repo_obj.get("dirty") if isinstance(repo_obj, dict) else None
            if dirty_val is not assertions["repository_dirty"]:
                assertions_ok = False
        if "abstained" in assertions:
            is_abstained = (
                str(payload.get("next_action", "")).strip().lower() == "abstained"
            )
            if is_abstained is not bool(assertions["abstained"]):
                assertions_ok = False
        if "dead_end_count" in assertions and len(payload.get("dead_ends", [])) != int(
            assertions["dead_end_count"]
        ):
            assertions_ok = False
        if "max_subject_length" in assertions and len(
            str(payload.get("commit_subject", ""))
        ) > int(assertions["max_subject_length"]):
            assertions_ok = False
        if "safe_controls_all_passed" in assertions:
            outcomes = payload.get("safe_control_outcomes", [])
            all_passed = bool(outcomes) and all(
                isinstance(c, dict) and c.get("passed") is True for c in outcomes
            )
            if all_passed is not assertions["safe_controls_all_passed"]:
                assertions_ok = False
        if isinstance(assertions.get("changed_files"), list):
            expected_changed = set(assertions["changed_files"])
            payload_changed = {
                item if isinstance(item, str) else str(item.get("path", ""))
                for item in payload.get("file_changes", [])
                if isinstance(item, str | dict)
            } - {"", "none"}
            if changed_paths != expected_changed or payload_changed != expected_changed:
                assertions_ok = False
        if not _check_generated_paths(assertions, changed_paths, source_dir):
            assertions_ok = False
        if assertions.get("all_executions_passed"):
            exec_results = payload.get("execution_results", [])
            if not exec_results or not all(
                isinstance(er, dict)
                and er.get("passed") is True
                and er.get("exit_code") == 0
                for er in exec_results
            ):
                assertions_ok = False
        if "acyclic" in assertions:
            raw_tasks = [
                t for t in payload.get("task_breakdowns", []) if isinstance(t, dict)
            ]
            if _tasks_are_acyclic(raw_tasks) is not assertions["acyclic"]:
                assertions_ok = False
        if "unmapped_count" in assertions and len(
            payload.get("unmapped_records", [])
        ) != int(assertions["unmapped_count"]):
            assertions_ok = False

    return _GradeOutcome(
        reported=reported,
        grounded=grounded,
        expected=expected,
        uncertain_count=uncertain,
        assertions_ok=assertions_ok,
        normalized_records=records,
    )


def _dispatch_suite_grader(
    contract: str,
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    if contract == "architecture":
        return _grade_architecture(payload, case)
    if contract == "review":
        return _grade_review(payload, case)
    if contract == "testing":
        return _grade_testing(payload, case, changed_paths, source_dir)
    if contract == "spec":
        return _grade_spec(payload, case, changed_paths, source_dir)
    if contract == "workflow":
        return _grade_workflow(payload, case, changed_paths, source_dir)
    return _grade_audit(payload, case)


def _extract_tokens(usage: Any) -> int | None:
    tokens = usage.get("tokens") if isinstance(usage, dict) else None
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        return None
    return tokens


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for configuration in ["without_skill", "with_skill"]:
        rows = [row for row in results if row["configuration"] == configuration]
        token_values = [row["tokens"] for row in rows if row["tokens"] is not None]
        summary[configuration] = {
            "runs": len(rows),
            "exact_match_rate": sum(row["exact_match"] for row in rows) / len(rows)
            if rows
            else None,
            "mean_seconds": statistics.mean(row["seconds"] for row in rows)
            if rows
            else None,
            "failures": sum(row["state"] != "executed" for row in rows),
            "tokens_reported_runs": len(token_values),
            "total_tokens": sum(token_values) if token_values else None,
            "mean_tokens": statistics.mean(token_values) if token_values else None,
        }

    baseline_by_key = {
        (row["case"], row["repeat"]): row
        for row in results
        if row["configuration"] == "without_skill"
    }
    assisted_by_key = {
        (row["case"], row["repeat"]): row
        for row in results
        if row["configuration"] == "with_skill"
    }
    paired_keys = [key for key in baseline_by_key if key in assisted_by_key]
    pairs = [(baseline_by_key[k], assisted_by_key[k]) for k in paired_keys]
    token_pairs = [
        (base["tokens"], skill["tokens"])
        for base, skill in pairs
        if base["tokens"] is not None and skill["tokens"] is not None
    ]
    summary["skill_lift"] = {
        "paired_runs": len(pairs),
        "exact_match_rate_delta": statistics.mean(
            float(skill["exact_match"]) - float(base["exact_match"])
            for base, skill in pairs
        )
        if pairs
        else None,
        "mean_seconds_delta": statistics.mean(
            skill["seconds"] - base["seconds"] for base, skill in pairs
        )
        if pairs
        else None,
        "failures_delta": sum(
            int(skill["state"] != "executed") - int(base["state"] != "executed")
            for base, skill in pairs
        ),
        "true_positives_delta": sum(
            skill["true_positives"] - base["true_positives"] for base, skill in pairs
        ),
        "false_positives_delta": sum(
            skill["false_positives"] - base["false_positives"] for base, skill in pairs
        ),
        "false_negatives_delta": sum(
            skill["false_negatives"] - base["false_negatives"] for base, skill in pairs
        ),
        "paired_token_runs": len(token_pairs),
        "total_tokens_delta": sum(s_tok - b_tok for b_tok, s_tok in token_pairs)
        if token_pairs
        else None,
        "mean_tokens_delta": statistics.mean(
            s_tok - b_tok for b_tok, s_tok in token_pairs
        )
        if token_pairs
        else None,
    }
    return summary


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
    if (
        not command
        or isinstance(repeats, bool)
        or not 1 <= repeats <= 10
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 3600
        or (
            max_tokens is not None
            and (
                isinstance(max_tokens, bool)
                or not isinstance(max_tokens, int)
                or max_tokens < 1
            )
        )
    ):
        raise ValueError(
            "Evaluation requires an explicit JSON argv adapter, 1–10 repeats, a bounded timeout, and a positive max_tokens limit when set"
        )
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty evaluation output directory")
    manifest = read_json(root / "evals/cases.json")
    selected = [case for case in manifest["cases"] if not cases or case["id"] in cases]
    if cases and set(cases) - {case["id"] for case in selected}:
        raise ValueError("Unknown evaluation case ID")
    suite_map = _skill_to_suite_map(root)
    ignore_bytecode = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", ".cursor-plugin"
    )
    results: list[dict[str, Any]] = []
    for case in selected:
        contract = _resolve_case_contract(case, suite_map)
        schema_path = _resolve_case_schema(case, contract, root)
        writable_paths = case.get("writable_paths")
        for configuration in ["without_skill", "with_skill"]:
            for repeat in range(repeats):
                work = output / case["id"] / configuration / str(repeat + 1)
                work.mkdir(parents=True)
                fixture = root / "evals" / relative_path(case["fixture"])
                source_dir = work / "source"
                shutil.copytree(fixture, source_dir, ignore=ignore_bytecode)
                if configuration == "with_skill":
                    skill = root / "skills" / relative_path(case["skill"])
                    shutil.copytree(
                        skill,
                        work / "skill",
                        ignore=ignore_bytecode,
                    )
                request = _build_prompt_request(case, configuration)
                prompt = work / "prompt.md"
                prompt.write_text(request + "\n", encoding="utf-8")
                inputs_before = package_files(source_dir)
                substitutions = {
                    "{prompt_file}": str(prompt),
                    "{output_file}": str(work / "result.json"),
                    "{case_dir}": str(work),
                }
                argv = []
                for argument in command:
                    for key, value in substitutions.items():
                        argument = argument.replace(key, value)
                    argv.append(argument)
                start = time.monotonic()
                state, error = "executed", None
                payload: dict[str, Any] | None = None
                actor: dict[str, Any] = {"kind": "unknown"}
                usage: Any = None
                try:
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
                            if sys.platform != "win32":
                                with contextlib.suppress(ProcessLookupError):
                                    os.killpg(proc.pid, signal.SIGKILL)
                            else:
                                proc.kill()
                            proc.wait()
                            raise
                    if proc.returncode:
                        state, error = "failed", f"Adapter exited {proc.returncode}"
                    elif (work / "result.json").exists():
                        if (work / "result.json").stat().st_size > 2_000_000:
                            raise ValueError("Adapter result exceeds the 2 MB limit")
                        raw_result = read_json(work / "result.json")
                        payload, actor, usage = _unwrap_adapter_result(raw_result)
                        _validate_contract_payload(payload, contract, schema_path)
                    else:
                        state, error = "failed", "Adapter did not write result.json"
                except subprocess.TimeoutExpired:
                    state, error = "timeout", "Adapter exceeded its time budget"
                except (OSError, ValueError, TypeError) as exc:
                    state, error = "failed", str(exc)

                inputs_changed, changed_paths, mutation_error = (
                    _evaluate_source_changes(inputs_before, source_dir, writable_paths)
                )
                if mutation_error is not None:
                    state, error = "failed", mutation_error

                # Grading happens only after adapter execution, outside its workdir.
                if payload is not None and state == "executed":
                    outcome = _dispatch_suite_grader(
                        contract, payload, case, changed_paths, source_dir
                    )
                    if outcome.malformed_error is not None:
                        state, error = "failed", outcome.malformed_error
                else:
                    outcome = _GradeOutcome(
                        reported=set(),
                        grounded=set(),
                        expected=_expected_keys_from_case(case),
                        uncertain_count=0,
                        assertions_ok=False,
                    )

                tokens = _extract_tokens(usage)
                if (
                    max_tokens is not None
                    and tokens is not None
                    and tokens > max_tokens
                    and state == "executed"
                ):
                    state = "failed"
                    error = f"Adapter token usage ({tokens}) exceeded max_tokens ({max_tokens})"

                reported = outcome.reported
                grounded = outcome.grounded
                expected = outcome.expected
                true = reported & expected
                record = {
                    "case": case["id"],
                    "configuration": configuration,
                    "repeat": repeat + 1,
                    "state": state,
                    "error": error,
                    "seconds": round(time.monotonic() - start, 4),
                    "inputs_changed": inputs_changed,
                    "true_positives": len(true),
                    "false_positives": len(reported - expected),
                    "false_negatives": len(expected - reported),
                    "unsubstantiated_claims": len(reported - grounded),
                    "uncertain_count": outcome.uncertain_count,
                    "normalized_records": outcome.normalized_records,
                    "actor": actor,
                    "exact_match": state == "executed"
                    and outcome.assertions_ok
                    and reported == expected
                    and reported <= grounded,
                    "tokens": tokens,
                    "result_path": (work / "result.json")
                    .relative_to(output)
                    .as_posix(),
                }
                results.append(record)
                atomic_json(work / "grading.json", record)

    summary = _build_summary(results)
    benchmark = {
        "schema_version": "1.0",
        "toolkit_version": version(root),
        "adapter_argv": command,
        "cases_digest": digest(manifest),
        "runs": results,
        "summary": summary,
        "limitations": [
            "Property-anchor matching is a bounded deterministic rubric, not semantic proof; review raw evidence and traces.",
            "The adapter has normal process access. A fresh directory prevents accidental expected-answer leakage, not a security sandbox.",
            "Log files are streamed without a disk quota. POSIX timeouts kill the process group; Windows stops the direct child only. Run untrusted adapters in an external sandbox with resource limits.",
            "The provided deterministic reference adapter tests this harness, not an LLM. Label model/host versions in real adapter output.",
        ],
    }
    atomic_json(output / "benchmark.json", benchmark)
    return benchmark

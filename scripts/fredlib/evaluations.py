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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from .common import (
    ROOT,
    _infer_suite_name,
    _skill_to_suite_map,
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

_VALID_CONTRACTS: frozenset[str] = frozenset(_SUITE_TO_CONTRACT.values())

_AUDIT_STATUSES: frozenset[str] = frozenset(
    {
        "confirmed",
        "supported",
        "needs-validation",
        "hardening",
        "refuted",
        "grouped",
    }
)

_ACTIONABLE_AUDIT_STATUSES: frozenset[str] = frozenset(
    {"confirmed", "supported", "grouped"}
)


@dataclass
class _GradeOutcome:
    reported: set[str]
    grounded: set[str]
    expected: set[str]
    uncertain_count: int
    assertions_ok: bool
    normalized_records: list[dict[str, str]] = field(default_factory=list)
    malformed_error: str | None = None


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


def _resolve_case_schema(
    case: dict[str, Any], contract: str, root: Path
) -> Path | None:
    explicit = case.get("schema")
    if explicit is not None:
        if not isinstance(explicit, str):
            raise ValueError("Evaluation case schema must be a relative path string")
        schema_path = root / relative_path(explicit)
        if schema_path.is_symlink() or not schema_path.is_file():
            raise ValueError(f"Missing evaluation schema: {explicit}")
        return schema_path
    if contract == "audit":
        return None
    skill_dir = root / "skills" / relative_path(case["skill"])
    candidates = sorted(
        p
        for p in (skill_dir / "assets").glob("*.schema.json")
        if not p.name.startswith("evaluation-scenarios") and not p.is_symlink()
    )
    if not candidates:
        raise ValueError(f"Missing output schema for skill: {case['skill']}")
    return candidates[0]


def _build_prompt_request(
    case: dict[str, Any],
    contract: str,
    schema_path: Path | None,
    configuration: str,
) -> str:
    if contract == "audit":
        contract_instruction = (
            "Inspect ./source. Return result.json with a findings array of "
            "{key, status, evidence} records."
        )
    elif contract == "architecture":
        schema_label = schema_path.name if schema_path else "architecture.schema.json"
        contract_instruction = (
            f"Inspect ./source. Return result.json conforming to {schema_label} "
            "(schema_version, title, status, boundaries, and decisions)."
        )
    elif contract == "review":
        schema_label = schema_path.name if schema_path else "review.schema.json"
        contract_instruction = (
            f"Inspect ./source. Return result.json conforming to {schema_label} "
            "(schema_version, verdict, summary, and findings)."
        )
    elif contract == "testing":
        schema_label = schema_path.name if schema_path else "testing.schema.json"
        contract_instruction = (
            f"Inspect ./source. Return result.json conforming to {schema_label} "
            "(schema_version, component, framework, and test_cases)."
        )
    elif contract == "spec":
        schema_label = schema_path.name if schema_path else "spec.schema.json"
        contract_instruction = (
            f"Inspect ./source. Return result.json conforming to {schema_label} "
            "(schema_version, title, status, interview_decisions, new_file_scopes, out_of_scope, and verification_commands)."
        )
    else:
        schema_label = schema_path.name if schema_path else "workflow.schema.json"
        required_fields: list[str] = []
        if schema_path is not None:
            try:
                schema_doc = read_json(schema_path)
                if isinstance(schema_doc, dict) and isinstance(
                    schema_doc.get("required"), list
                ):
                    required_fields = [
                        str(f) for f in schema_doc["required"] if isinstance(f, str)
                    ]
            except (OSError, ValueError):
                required_fields = []
        fields_hint = f" ({', '.join(required_fields)})" if required_fields else ""
        contract_instruction = f"Inspect ./source. Return result.json conforming to {schema_label}{fields_hint}."

    request = f"{case['prompt']}\n\n{contract_instruction}"
    if configuration == "with_skill":
        request += "\nA skill is available at ./skill/SKILL.md. Use it only if it applies to the user's request."
    request += "\nInspect only this case's source and optional skill; do not inspect grading data, parent workspaces, or other cases."
    return request


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
    if contract == "audit":
        if not isinstance(payload.get("findings"), list):
            raise ValueError("Adapter result must be an object with a findings array")
        if schema_path is not None:
            schema_doc = read_json(schema_path)
            errors = sorted(
                jsonschema.Draft202012Validator(schema_doc).iter_errors(payload),
                key=lambda err: [str(p) for p in err.path],
            )
            if errors:
                raise ValueError(
                    f"Adapter result failed {schema_path.name} validation: {errors[0].message}"
                )
        return

    if schema_path is None:
        raise ValueError(f"Missing schema path for {contract} contract")
    schema_doc = read_json(schema_path)
    errors = sorted(
        jsonschema.Draft202012Validator(schema_doc).iter_errors(payload),
        key=lambda err: [str(p) for p in err.path],
    )
    if errors:
        raise ValueError(
            f"Adapter result failed {schema_path.name} validation: {errors[0].message}"
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


def _expected_keys_from_case(case: dict[str, Any]) -> set[str]:
    base = {str(k) for k in case.get("expected_keys", [])}
    assertions = case.get("expected_assertions")
    if isinstance(assertions, dict):
        for list_field in (
            "boundary_names",
            "decision_ids",
            "finding_ids",
            "test_case_ids",
            "interview_decision_ids",
            "convergence_check_ids",
            "changed_files",
        ):
            vals = assertions.get(list_field)
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


def _project_audit_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    items = payload.get("findings")
    if not isinstance(items, list):
        raise TypeError("Malformed findings response")
    records: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("Malformed findings response")
        raw_status = item.get("status")
        if not isinstance(raw_status, str) or raw_status not in _AUDIT_STATUSES:
            raise ValueError("Malformed findings response")
        raw_key = item.get("key")
        if not isinstance(raw_key, str) or not raw_key:
            if "schema_version" in payload and isinstance(item.get("id"), str):
                raw_key = str(item.get("category") or item["id"])
            else:
                raise ValueError("Malformed findings response")
        raw_ev = item.get("evidence")
        if isinstance(raw_ev, str):
            evidence = raw_ev
        elif isinstance(item.get("evidence_ids"), list):
            evidence = "; ".join(
                str(eid).strip() for eid in item["evidence_ids"] if str(eid).strip()
            )
        else:
            evidence = ""
        records.append(_make_normalized_record(raw_key, raw_status, evidence))
    return records


def _project_architecture_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    doc_status = payload.get("status")
    record_status = (
        "needs-validation"
        if doc_status == "draft"
        else "supported"
        if doc_status == "accepted"
        else "grouped"
    )
    records: list[dict[str, str]] = []
    for boundary in payload.get("boundaries", []):
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
        records.append(
            _make_normalized_record(str(boundary["name"]), record_status, evidence)
        )
    decision_status = "confirmed" if doc_status == "accepted" else record_status
    for decision in payload.get("decisions", []):
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
        records.append(
            _make_normalized_record(str(decision["id"]), decision_status, evidence)
        )
    return records


def _project_review_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for finding in payload.get("findings", []):
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
        records.append(_make_normalized_record(str(finding["id"]), status, evidence))
    return records


def _project_testing_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    valid_oracles = {"state", "exception", "output", "boundary"}
    for tc in payload.get("test_cases", []):
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
        records.append(_make_normalized_record(str(tc["id"]), status, evidence))
    return records


def _project_spec_records(payload: dict[str, Any]) -> list[dict[str, str]]:
    doc_status = payload.get("status")
    in_progress = doc_status in {"draft", "interviewing"}
    records: list[dict[str, str]] = []
    for decision in payload.get("interview_decisions", []):
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
        records.append(_make_normalized_record(str(decision["id"]), status, evidence))
    for check in payload.get("convergence_checks", []):
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
        records.append(_make_normalized_record(str(check["id"]), status, evidence))
    return records


def _project_workflow_records(payload: dict[str, Any]) -> list[dict[str, str]]:
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
            records.append(
                _make_normalized_record(fid, raw_status, "; ".join(ev_parts))
            )
    elif "failure_boundaries" in payload:
        fid = str(payload.get("finding_id", ""))
        if fid and fid != "none":
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
            records.append(_make_normalized_record(fid, "confirmed", ev))
    elif "task_breakdowns" in payload:
        for task in payload.get("task_breakdowns", []):
            if (
                not isinstance(task, dict)
                or not isinstance(task.get("task_id"), str)
                or task["task_id"] == "noop"
            ):
                continue
            scopes = task.get("file_scopes")
            criteria = task.get("acceptance_criteria")
            ev = (
                f"scopes={scopes}; criteria={criteria}" if (scopes and criteria) else ""
            )
            records.append(
                _make_normalized_record(str(task["task_id"]), "supported", ev)
            )
    elif "file_changes" in payload:
        exec_ok = any(
            isinstance(er, dict) and er.get("passed") is True
            for er in payload.get("execution_results", [])
        )
        has_diffs = bool(payload.get("diff_summaries"))
        for item in payload.get("file_changes", []):
            if not isinstance(item, str | dict):
                continue
            path_val = item if isinstance(item, str) else str(item.get("path", ""))
            if not path_val or path_val == "none":
                continue
            ev = f"{path_val}: verified diff" if (has_diffs and exec_ok) else ""
            records.append(_make_normalized_record(path_val, "confirmed", ev))
    elif "original_reproduced" in payload and "safe_controls_passed" in payload:
        res_label = str(payload.get("result", ""))
        if res_label and res_label != "abstained":
            cmds = payload.get("commands")
            ev = "; ".join(str(c) for c in cmds) if cmds else ""
            status = "confirmed" if res_label == "verified-fixed" else "supported"
            records.append(_make_normalized_record(res_label, status, ev))
    elif "remediation_state_transitions" in payload:
        matched_ids = {
            m.get("finding_id")
            for m in payload.get("finding_fingerprint_matches", [])
            if isinstance(m, dict)
        }
        for trans in payload.get("remediation_state_transitions", []):
            if not isinstance(trans, dict):
                continue
            fid = str(trans.get("finding_id", ""))
            new_state = str(trans.get("new_state", ""))
            if not fid or not new_state:
                continue
            key = f"{fid}:{new_state}"
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
            records.append(_make_normalized_record(key, status, ev))
    elif "dead_ends" in payload and "verified_facts" in payload:
        next_act = payload.get("next_action")
        grounded_ok = (
            bool(payload.get("verified_facts"))
            and bool(payload.get("commands_run"))
            and isinstance(next_act, str)
            and bool(next_act.strip())
        )
        action_text = next_act.strip() if isinstance(next_act, str) else ""
        for p in payload.get("touched_files", []):
            if isinstance(p, str) and p and p != "none":
                ev = f"{p}: {action_text}" if grounded_ok else ""
                records.append(_make_normalized_record(p, "confirmed", ev))
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
            records.append(_make_normalized_record(ctype, "confirmed", ev))
    return records


def _project_normalized_records(
    contract: str, payload: dict[str, Any]
) -> list[dict[str, str]]:
    if contract == "architecture":
        return _project_architecture_records(payload)
    if contract == "review":
        return _project_review_records(payload)
    if contract == "testing":
        return _project_testing_records(payload)
    if contract == "spec":
        return _project_spec_records(payload)
    if contract == "workflow":
        return _project_workflow_records(payload)
    return _project_audit_records(payload)


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
    expected = set(case.get("expected_keys", []))
    try:
        records = _project_audit_records(payload)
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
    records = _project_architecture_records(payload)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True, cap_uncertain_at_one=True
    )
    boundaries = [b for b in payload.get("boundaries", []) if isinstance(b, dict)]
    decisions = [d for d in payload.get("decisions", []) if isinstance(d, dict)]
    boundary_keys = {
        str(b["name"]) for b in boundaries if isinstance(b.get("name"), str)
    }
    decision_keys = {str(d["id"]) for d in decisions if isinstance(d.get("id"), str)}

    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if "status" in assertions and payload.get("status") != assertions["status"]:
            assertions_ok = False
        if "boundary_names" in assertions and boundary_keys != set(
            assertions["boundary_names"]
        ):
            assertions_ok = False
        if "decision_ids" in assertions and decision_keys != set(
            assertions["decision_ids"]
        ):
            assertions_ok = False
        if isinstance(assertions.get("boundary_types"), dict):
            by_name = {b.get("name"): b.get("type") for b in boundaries}
            for bname, expected_type in assertions["boundary_types"].items():
                if by_name.get(bname) != expected_type:
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
    records = _project_review_records(payload)
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
        if "finding_ids" in assertions and reported != set(assertions["finding_ids"]):
            assertions_ok = False
        if isinstance(assertions.get("severities"), dict):
            by_id = {f.get("id"): f.get("severity") for f in findings}
            for fid, expected_sev in assertions["severities"].items():
                if isinstance(expected_sev, list):
                    if by_id.get(fid) not in expected_sev:
                        assertions_ok = False
                elif by_id.get(fid) != expected_sev:
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


def _grade_testing(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_testing_records(payload)
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
        if "test_case_ids" in assertions and reported != set(
            assertions["test_case_ids"]
        ):
            assertions_ok = False
        if isinstance(assertions.get("oracle_types"), dict):
            by_id = {tc.get("id"): tc.get("oracle_type") for tc in test_cases}
            for tcid, expected_oracle in assertions["oracle_types"].items():
                if by_id.get(tcid) != expected_oracle:
                    assertions_ok = False
        if "verified" in assertions and any(
            tc.get("verified") is not assertions["verified"] for tc in test_cases
        ):
            assertions_ok = False
        if isinstance(assertions.get("generated_files"), list):
            for rel in assertions["generated_files"]:
                if rel not in changed_paths or not (source_dir / rel).is_file():
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
    records = _project_spec_records(payload)
    reported, grounded, uncertain = _score_normalized_records(
        records, report_needs_validation=True, cap_uncertain_at_one=True
    )
    decisions = [
        d for d in payload.get("interview_decisions", []) if isinstance(d, dict)
    ]
    checks = [c for c in payload.get("convergence_checks", []) if isinstance(c, dict)]
    decision_ids = {str(d["id"]) for d in decisions if isinstance(d.get("id"), str)}
    check_ids = {str(c["id"]) for c in checks if isinstance(c.get("id"), str)}

    assertions = case.get("expected_assertions") or {}
    expected = _expected_keys_from_case(case)
    assertions_ok = True
    if isinstance(assertions, dict):
        if "status" in assertions and payload.get("status") != assertions["status"]:
            assertions_ok = False
        if "interview_decision_ids" in assertions and decision_ids != set(
            assertions["interview_decision_ids"]
        ):
            assertions_ok = False
        if "convergence_check_ids" in assertions and check_ids != set(
            assertions["convergence_check_ids"]
        ):
            assertions_ok = False
        if "new_file_scopes" in assertions and payload.get("new_file_scopes") != list(
            assertions["new_file_scopes"]
        ):
            assertions_ok = False
        if isinstance(assertions.get("generated_files"), list):
            for rel in assertions["generated_files"]:
                if rel not in changed_paths or not (source_dir / rel).is_file():
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


def _grade_workflow(
    payload: dict[str, Any],
    case: dict[str, Any],
    changed_paths: set[str],
    source_dir: Path,
) -> _GradeOutcome:
    records = _project_workflow_records(payload)
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
            "dependency_order",
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
            if changed_paths != expected_changed or reported != expected_changed:
                assertions_ok = False
        if isinstance(assertions.get("generated_files"), list):
            for rel in assertions["generated_files"]:
                if rel not in changed_paths or not (source_dir / rel).is_file():
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
) -> dict:
    """Run a user-supplied adapter without a shell; never pass grader answers to it."""
    if not command or not 1 <= repeats <= 10 or not 1 <= timeout <= 3600:
        raise ValueError(
            "Evaluation requires an explicit JSON argv adapter, 1–10 repeats, and a bounded timeout"
        )
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty evaluation output directory")
    manifest = read_json(root / "evals/cases.json")
    selected = [case for case in manifest["cases"] if not cases or case["id"] in cases]
    if cases and set(cases) - {case["id"] for case in selected}:
        raise ValueError("Unknown evaluation case ID")
    suite_map = _skill_to_suite_map(root)
    ignore_bytecode = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
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
                request = _build_prompt_request(
                    case, contract, schema_path, configuration
                )
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

                reported = outcome.reported
                grounded = outcome.grounded
                expected = outcome.expected
                true = reported & expected
                tokens = _extract_tokens(usage)
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
            "Key matching is a bounded deterministic rubric, not semantic proof; review raw evidence and traces.",
            "The adapter has normal process access. A fresh directory prevents accidental expected-answer leakage, not a security sandbox.",
            "Log files are streamed without a disk quota. POSIX timeouts kill the process group; Windows stops the direct child only. Run untrusted adapters in an external sandbox with resource limits.",
            "The provided deterministic reference adapter tests this harness, not an LLM. Label model/host versions in real adapter output.",
        ],
    }
    atomic_json(output / "benchmark.json", benchmark)
    return benchmark

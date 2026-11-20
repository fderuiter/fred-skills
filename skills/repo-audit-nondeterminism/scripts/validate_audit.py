#!/usr/bin/env python3
"""Validate audit structure and cross-record consistency, not correctness of claims."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any


def _schema_errors(data: Any, schema: Mapping[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError as e:
        raise RuntimeError(
            "Dependency missing: jsonschema. Use an approved environment with jsonschema; no validation performed."
        ) from e
    return [
        (("/".join(map(str, e.absolute_path)) or "root") + ": " + e.message)
        for e in jsonschema.Draft202012Validator(schema).iter_errors(data)
    ]


def _index_field(
    items: list[Any], field: str, errors: list[str]
) -> dict[str, Mapping[str, Any]]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in seen and item_id not in duplicates:
            errors.append(f"Duplicate IDs in {field}: {item_id}")
            duplicates.add(item_id)
        seen.add(item_id)
        indexed[item_id] = item
    return indexed


def _index_records(
    data: Mapping[str, Any], errors: list[str]
) -> dict[str, dict[str, Mapping[str, Any]]]:
    return {
        field: _index_field(data.get(field, []), field, errors)
        for field in ("evidence", "contracts", "findings", "issues")
    }


def _check_refs(
    record: Mapping[str, Any],
    field: str,
    target: str,
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    rid = record.get("id") or record.get("topic") or "unknown"
    target_map = maps.get(target, {})
    return [
        f"Unknown {target} reference {value} in {rid} ({field})"
        for value in record.get(field, [])
        if value not in target_map
    ]


def _is_repo_relative_evidence_path(raw_path: str) -> bool:
    p = pathlib.PurePosixPath(raw_path)
    win_p = pathlib.PureWindowsPath(raw_path)
    return not (
        p.is_absolute() or win_p.is_absolute() or ".." in p.parts or "\\" in raw_path
    )


def _check_evidence(evidence_items: list[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for ev in evidence_items:
        if ev["line_end"] < ev["line_start"]:
            errors.append(f"Reversed lines: {ev['id']}")
        if not _is_repo_relative_evidence_path(ev["path"]):
            errors.append(f"Evidence path must be repository-relative: {ev['id']}")
    return errors


def _check_contract_and_coverage_refs(
    data: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for contract in data.get("contracts", []):
        errors.extend(_check_refs(contract, "evidence_ids", "evidence", maps))
    for cov in data.get("coverage", []):
        errors.extend(_check_refs(cov, "evidence_ids", "evidence", maps))
    return errors


def _check_single_finding(
    finding: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors = _check_refs(finding, "evidence_ids", "evidence", maps)
    errors.extend(_check_refs(finding, "contract_ids", "contracts", maps))
    errors.extend(_check_refs(finding, "related_findings", "findings", maps))
    status = finding["status"]
    if status in ("confirmed", "supported") and (
        not finding["evidence_ids"] or not finding["contract_ids"]
    ):
        errors.append(f"Actionable defect lacks contract/evidence: {finding['id']}")
    if status == "confirmed" and (
        finding["validation"]["state"] != "executed"
        or not finding["validation"]["commands"]
    ):
        errors.append(f"Confirmed finding lacks executed command: {finding['id']}")
    if status == "refuted" and finding["severity"] != "info":
        errors.append(f"Refuted candidate must be info: {finding['id']}")
    return errors


def _check_findings(
    findings: list[Mapping[str, Any]],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for finding in findings:
        errors.extend(_check_single_finding(finding, maps))
    return errors


def _check_single_issue(
    issue: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors = _check_refs(issue, "finding_ids", "findings", maps)
    errors.extend(_check_refs(issue, "depends_on", "issues", maps))
    findings_map = maps["findings"]
    errors.extend(
        f"Issue includes refuted finding: {issue['id']}"
        for fid in issue["finding_ids"]
        if fid in findings_map and findings_map[fid]["status"] == "refuted"
    )
    if issue["publication"] in ("created", "linked") and not issue["existing_url"]:
        errors.append(f"Published issue lacks verified URL: {issue['id']}")
    return errors


def _check_issues(
    issues: list[Mapping[str, Any]],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for issue in issues:
        errors.extend(_check_single_issue(issue, maps))
    return errors


def _visit_issue(
    issue_id: str,
    issues_map: Mapping[str, Mapping[str, Any]],
    visiting: set[str],
    done: set[str],
    errors: list[str],
) -> None:
    if issue_id in visiting:
        errors.append(f"Issue dependency cycle: {issue_id}")
        return
    if issue_id in done or issue_id not in issues_map:
        return
    visiting.add(issue_id)
    for dep in issues_map[issue_id]["depends_on"]:
        _visit_issue(dep, issues_map, visiting, done, errors)
    visiting.remove(issue_id)
    done.add(issue_id)


def _find_issue_cycles(issues_map: Mapping[str, Mapping[str, Any]]) -> list[str]:
    visiting: set[str] = set()
    done: set[str] = set()
    errors: list[str] = []
    for issue_id in issues_map:
        _visit_issue(issue_id, issues_map, visiting, done, errors)
    return errors


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    maps = _index_records(data, errors)
    errors.extend(_check_evidence(data.get("evidence", [])))
    errors.extend(_check_contract_and_coverage_refs(data, maps))
    errors.extend(_check_findings(data.get("findings", []), maps))
    errors.extend(_check_issues(data.get("issues", []), maps))
    errors.extend(_find_issue_cycles(maps.get("issues", {})))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/audit.schema.json",
    )
    a = parser.parse_args()
    try:
        errors = validate(
            json.loads(a.audit.read_text(encoding="utf-8")),
            json.loads(a.schema.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        return 2
    except (ImportError, RuntimeError) as e:
        print(f"{e}", file=sys.stderr)
        print(
            "Remediation: pip install jsonschema (or pip install -r requirements-dev.txt)",
            file=sys.stderr,
        )
        return 2
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(
        "PASS: structure, references, and dependency graph; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate code-review artifact structure, file paths, and verdict consistency."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any


def _is_repo_relative(raw_path: str) -> bool:
    if not raw_path or "\\" in raw_path:
        return False
    posix = pathlib.PurePosixPath(raw_path)
    win = pathlib.PureWindowsPath(raw_path)
    return not (posix.is_absolute() or win.is_absolute() or ".." in posix.parts)


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


def _check_path_list(paths: Any, label: str) -> list[str]:
    return [
        f"Deliverable path must be repository-relative ({label}): {raw_path}"
        for raw_path in paths
        if isinstance(raw_path, str) and not _is_repo_relative(raw_path)
    ]


def _check_top_and_coverage_paths(data: Mapping[str, Any]) -> list[str]:
    errors = _check_path_list(data.get("paths", []), "paths")
    for cov in data.get("coverage", []):
        if isinstance(cov, Mapping):
            errors.extend(_check_path_list(cov.get("paths", []), "coverage.paths"))
    return errors


def _check_finding_paths(finding: Mapping[str, Any], fid: Any) -> list[str]:
    errors: list[str] = []
    fpath = finding.get("file")
    if isinstance(fpath, str) and not _is_repo_relative(fpath):
        errors.append(f"Finding file path must be repository-relative: {fid or fpath}")
    errors.extend(
        f"Finding file path must be repository-relative: {fid or extra_path}"
        for extra_path in finding.get("paths", [])
        if isinstance(extra_path, str) and not _is_repo_relative(extra_path)
    )
    return errors


def _record_unique_id(
    fid: str, seen_ids: set[str], dup_ids: set[str], errors: list[str]
) -> None:
    if fid in seen_ids and fid not in dup_ids:
        errors.append(f"Duplicate IDs in findings: {fid}")
        dup_ids.add(fid)
    seen_ids.add(fid)


def _check_review_findings(findings: Any) -> tuple[list[str], list[str]]:
    seen_ids: set[str] = set()
    dup_ids: set[str] = set()
    blocking_findings: list[str] = []
    errors: list[str] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        fid = finding.get("id")
        if isinstance(fid, str):
            _record_unique_id(fid, seen_ids, dup_ids, errors)
        if isinstance(fid, str) and finding.get("severity") in ("critical", "high"):
            blocking_findings.append(fid)
        errors.extend(_check_finding_paths(finding, fid))
    return errors, blocking_findings


def _check_verdict_alignment(verdict: Any, blocking_findings: list[str]) -> list[str]:
    if verdict == "approve" and blocking_findings:
        return [
            f"Verdict 'approve' conflicts with critical/high findings: {', '.join(blocking_findings)}"
        ]
    return []


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    errors.extend(_check_top_and_coverage_paths(data))
    finding_errors, blocking_findings = _check_review_findings(data.get("findings", []))
    errors.extend(finding_errors)
    errors.extend(_check_verdict_alignment(data.get("verdict"), blocking_findings))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/review.schema.json",
    )
    a = parser.parse_args()
    try:
        errors = validate(
            json.loads(a.artifact.read_text(encoding="utf-8")),
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
        "PASS: review structure, relative file paths, and verdict consistency; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

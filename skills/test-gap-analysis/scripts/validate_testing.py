#!/usr/bin/env python3
"""Validate automated-testing artifact structure and test-case ID uniqueness."""

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


def _check_component_path(component: Any) -> list[str]:
    if (
        isinstance(component, str)
        and ("/" in component or "\\" in component)
        and not _is_repo_relative(component)
    ):
        return [
            f"Component path must be repository-relative when path-formatted: {component}"
        ]
    return []


def _check_path_list(paths: Any, label: str) -> list[str]:
    return [
        f"Deliverable path must be repository-relative ({label}): {raw_path}"
        for raw_path in paths
        if isinstance(raw_path, str) and not _is_repo_relative(raw_path)
    ]


def _check_top_and_coverage_paths(data: Mapping[str, Any]) -> list[str]:
    errors = _check_component_path(data.get("component"))
    errors.extend(_check_path_list(data.get("paths", []), "paths"))
    for cov in data.get("coverage", []):
        if isinstance(cov, Mapping):
            errors.extend(_check_path_list(cov.get("paths", []), "coverage.paths"))
    return errors


def _check_test_case_paths(tc: Mapping[str, Any], tc_id: Any) -> list[str]:
    prefix = tc_id or "test_case"
    errors: list[str] = []
    tc_file = tc.get("file")
    if isinstance(tc_file, str) and not _is_repo_relative(tc_file):
        errors.append(
            f"Deliverable path must be repository-relative ({prefix}.file): {tc_file}"
        )
    errors.extend(_check_path_list(tc.get("paths", []), f"{prefix}.paths"))
    return errors


def _record_unique_id(
    tc_id: str, seen_ids: set[str], dup_ids: set[str], errors: list[str]
) -> None:
    if tc_id in seen_ids and tc_id not in dup_ids:
        errors.append(f"Duplicate IDs in test_cases: {tc_id}")
        dup_ids.add(tc_id)
    seen_ids.add(tc_id)


def _check_test_cases(test_cases: Any) -> list[str]:
    seen_ids: set[str] = set()
    dup_ids: set[str] = set()
    errors: list[str] = []
    for tc in test_cases:
        if not isinstance(tc, Mapping):
            continue
        tc_id = tc.get("id")
        if isinstance(tc_id, str):
            _record_unique_id(tc_id, seen_ids, dup_ids, errors)
        errors.extend(_check_test_case_paths(tc, tc_id))
    return errors


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    errors.extend(_check_top_and_coverage_paths(data))
    errors.extend(_check_test_cases(data.get("test_cases", [])))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/testing.schema.json",
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
        "PASS: testing structure, component target, and test_cases IDs; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

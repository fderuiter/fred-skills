#!/usr/bin/env python3
"""Validate spec-driven-development artifact structure, scope paths, and convergence checks."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any


def _is_repo_relative_scope(raw_path: str) -> bool:
    cleaned = raw_path.strip()
    if not cleaned or "\\" in cleaned:
        return False
    posix = pathlib.PurePosixPath(cleaned)
    win = pathlib.PureWindowsPath(cleaned)
    if posix.is_absolute() or win.is_absolute():
        return False
    return bool(posix.parts) and ".." not in posix.parts and posix.parts != (".",)


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


def _check_field_unique_ids(items: Any, field: str) -> list[str]:
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    duplicates: set[str] = set()
    errors: list[str] = []
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
    return errors


def _check_unique_ids(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("interview_decisions", "requirements", "convergence_checks"):
        errors.extend(_check_field_unique_ids(data.get(field), field))
    return errors


def _check_scope_paths(scopes: Any) -> list[str]:
    return [
        f"Specification file scope must be repository-relative: {scope_path}"
        for scope_path in scopes
        if isinstance(scope_path, str) and not _is_repo_relative_scope(scope_path)
    ]


def _check_convergence_status(data: Mapping[str, Any]) -> list[str]:
    if data.get("status") != "converged":
        return []
    non_aligned = [
        str(chk.get("id", "unknown"))
        for chk in data.get("convergence_checks", [])
        if isinstance(chk, Mapping) and chk.get("status") != "aligned"
    ]
    if non_aligned:
        return [
            f"Status 'converged' conflicts with non-aligned convergence_checks: {', '.join(non_aligned)}"
        ]
    return []


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    errors.extend(_check_unique_ids(data))
    errors.extend(_check_scope_paths(data.get("new_file_scopes", [])))
    errors.extend(_check_convergence_status(data))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1] / "assets/spec.schema.json",
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
        "PASS: specification structure, unique IDs, relative scopes, and convergence status; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

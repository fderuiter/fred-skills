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


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError as e:
        raise RuntimeError(
            "Dependency missing: jsonschema. Use an approved environment with jsonschema; no validation performed."
        ) from e
    errors = [
        (("/".join(map(str, e.absolute_path)) or "root") + ": " + e.message)
        for e in jsonschema.Draft202012Validator(schema).iter_errors(data)
    ]
    if errors or not isinstance(data, Mapping):
        return errors

    for field in ("interview_decisions", "requirements", "convergence_checks"):
        items = data.get(field)
        if not isinstance(items, list):
            continue
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str):
                if item_id in seen and item_id not in duplicates:
                    errors.append(f"Duplicate IDs in {field}: {item_id}")
                    duplicates.add(item_id)
                seen.add(item_id)

    for scope_path in data.get("new_file_scopes", []):
        if isinstance(scope_path, str) and not _is_repo_relative_scope(scope_path):
            errors.append(
                f"Specification file scope must be repository-relative: {scope_path}"
            )

    if data.get("status") == "converged":
        non_aligned = [
            str(chk.get("id", "unknown"))
            for chk in data.get("convergence_checks", [])
            if isinstance(chk, Mapping) and chk.get("status") != "aligned"
        ]
        if non_aligned:
            errors.append(
                f"Status 'converged' conflicts with non-aligned convergence_checks: {', '.join(non_aligned)}"
            )

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

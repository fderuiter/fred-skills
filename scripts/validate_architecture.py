#!/usr/bin/env python3
"""Validate software-architecture artifact structure and cross-record uniqueness."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any


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

    seen_boundaries: set[str] = set()
    dup_boundaries: set[str] = set()
    for boundary in data.get("boundaries", []):
        if not isinstance(boundary, Mapping):
            continue
        b_name = boundary.get("name")
        if isinstance(b_name, str):
            if b_name in seen_boundaries and b_name not in dup_boundaries:
                errors.append(f"Duplicate names in boundaries: {b_name}")
                dup_boundaries.add(b_name)
            seen_boundaries.add(b_name)

    seen_decisions: set[str] = set()
    dup_decisions: set[str] = set()
    for decision in data.get("decisions", []):
        if not isinstance(decision, Mapping):
            continue
        did = decision.get("id")
        if isinstance(did, str):
            if did in seen_decisions and did not in dup_decisions:
                errors.append(f"Duplicate IDs in decisions: {did}")
                dup_decisions.add(did)
            seen_decisions.add(did)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/architecture.schema.json",
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
        "PASS: architecture structure, boundary names, and decision IDs; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

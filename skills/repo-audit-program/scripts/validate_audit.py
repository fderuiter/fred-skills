#!/usr/bin/env python3
"""Validate audit structure and cross-record consistency, not correctness of claims."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

sys.dont_write_bytecode = True

_VALIDATOR_MODULE: ModuleType | None = None


def _load_validator() -> ModuleType:
    global _VALIDATOR_MODULE
    if _VALIDATOR_MODULE is not None:
        return _VALIDATOR_MODULE
    candidate = pathlib.Path(__file__).resolve().parent / "validate_output.py"
    if not candidate.is_file():
        candidate = (
            pathlib.Path(__file__).resolve().parents[3] / "scripts/validate_output.py"
        )
    spec = importlib.util.spec_from_file_location(
        "fred_stdlib_validate_output", candidate
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load stdlib validator from {candidate}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    _VALIDATOR_MODULE = module
    return module


def validate(data: Any, schema: dict[str, Any] | bool) -> list[str]:
    validator = _load_validator()
    result: list[str] = validator.validate(data, schema, check_records=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/audit.schema.json",
    )
    args = parser.parse_args(argv)
    try:
        errors = validate(
            json.loads(args.audit.read_text(encoding="utf-8")),
            json.loads(args.schema.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError, TypeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
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
    raise SystemExit(main())

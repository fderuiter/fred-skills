#!/usr/bin/env python3
"""Validate skill JSON outputs against Draft 2020-12 JSON Schemas using only Python 3.12 stdlib."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.dont_write_bytecode = True

ACTIONABLE_DEFECT = {"confirmed", "supported", "hypothesis"}

_KNOWN_SCHEMA_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "default",
        "examples",
        "$defs",
        "$ref",
        "type",
        "const",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "items",
        "properties",
        "required",
        "additionalProperties",
        "minProperties",
        "maxProperties",
        "anyOf",
        "oneOf",
        "allOf",
    }
)


def _format_path(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "root"


def _is_json_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_json_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value) and value.is_integer()


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return _is_json_integer(value)
    if expected == "number":
        return _is_json_number(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise ValueError(f"Unsupported JSON Schema type: {expected!r}")


def _json_equals(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_json_number(left) and _is_json_number(right):
        return bool(left == right)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equals(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equals(left[k], right[k]) for k in left
        )
    return bool(left == right)


def _resolve_ref(ref: str, root_schema: dict[str, Any] | bool) -> dict[str, Any] | bool:
    if not ref.startswith("#"):
        raise ValueError(f"Unsupported external JSON Schema $ref: {ref!r}")
    if ref == "#":
        return root_schema
    if not ref.startswith("#/"):
        raise ValueError(f"Malformed JSON Pointer $ref: {ref!r}")
    current: Any = root_schema
    for raw_token in ref[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            idx = int(token)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                raise ValueError(f"Unresolvable JSON Schema $ref index: {ref!r}")
        else:
            raise ValueError(f"Unresolvable JSON Schema $ref: {ref!r}")
    if not isinstance(current, (dict, bool)):
        raise TypeError(f"Invalid target for JSON Schema $ref: {ref!r}")
    return current


def _validate_numeric(
    value: Any, schema: dict[str, Any], label: str, errors: list[str]
) -> None:
    if not _is_json_number(value):
        return
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(
            f"{label}: {value!r} is less than the minimum of {schema['minimum']!r}"
        )
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(
            f"{label}: {value!r} is greater than the maximum of {schema['maximum']!r}"
        )
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        errors.append(
            f"{label}: {value!r} is less than or equal to the minimum of {schema['exclusiveMinimum']!r}"
        )
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        errors.append(
            f"{label}: {value!r} is greater than or equal to the maximum of {schema['exclusiveMaximum']!r}"
        )


def _validate_string(
    value: Any, schema: dict[str, Any], label: str, errors: list[str]
) -> None:
    if not isinstance(value, str):
        return
    if "minLength" in schema and len(value) < schema["minLength"]:
        if schema["minLength"] == 1 and value == "":
            errors.append(f"{label}: {value!r} should be non-empty")
        else:
            errors.append(f"{label}: {value!r} is too short")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{label}: {value!r} is too long")
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        errors.append(f"{label}: {value!r} does not match {schema['pattern']!r}")


def _validate_array(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | bool,
    path: tuple[str, ...],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, list):
        return
    if "minItems" in schema and len(value) < schema["minItems"]:
        if schema["minItems"] == 1 and len(value) == 0:
            errors.append(f"{label}: {value!r} should be non-empty")
        else:
            errors.append(f"{label}: {value!r} is too short")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{label}: {value!r} is too long")
    if schema.get("uniqueItems") is True:
        for idx, elem in enumerate(value):
            if any(_json_equals(elem, prev) for prev in value[:idx]):
                errors.append(f"{label}: {value!r} has non-unique elements")
                break
    if "items" in schema:
        item_schema = schema["items"]
        for idx, elem in enumerate(value):
            _validate_node(elem, item_schema, root_schema, (*path, str(idx)), errors)


def _validate_object(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | bool,
    path: tuple[str, ...],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        return
    if "minProperties" in schema and len(value) < schema["minProperties"]:
        errors.append(f"{label}: {value!r} does not have enough properties")
    if "maxProperties" in schema and len(value) > schema["maxProperties"]:
        errors.append(f"{label}: {value!r} has too many properties")
    for req_key in schema.get("required", ()):
        if req_key not in value:
            errors.append(f"{label}: {req_key!r} is a required property")
    props = schema.get("properties", {})
    for prop_key, prop_schema in props.items():
        if prop_key in value:
            _validate_node(
                value[prop_key], prop_schema, root_schema, (*path, prop_key), errors
            )
    if "additionalProperties" in schema:
        add_props = schema["additionalProperties"]
        extra_keys = [key for key in value if key not in props]
        if add_props is False and extra_keys:
            quoted = ", ".join(repr(key) for key in extra_keys)
            verb = "was" if len(extra_keys) == 1 else "were"
            errors.append(
                f"{label}: Additional properties are not allowed ({quoted} {verb} unexpected)"
            )
        elif isinstance(add_props, (dict, bool)) and add_props is not True:
            for extra_key in extra_keys:
                _validate_node(
                    value[extra_key], add_props, root_schema, (*path, extra_key), errors
                )


def _validate_combinators(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | bool,
    path: tuple[str, ...],
    label: str,
    errors: list[str],
) -> None:
    if "allOf" in schema:
        for subschema in schema["allOf"]:
            _validate_node(value, subschema, root_schema, path, errors)
    if "anyOf" in schema:
        matched = False
        for subschema in schema["anyOf"]:
            branch_errors: list[str] = []
            _validate_node(value, subschema, root_schema, path, branch_errors)
            if not branch_errors:
                matched = True
                break
        if not matched:
            errors.append(
                f"{label}: {value!r} is not valid under any of the given schemas"
            )
    if "oneOf" in schema:
        valid_subschemas: list[Any] = []
        for subschema in schema["oneOf"]:
            branch_errors = []
            _validate_node(value, subschema, root_schema, path, branch_errors)
            if not branch_errors:
                valid_subschemas.append(subschema)
        if not valid_subschemas:
            errors.append(
                f"{label}: {value!r} is not valid under any of the given schemas"
            )
        elif len(valid_subschemas) > 1:
            joined = ", ".join(repr(sub) for sub in valid_subschemas)
            errors.append(f"{label}: {value!r} is valid under each of {joined}")


def _validate_node(
    value: Any,
    schema: dict[str, Any] | bool,
    root_schema: dict[str, Any] | bool,
    path: tuple[str, ...],
    errors: list[str],
    seen_refs: frozenset[str] = frozenset(),
) -> None:
    label = _format_path(path)
    if isinstance(schema, bool):
        if not schema:
            errors.append(f"{label}: {value!r} is not allowed")
        return
    if not isinstance(schema, dict):
        raise TypeError(f"Invalid JSON Schema node at {label}: {schema!r}")
    unknown = set(schema) - _KNOWN_SCHEMA_KEYWORDS
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise ValueError(f"Unsupported JSON Schema keyword(s) at {label}: {joined}")

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen_refs:
            raise ValueError(f"Cyclic JSON Schema $ref detected: {ref!r}")
        target = _resolve_ref(ref, root_schema)
        _validate_node(value, target, root_schema, path, errors, seen_refs | {ref})

    if "type" in schema:
        expected_type = schema["type"]
        if isinstance(expected_type, str):
            if not _matches_type(value, expected_type):
                errors.append(f"{label}: {value!r} is not of type {expected_type!r}")
        elif isinstance(expected_type, list):
            if not any(_matches_type(value, item) for item in expected_type):
                joined_types = ", ".join(repr(item) for item in expected_type)
                errors.append(f"{label}: {value!r} is not of type {joined_types}")
        else:
            raise ValueError(
                f"Invalid JSON Schema type specification: {expected_type!r}"
            )

    if "const" in schema and not _json_equals(value, schema["const"]):
        errors.append(f"{label}: {schema['const']!r} was expected")

    if "enum" in schema and not any(
        _json_equals(value, option) for option in schema["enum"]
    ):
        errors.append(f"{label}: {value!r} is not one of {schema['enum']!r}")

    _validate_numeric(value, schema, label, errors)
    _validate_string(value, schema, label, errors)
    _validate_array(value, schema, root_schema, path, label, errors)
    _validate_object(value, schema, root_schema, path, label, errors)
    _validate_combinators(value, schema, root_schema, path, label, errors)


def validate_schema(data: Any, schema: dict[str, Any] | bool) -> list[str]:
    """Validate JSON data against a Draft 2020-12 JSON Schema using stdlib only."""
    errors: list[str] = []
    _validate_node(data, schema, schema, (), errors)
    return errors


def validate_audit_ledger(data: dict[str, Any]) -> list[str]:
    """Run cross-record semantic checks on a schema-valid audit.json ledger."""
    errors: list[str] = []
    maps: dict[str, dict[str, dict[str, Any]]] = {}
    for field in ("evidence", "contracts", "findings", "issues"):
        seen: set[str] = set()
        duplicates: set[str] = set()
        for record in data.get(field, []):
            item_id = record.get("id")
            if item_id is not None:
                if item_id in seen and item_id not in duplicates:
                    errors.append(f"Duplicate IDs in {field}: {item_id}")
                    duplicates.add(item_id)
                seen.add(item_id)
        maps[field] = {
            record["id"]: record for record in data.get(field, []) if "id" in record
        }

    def check_refs(record: dict[str, Any], field: str, target: str) -> None:
        rid = record.get("id") or record.get("topic") or "unknown"
        for value in record.get(field, []):
            if value not in maps.get(target, {}):
                errors.append(f"Unknown {target} reference {value} in {rid} ({field})")

    for ev in data.get("evidence", []):
        if ev["line_end"] < ev["line_start"]:
            errors.append(f"Reversed lines: {ev['id']}")
        pos_path = PurePosixPath(ev["path"].replace("\\", "/"))
        if (
            pos_path.is_absolute()
            or ".." in pos_path.parts
            or (pos_path.parts and ":" in pos_path.parts[0])
        ):
            errors.append(f"Evidence path must be repository-relative: {ev['id']}")
    for contract in data.get("contracts", []):
        check_refs(contract, "evidence_ids", "evidence")
    for cov in data.get("coverage", []):
        check_refs(cov, "evidence_ids", "evidence")
    for finding in data.get("findings", []):
        check_refs(finding, "evidence_ids", "evidence")
        check_refs(finding, "contract_ids", "contracts")
        check_refs(finding, "related_findings", "findings")
        if finding["status"] in ("confirmed", "supported") and (
            not finding["evidence_ids"] or not finding["contract_ids"]
        ):
            errors.append(f"Actionable defect lacks contract/evidence: {finding['id']}")
        if finding["status"] == "confirmed" and (
            finding["validation"]["state"] != "executed"
            or not finding["validation"]["commands"]
        ):
            errors.append(f"Confirmed finding lacks executed command: {finding['id']}")
        if finding["status"] == "refuted" and finding["severity"] != "info":
            errors.append(f"Refuted candidate must be info: {finding['id']}")
    for issue in data.get("issues", []):
        check_refs(issue, "finding_ids", "findings")
        check_refs(issue, "depends_on", "issues")
        for fid in issue["finding_ids"]:
            if fid in maps["findings"] and maps["findings"][fid]["status"] == "refuted":
                errors.append(f"Issue includes refuted finding: {issue['id']}")
        if issue["publication"] in ("created", "linked") and not issue["existing_url"]:
            errors.append(f"Published issue lacks verified URL: {issue['id']}")

    visiting: set[str] = set()
    done: set[str] = set()

    def visit(iid: str) -> None:
        if iid in visiting:
            errors.append(f"Issue dependency cycle: {iid}")
            return
        if iid in done or iid not in maps["issues"]:
            return
        visiting.add(iid)
        for dep in maps["issues"][iid]["depends_on"]:
            visit(dep)
        visiting.remove(iid)
        done.add(iid)

    for iid in maps.get("issues", {}):
        visit(iid)
    return errors


def validate(
    data: Any,
    schema: dict[str, Any] | bool,
    *,
    check_records: bool = False,
) -> list[str]:
    """Validate JSON output against schema and optionally run cross-record ledger checks."""
    errors = validate_schema(data, schema)
    if errors or not check_records or not isinstance(data, dict):
        return errors
    return validate_audit_ledger(data)


def resolve_default_schema(skill_dir: Path, payload_path: Path, data: Any) -> Path:
    """Resolve the appropriate output schema from a skill's assets/ directory."""
    assets_dir = skill_dir / "assets"
    candidates = sorted(assets_dir.glob("*.schema.json")) if assets_dir.is_dir() else []
    if not candidates:
        fallback = (
            skill_dir.parent / "shared/repo-audit/resources/assets/audit.schema.json"
        )
        if fallback.is_file():
            return fallback
        raise FileNotFoundError(f"No output schema found under {assets_dir}")
    if len(candidates) == 1:
        return candidates[0]

    by_name = {candidate.name: candidate for candidate in candidates}
    stem = payload_path.name
    if stem == "audit.json" and "audit.schema.json" in by_name:
        return by_name["audit.schema.json"]
    if isinstance(data, dict) and "run_id" in data and "audit.schema.json" in by_name:
        return by_name["audit.schema.json"]
    skill_specific = [c for c in candidates if c.name != "audit.schema.json"]
    return skill_specific[0] if skill_specific else candidates[0]


def main(argv: list[str] | None = None) -> int:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_json", type=Path, help="Path to JSON output artifact")
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Explicit path to Draft 2020-12 JSON Schema (defaults to local assets/*.schema.json)",
    )
    parser.add_argument(
        "--check-ledger",
        action="store_true",
        help="Also enforce cross-record audit.json ledger invariants",
    )
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.output_json.read_text(encoding="utf-8"))
        schema_path = (
            args.schema
            if args.schema is not None
            else resolve_default_schema(package_dir, args.output_json, data)
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to read JSON input: {exc}", file=sys.stderr)
        return 1

    try:
        errors = validate(data, schema, check_records=args.check_ledger)
    except (ValueError, TypeError) as exc:
        print(f"schema error: {exc}", file=sys.stderr)
        return 1

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate spec-driven-development artifact structure, scope paths, and convergence checks."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any

_KNOWN_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "maxLength",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
_VALID_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "boolean", "integer", "number", "null"}
)


def _is_json_number(val: Any) -> bool:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


def _is_json_int(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return True
    return isinstance(val, float) and math.isfinite(val) and val.is_integer()


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if _is_json_number(left) and _is_json_number(right):
        return bool(left == right)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _json_equal(left[k], right[k]) for k in left
        )
    return type(left) is type(right) and bool(left == right)


def _step_pointer(current: Any, raw_token: str, ref: str) -> Any:
    token = raw_token.replace("~1", "/").replace("~0", "~")
    if isinstance(current, Mapping) and token in current:
        return current[token]
    raise ValueError(f"Unresolvable JSON Schema $ref: {ref!r}")


def _resolve_local_ref(ref: Any, root_schema: Mapping[str, Any]) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#"):
        raise ValueError(f"Unsupported non-local JSON Schema $ref: {ref!r}")
    if ref == "#":
        return root_schema
    if not ref.startswith("#/"):
        raise ValueError(f"Unsupported JSON Schema $ref format: {ref!r}")
    target: Any = root_schema
    for part in ref[2:].split("/"):
        target = _step_pointer(target, part, ref)
    return target


def _validate_type_keyword(expected: Any) -> None:
    if isinstance(expected, str) and expected in _VALID_SCHEMA_TYPES:
        return
    if (
        isinstance(expected, list)
        and expected
        and all(isinstance(t, str) and t in _VALID_SCHEMA_TYPES for t in expected)
    ):
        return
    raise ValueError(f"Invalid JSON Schema 'type': {expected!r}")


def _validate_pattern_keyword(pattern: Any) -> None:
    if not isinstance(pattern, str):
        raise TypeError("Invalid JSON Schema 'pattern': expected string")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid JSON Schema regex pattern: {exc}") from exc


def _validate_scalar_keywords(node: Mapping[str, Any]) -> None:
    if "type" in node:
        _validate_type_keyword(node["type"])
    if "required" in node and (
        not isinstance(node["required"], list)
        or not all(isinstance(k, str) for k in node["required"])
    ):
        raise ValueError("Invalid JSON Schema 'required': expected string array")
    if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
        raise ValueError("Invalid JSON Schema 'enum': expected non-empty array")
    for int_key in ("minItems", "minLength", "maxLength"):
        if int_key in node and (not _is_json_int(node[int_key]) or node[int_key] < 0):
            raise ValueError(
                f"Invalid JSON Schema {int_key!r}: expected non-negative integer"
            )
    if "minimum" in node and not _is_json_number(node["minimum"]):
        raise ValueError("Invalid JSON Schema 'minimum': expected number")
    if "pattern" in node:
        _validate_pattern_keyword(node["pattern"])


def _validate_schema_dict_children(
    mapping: Any, root: Mapping[str, Any], label: str
) -> None:
    if not isinstance(mapping, Mapping):
        raise TypeError(f"Invalid JSON Schema {label}: expected object")
    for child in mapping.values():
        _validate_schema_node(child, root)


def _validate_schema_list_children(
    items: Any, root: Mapping[str, Any], label: str
) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError(f"Invalid JSON Schema {label}: expected non-empty array")
    for child in items:
        _validate_schema_node(child, root)


def _validate_schema_subtrees(node: Mapping[str, Any], root: Mapping[str, Any]) -> None:
    for dict_key in ("$defs", "properties"):
        if dict_key in node:
            _validate_schema_dict_children(node[dict_key], root, dict_key)
    for node_key in ("additionalProperties", "items"):
        if node_key in node:
            _validate_schema_node(node[node_key], root)
    for list_key in ("anyOf", "oneOf"):
        if list_key in node:
            _validate_schema_list_children(node[list_key], root, list_key)


def _validate_schema_node(node: Any, root: Mapping[str, Any]) -> None:
    if isinstance(node, bool):
        return
    if not isinstance(node, Mapping):
        raise TypeError(f"Invalid JSON Schema node: {node!r}")
    unknown = sorted(set(node.keys()) - _KNOWN_SCHEMA_KEYS)
    if unknown:
        raise ValueError(f"Unsupported JSON Schema keyword(s): {', '.join(unknown)}")
    if "$ref" in node:
        _resolve_local_ref(node["$ref"], root)
    _validate_scalar_keywords(node)
    _validate_schema_subtrees(node, root)


def _format_loc(path: tuple[str | int, ...]) -> str:
    return "/".join(map(str, path)) or "root"


def _matches_single_type(val: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(val, Mapping)
    if expected == "array":
        return isinstance(val, list)
    if expected == "string":
        return isinstance(val, str)
    if expected == "boolean":
        return isinstance(val, bool)
    if expected == "integer":
        return _is_json_int(val)
    if expected == "number":
        return _is_json_number(val)
    return val is None


def _check_schema_type(val: Any, expected: Any, loc: str) -> list[str]:
    if isinstance(expected, str):
        ok = _matches_single_type(val, expected)
    else:
        ok = any(_matches_single_type(val, t) for t in expected)
    return [] if ok else [f"{loc}: {val!r} is not of type {expected!r}"]


def _eval_string_constraints(
    val: str, schema: Mapping[str, Any], loc: str
) -> list[str]:
    errors: list[str] = []
    if "minLength" in schema and len(val) < schema["minLength"]:
        errors.append(f"{loc}: {val!r} is too short")
    if "maxLength" in schema and len(val) > schema["maxLength"]:
        errors.append(f"{loc}: {val!r} is too long")
    if "pattern" in schema and re.search(schema["pattern"], val) is None:
        errors.append(f"{loc}: {val!r} does not match {schema['pattern']!r}")
    return errors


def _eval_scalar_constraints(
    val: Any, schema: Mapping[str, Any], loc: str
) -> list[str]:
    errors: list[str] = []
    if "type" in schema:
        errors.extend(_check_schema_type(val, schema["type"], loc))
    if "const" in schema and not _json_equal(val, schema["const"]):
        errors.append(f"{loc}: {schema['const']!r} was expected")
    if "enum" in schema and not any(_json_equal(val, item) for item in schema["enum"]):
        errors.append(f"{loc}: {val!r} is not one of {schema['enum']!r}")
    if isinstance(val, str):
        errors.extend(_eval_string_constraints(val, schema, loc))
    if _is_json_number(val) and "minimum" in schema and val < schema["minimum"]:
        errors.append(
            f"{loc}: {val!r} is less than the minimum of {schema['minimum']!r}"
        )
    return errors


def _eval_array_constraints(
    val: list[Any],
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[str]:
    errors: list[str] = []
    if "minItems" in schema and len(val) < schema["minItems"]:
        errors.append(f"{_format_loc(path)}: {val!r} is too short")
    if "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(val):
            errors.extend(
                _eval_schema(item, item_schema, root, (*path, idx), frozenset())
            )
    return errors


def _eval_additional_props(
    val: Mapping[str, Any],
    unexpected: list[str],
    addl: Any,
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[str]:
    if not unexpected or addl is True:
        return []
    if addl is False:
        verb = "was" if len(unexpected) == 1 else "were"
        joined = ", ".join(repr(k) for k in unexpected)
        return [
            f"{_format_loc(path)}: Additional properties are not allowed ({joined} {verb} unexpected)"
        ]
    errors: list[str] = []
    for key in unexpected:
        errors.extend(_eval_schema(val[key], addl, root, (*path, key), frozenset()))
    return errors


def _eval_object_constraints(
    val: Mapping[str, Any],
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[str]:
    errors: list[str] = []
    loc = _format_loc(path)
    for req_key in schema.get("required", ()):
        if req_key not in val:
            errors.append(f"{loc}: {req_key!r} is a required property")
    props = schema.get("properties", {})
    for prop_key, prop_schema in props.items():
        if prop_key in val:
            errors.extend(
                _eval_schema(
                    val[prop_key], prop_schema, root, (*path, prop_key), frozenset()
                )
            )
    if "additionalProperties" in schema:
        unexpected = [k for k in val if k not in props]
        errors.extend(
            _eval_additional_props(
                val, unexpected, schema["additionalProperties"], root, path
            )
        )
    return errors


def _eval_combinators(
    val: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
) -> list[str]:
    errors: list[str] = []
    loc = _format_loc(path)
    if "anyOf" in schema and not any(
        not _eval_schema(val, sub, root, path, frozenset()) for sub in schema["anyOf"]
    ):
        errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
    if "oneOf" in schema:
        matches = sum(
            not _eval_schema(val, sub, root, path, frozenset())
            for sub in schema["oneOf"]
        )
        if matches == 0:
            errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
        elif matches > 1:
            errors.append(
                f"{loc}: {val!r} is valid under more than one of the given schemas"
            )
    return errors


def _eval_schema(
    val: Any,
    schema: Any,
    root: Mapping[str, Any],
    path: tuple[str | int, ...],
    seen_refs: frozenset[str],
) -> list[str]:
    if isinstance(schema, bool):
        return (
            []
            if schema
            else [f"{_format_loc(path)}: False schema does not allow {val!r}"]
        )
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen_refs:
            raise ValueError(f"Cyclic JSON Schema $ref: {ref!r}")
        target = _resolve_local_ref(ref, root)
        errors.extend(_eval_schema(val, target, root, path, seen_refs | {ref}))
    errors.extend(_eval_scalar_constraints(val, schema, _format_loc(path)))
    if isinstance(val, list):
        errors.extend(_eval_array_constraints(val, schema, root, path))
    if isinstance(val, Mapping):
        errors.extend(_eval_object_constraints(val, schema, root, path))
    errors.extend(_eval_combinators(val, schema, root, path))
    return errors


def _schema_errors(data: Any, schema: Mapping[str, Any]) -> list[str]:
    if not isinstance(schema, Mapping):
        raise TypeError("JSON Schema root must be a mapping")
    _validate_schema_node(schema, schema)
    return _eval_schema(data, schema, schema, (), frozenset())


def _is_repo_relative_scope(raw_path: str) -> bool:
    cleaned = raw_path.strip()
    if (
        not cleaned
        or "\\" in cleaned
        or cleaned.startswith(("/", "~"))
        or (len(cleaned) >= 2 and cleaned[1] == ":")
    ):
        return False
    parts = pathlib.PurePosixPath(cleaned).parts
    return bool(parts) and not any(seg in {".", "..", ".git"} for seg in parts)


def _scope_segments(raw_entry: Any) -> tuple[str, ...] | None:
    if not isinstance(raw_entry, str):
        return None
    cleaned = raw_entry.strip()
    if not _is_repo_relative_scope(cleaned):
        return None
    return pathlib.PurePosixPath(cleaned).parts


def _segments_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    limit = min(len(left), len(right))
    return bool(limit) and left[:limit] == right[:limit]


def _find_scope_overlap(
    scope_path: str,
    scope_segs: tuple[str, ...],
    exclusions: list[tuple[str, tuple[str, ...]]],
) -> list[str]:
    return [
        f"Overlapping new_file_scopes and out_of_scope: {scope_path!r} conflicts with {excl_raw!r}"
        for excl_raw, excl_segs in exclusions
        if _segments_overlap(scope_segs, excl_segs)
    ]


def _check_scope_overlaps(scopes: Any, out_of_scope: Any) -> list[str]:
    if not isinstance(scopes, list) or not isinstance(out_of_scope, list):
        return []
    exclusions = [
        (item, segs)
        for item in out_of_scope
        if isinstance(item, str) and (segs := _scope_segments(item)) is not None
    ]
    errors: list[str] = []
    for scope_path in scopes:
        segs = _scope_segments(scope_path)
        if isinstance(scope_path, str) and segs is not None:
            errors.extend(_find_scope_overlap(scope_path, segs, exclusions))
    return errors


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
    checks = data.get("convergence_checks")
    if not isinstance(checks, list) or not checks:
        return ["Status 'converged' conflicts with empty convergence_checks"]
    non_aligned = [
        str(chk.get("id", "unknown"))
        for chk in checks
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
    scopes = data.get("new_file_scopes", [])
    errors.extend(_check_scope_paths(scopes))
    errors.extend(_check_scope_overlaps(scopes, data.get("out_of_scope", [])))
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
    except (OSError, TypeError, ValueError) as e:
        print(f"Invalid input: {e}", file=sys.stderr)
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

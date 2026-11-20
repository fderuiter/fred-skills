#!/usr/bin/env python3
"""Validate repository-workflows artifact structure and cross-field invariants."""

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


def _is_repo_relative(raw_path: str) -> bool:
    if (
        not raw_path
        or "\\" in raw_path
        or raw_path.startswith(("/", "~"))
        or (len(raw_path) >= 2 and raw_path[1] == ":")
    ):
        return False
    parts = pathlib.PurePosixPath(raw_path).parts
    return bool(parts) and not any(seg in {".", "..", ".git"} for seg in parts)


def _default_schema_path() -> pathlib.Path:
    assets_dir = pathlib.Path(__file__).resolve().parents[1] / "assets"
    candidates = sorted(
        p
        for p in assets_dir.glob("*.schema.json")
        if not p.name.startswith("evaluation-scenarios")
    )
    if candidates:
        return candidates[0]
    return assets_dir / "workflow.schema.json"


def _check_path_list(items: Any, label: str) -> list[str]:
    return [
        f"Path must be repository-relative ({label}): {item}"
        for item in items
        if isinstance(item, str) and not _is_repo_relative(item)
    ]


def _extract_entry_path(entry: Any, allow_str: bool) -> Any:
    if allow_str and isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        return entry.get("path")
    return None


def _check_entry_paths(entries: Any, label: str, allow_str: bool) -> list[str]:
    errors: list[str] = []
    for entry in entries:
        candidate = _extract_entry_path(entry, allow_str)
        if isinstance(candidate, str) and not _is_repo_relative(candidate):
            errors.append(f"Path must be repository-relative ({label}): {candidate}")
    return errors


def _check_workflow_paths(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    test_file = data.get("test_file")
    if isinstance(test_file, str) and not _is_repo_relative(test_file):
        errors.append(f"Path must be repository-relative (test_file): {test_file}")
    errors.extend(_check_path_list(data.get("file_scopes", []), "file_scopes"))
    errors.extend(
        _check_entry_paths(data.get("file_changes", []), "file_changes", allow_str=True)
    )
    errors.extend(
        _check_entry_paths(
            data.get("diff_summaries", []), "diff_summaries", allow_str=False
        )
    )
    errors.extend(_check_path_list(data.get("touched_files", []), "touched_files"))
    return errors


def _check_commit_prefix(data: Mapping[str, Any]) -> list[str]:
    commit_type = data.get("commit_type")
    commit_subject = data.get("commit_subject")
    if not (isinstance(commit_type, str) and isinstance(commit_subject, str)):
        return []
    commit_scope = data.get("commit_scope")
    if isinstance(commit_scope, str):
        expected_prefixes: tuple[str, ...] = (
            f"{commit_type}({commit_scope}): ",
            f"{commit_type}({commit_scope})!: ",
        )
    else:
        expected_prefixes = (
            f"{commit_type}: ",
            f"{commit_type}!: ",
            f"{commit_type}(",
        )
    if not commit_subject.startswith(expected_prefixes):
        return [
            f"commit_subject must match commit_type/commit_scope prefix ({commit_type}): {commit_subject}"
        ]
    return []


def _check_finding_status(data: Mapping[str, Any]) -> list[str]:
    if data.get("reproduction_status") != "confirmed":
        return []
    errors: list[str] = []
    if data.get("reproduced") is False:
        errors.append("Confirmed finding verification cannot set reproduced to false")
    commands = data.get("commands")
    if commands is not None and len(commands) == 0:
        errors.append(
            "Confirmed finding verification requires non-empty commands when commands field is present"
        )
    return errors


def _task_dependencies(task: Mapping[str, Any]) -> list[str]:
    deps: list[str] = []
    for dep_field in ("blocked_by", "dependencies"):
        deps.extend(dep for dep in task.get(dep_field, []) if isinstance(dep, str))
    return deps


def _check_task_paths(task: Mapping[str, Any], tid: Any) -> list[str]:
    errors: list[str] = []
    for path_field in ("file_scopes", "files"):
        errors.extend(_check_path_list(task.get(path_field, []), f"{tid}.{path_field}"))
    return errors


def _record_unique_task_id(
    tid: str,
    seen_tasks: set[str],
    duplicate_tasks: set[str],
    errors: list[str],
) -> None:
    if tid in seen_tasks and tid not in duplicate_tasks:
        errors.append(f"Duplicate task_id in task_breakdowns: {tid}")
        duplicate_tasks.add(tid)
    seen_tasks.add(tid)


def _index_tasks(
    task_breakdowns: list[Any],
) -> tuple[set[str], dict[str, list[str]], list[str]]:
    seen_tasks: set[str] = set()
    duplicate_tasks: set[str] = set()
    task_deps: dict[str, list[str]] = {}
    errors: list[str] = []
    for task in task_breakdowns:
        if not isinstance(task, Mapping):
            continue
        tid = task.get("task_id")
        if isinstance(tid, str):
            _record_unique_task_id(tid, seen_tasks, duplicate_tasks, errors)
            task_deps[tid] = _task_dependencies(task)
        errors.extend(_check_task_paths(task, tid))
    return seen_tasks, task_deps, errors


def _check_task_references(
    data: Mapping[str, Any],
    seen_tasks: set[str],
    task_deps: Mapping[str, list[str]],
) -> list[str]:
    errors = [
        f"Unknown task dependency {dep} in {tid}"
        for tid, deps in task_deps.items()
        for dep in deps
        if dep not in seen_tasks
    ]
    dep_order = data.get("dependency_order")
    if isinstance(dep_order, list):
        errors.extend(
            f"Unknown task_id in dependency_order: {ordered_id}"
            for ordered_id in dep_order
            if isinstance(ordered_id, str) and ordered_id not in seen_tasks
        )
    return errors


def _visit_task(
    node: str,
    task_deps: Mapping[str, list[str]],
    visiting: set[str],
    done: set[str],
    errors: list[str],
) -> None:
    if node in visiting:
        errors.append(f"Task dependency cycle: {node}")
        return
    if node in done or node not in task_deps:
        return
    visiting.add(node)
    for dep in task_deps[node]:
        _visit_task(dep, task_deps, visiting, done, errors)
    visiting.remove(node)
    done.add(node)


def _find_task_cycles(task_deps: Mapping[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    done: set[str] = set()
    errors: list[str] = []
    for tid in task_deps:
        _visit_task(tid, task_deps, visiting, done, errors)
    return errors


def _check_tasks(data: Mapping[str, Any]) -> list[str]:
    task_breakdowns = data.get("task_breakdowns")
    if not isinstance(task_breakdowns, list):
        return []
    seen_tasks, task_deps, errors = _index_tasks(task_breakdowns)
    errors.extend(_check_task_references(data, seen_tasks, task_deps))
    errors.extend(_find_task_cycles(task_deps))
    return errors


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    errors.extend(_check_workflow_paths(data))
    errors.extend(_check_commit_prefix(data))
    errors.extend(_check_finding_status(data))
    errors.extend(_check_tasks(data))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=_default_schema_path(),
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
        "PASS: workflow artifact structure, paths, and task dependencies; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

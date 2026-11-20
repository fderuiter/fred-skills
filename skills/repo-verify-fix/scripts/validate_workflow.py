#!/usr/bin/env python3
"""Validate repository-workflows artifact structure and cross-field invariants."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
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


def _is_json_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_json_integer(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    return isinstance(val, int) or (isinstance(val, float) and val.is_integer())


_TYPE_PREDICATES: dict[str, Callable[[Any], bool]] = {
    "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    "integer": _is_json_integer,
    "number": _is_json_number,
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, Mapping),
}


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_json_number(left) and _is_json_number(right):
        return bool(left == right)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left.keys()) == set(right.keys()) and all(
            _json_equal(left[k], right[k]) for k in left
        )
    return type(left) is type(right) and bool(left == right)


def _validate_type_keyword(expected: Any) -> None:
    names = [expected] if isinstance(expected, str) else expected
    if not isinstance(names, list) or not names:
        raise ValueError(f"Invalid JSON Schema type specification: {expected!r}")
    for name in names:
        if not isinstance(name, str) or name not in _TYPE_PREDICATES:
            raise ValueError(f"Unsupported JSON Schema type: {name!r}")


def _matches_type(val: Any, expected: Any) -> bool:
    names = [expected] if isinstance(expected, str) else list(expected)
    return any(_TYPE_PREDICATES[name](val) for name in names)


def _resolve_local_ref(ref: Any, root_schema: Mapping[str, Any]) -> Any:
    if ref == "#":
        return root_schema
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f"Unsupported JSON Schema $ref: {ref!r}")
    target: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            raise ValueError(f"Unresolvable JSON Schema $ref: {ref!r}")
        target = target[part]
    return target


def _validate_schema_scalars(node: Mapping[str, Any]) -> None:
    if "type" in node:
        _validate_type_keyword(node["type"])
    if "enum" in node and not isinstance(node["enum"], list):
        raise ValueError("Invalid JSON Schema enum: expected array")
    if "required" in node:
        req = node["required"]
        if not isinstance(req, list) or not all(isinstance(k, str) for k in req):
            raise ValueError("Invalid JSON Schema required: expected string array")
    if "pattern" in node:
        if not isinstance(node["pattern"], str):
            raise ValueError("Invalid JSON Schema pattern: expected string")
        re.compile(node["pattern"])


def _validate_subschema_map(
    mapping_node: Any, label: str, root_schema: Mapping[str, Any]
) -> None:
    if not isinstance(mapping_node, Mapping):
        raise TypeError(f"Invalid JSON Schema {label}: expected object")
    for sub in mapping_node.values():
        _validate_schema_tree(sub, root_schema)


def _validate_subschema_list(
    list_node: Any, label: str, root_schema: Mapping[str, Any]
) -> None:
    if not isinstance(list_node, list) or not list_node:
        raise ValueError(f"Invalid JSON Schema {label}: expected non-empty array")
    for sub in list_node:
        _validate_schema_tree(sub, root_schema)


def _validate_schema_children(
    node: Mapping[str, Any], root_schema: Mapping[str, Any]
) -> None:
    if "$ref" in node:
        _resolve_local_ref(node["$ref"], root_schema)
    for map_key in ("$defs", "properties"):
        if map_key in node:
            _validate_subschema_map(node[map_key], map_key, root_schema)
    for sub_key in ("items", "additionalProperties"):
        if sub_key in node:
            _validate_schema_tree(node[sub_key], root_schema)
    for comb_key in ("anyOf", "oneOf"):
        if comb_key in node:
            _validate_subschema_list(node[comb_key], comb_key, root_schema)


def _validate_schema_tree(node: Any, root_schema: Mapping[str, Any]) -> None:
    if isinstance(node, bool):
        return
    if not isinstance(node, Mapping):
        raise TypeError("Invalid JSON Schema node: expected object or boolean")
    unknown = sorted(set(node.keys()) - _SUPPORTED_SCHEMA_KEYWORDS)
    if unknown:
        raise ValueError(f"Unsupported JSON Schema keyword(s): {', '.join(unknown)}")
    _validate_schema_scalars(node)
    _validate_schema_children(node, root_schema)


def _format_loc(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "root"


def _eval_type_const_enum(val: Any, node: Mapping[str, Any], loc: str) -> list[str]:
    errors: list[str] = []
    if "type" in node and not _matches_type(val, node["type"]):
        errors.append(f"{loc}: {val!r} is not of type {node['type']!r}")
    if "const" in node and not _json_equal(val, node["const"]):
        errors.append(f"{loc}: {node['const']!r} was expected")
    if "enum" in node and not any(_json_equal(val, opt) for opt in node["enum"]):
        errors.append(f"{loc}: {val!r} is not one of {node['enum']!r}")
    if "minimum" in node and _is_json_number(val) and val < node["minimum"]:
        errors.append(f"{loc}: {val!r} is less than the minimum of {node['minimum']!r}")
    return errors


def _eval_string_keywords(val: Any, node: Mapping[str, Any], loc: str) -> list[str]:
    if not isinstance(val, str):
        return []
    errors: list[str] = []
    if "minLength" in node and len(val) < node["minLength"]:
        errors.append(f"{loc}: {val!r} is too short")
    if "maxLength" in node and len(val) > node["maxLength"]:
        errors.append(f"{loc}: {val!r} is too long")
    if "pattern" in node and re.search(node["pattern"], val) is None:
        errors.append(f"{loc}: {val!r} does not match {node['pattern']!r}")
    return errors


def _eval_array_keywords(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if not isinstance(val, list):
        return []
    errors: list[str] = []
    if "minItems" in node and len(val) < node["minItems"]:
        errors.append(f"{_format_loc(path)}: {val!r} is too short")
    if "items" in node:
        item_schema = node["items"]
        for idx, item in enumerate(val):
            errors.extend(
                _eval_schema(
                    item, item_schema, root_schema, (*path, str(idx)), seen_refs
                )
            )
    return errors


def _eval_additional_props(
    val: Mapping[Any, Any],
    add_props: Any,
    defined: set[Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    extras = [k for k in val if k not in defined]
    if not extras or add_props is True:
        return []
    if add_props is False:
        quoted = ", ".join(repr(str(k)) for k in extras)
        verb = "was" if len(extras) == 1 else "were"
        return [
            f"{_format_loc(path)}: Additional properties are not allowed ({quoted} {verb} unexpected)"
        ]
    errors: list[str] = []
    for key in extras:
        errors.extend(
            _eval_schema(val[key], add_props, root_schema, (*path, str(key)), seen_refs)
        )
    return errors


def _eval_object_keywords(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if not isinstance(val, Mapping):
        return []
    loc = _format_loc(path)
    props = node.get("properties", {})
    errors = [
        f"{loc}: {req_key!r} is a required property"
        for req_key in node.get("required", [])
        if req_key not in val
    ]
    for prop_key, prop_schema in props.items():
        if prop_key in val:
            errors.extend(
                _eval_schema(
                    val[prop_key],
                    prop_schema,
                    root_schema,
                    (*path, str(prop_key)),
                    seen_refs,
                )
            )
    if "additionalProperties" in node:
        errors.extend(
            _eval_additional_props(
                val,
                node["additionalProperties"],
                set(props.keys()),
                root_schema,
                path,
                seen_refs,
            )
        )
    return errors


def _eval_combinators(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    loc = _format_loc(path)
    errors: list[str] = []
    if "anyOf" in node and not any(
        not _eval_schema(val, sub, root_schema, path, seen_refs)
        for sub in node["anyOf"]
    ):
        errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
    if "oneOf" in node:
        matches = sum(
            1
            for sub in node["oneOf"]
            if not _eval_schema(val, sub, root_schema, path, seen_refs)
        )
        if matches == 0:
            errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
        elif matches > 1:
            errors.append(f"{loc}: {val!r} is valid under multiple given schemas")
    return errors


def _eval_schema(
    val: Any,
    node: Any,
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if isinstance(node, bool):
        return (
            []
            if node
            else [f"{_format_loc(path)}: False schema does not allow {val!r}"]
        )
    errors: list[str] = []
    if "$ref" in node:
        ref = str(node["$ref"])
        ref_token = (path, ref)
        if ref_token in seen_refs:
            raise ValueError(f"Cyclic JSON Schema $ref: {ref!r}")
        target = _resolve_local_ref(ref, root_schema)
        errors.extend(
            _eval_schema(val, target, root_schema, path, seen_refs | {ref_token})
        )
    loc = _format_loc(path)
    errors.extend(_eval_type_const_enum(val, node, loc))
    errors.extend(_eval_string_keywords(val, node, loc))
    errors.extend(_eval_array_keywords(val, node, root_schema, path, seen_refs))
    errors.extend(_eval_object_keywords(val, node, root_schema, path, seen_refs))
    errors.extend(_eval_combinators(val, node, root_schema, path, seen_refs))
    return errors


def _schema_errors(data: Any, schema: Mapping[str, Any]) -> list[str]:
    _validate_schema_tree(schema, schema)
    return _eval_schema(data, schema, schema, (), frozenset())


def _is_repo_relative(raw_path: str) -> bool:
    if not raw_path or "\\" in raw_path:
        return False
    posix = pathlib.PurePosixPath(raw_path)
    win = pathlib.PureWindowsPath(raw_path)
    if posix.is_absolute() or win.is_absolute() or bool(win.drive):
        return False
    return bool(posix.parts) and ".." not in posix.parts and ".git" not in posix.parts


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


def _check_observation_paths(data: Mapping[str, Any]) -> list[str]:
    observations = data.get("observations")
    if not isinstance(observations, list):
        return []
    errors: list[str] = []
    for obs in observations:
        if isinstance(obs, Mapping):
            errors.extend(
                _check_entry_paths(
                    obs.get("locations", []), "observations.locations", allow_str=False
                )
            )
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
    errors.extend(_check_observation_paths(data))
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

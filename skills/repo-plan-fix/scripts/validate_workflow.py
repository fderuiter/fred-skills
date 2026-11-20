#!/usr/bin/env python3
"""Validate repository-workflows artifact structure and cross-field invariants."""

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
    return not (
        not posix.parts
        or posix.is_absolute()
        or win.is_absolute()
        or bool(win.drive)
        or any(part in {"..", ".git"} for part in posix.parts)
    )


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
        "PASS: workflow artifact structure, paths, and task dependencies; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    return not (posix.is_absolute() or win.is_absolute() or ".." in posix.parts)


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

    # 1. Repository-relative path checks across workflow artifacts
    test_file = data.get("test_file")
    if isinstance(test_file, str) and not _is_repo_relative(test_file):
        errors.append(f"Path must be repository-relative (test_file): {test_file}")

    for scope_path in data.get("file_scopes", []):
        if isinstance(scope_path, str) and not _is_repo_relative(scope_path):
            errors.append(
                f"Path must be repository-relative (file_scopes): {scope_path}"
            )

    for entry in data.get("file_changes", []):
        candidate = (
            entry
            if isinstance(entry, str)
            else entry.get("path")
            if isinstance(entry, Mapping)
            else None
        )
        if isinstance(candidate, str) and not _is_repo_relative(candidate):
            errors.append(
                f"Path must be repository-relative (file_changes): {candidate}"
            )

    for diff_entry in data.get("diff_summaries", []):
        if isinstance(diff_entry, Mapping):
            diff_path = diff_entry.get("path")
            if isinstance(diff_path, str) and not _is_repo_relative(diff_path):
                errors.append(
                    f"Path must be repository-relative (diff_summaries): {diff_path}"
                )

    for touched_path in data.get("touched_files", []):
        if isinstance(touched_path, str) and not _is_repo_relative(touched_path):
            errors.append(
                f"Path must be repository-relative (touched_files): {touched_path}"
            )

    commit_type = data.get("commit_type")
    commit_subject = data.get("commit_subject")
    if isinstance(commit_type, str) and isinstance(commit_subject, str):
        commit_scope = data.get("commit_scope")
        expected_prefixes: tuple[str, ...]
        if isinstance(commit_scope, str):
            expected_prefixes = (
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
            errors.append(
                f"commit_subject must match commit_type/commit_scope prefix ({commit_type}): {commit_subject}"
            )

    # 2. Finding verification execution consistency
    if data.get("reproduction_status") == "confirmed":
        if data.get("reproduced") is False:
            errors.append(
                "Confirmed finding verification cannot set reproduced to false"
            )
        commands = data.get("commands")
        if commands is not None and len(commands) == 0:
            errors.append(
                "Confirmed finding verification requires non-empty commands when commands field is present"
            )

    # 3. Implementation plan task ID uniqueness, references, and cycle checks
    task_breakdowns = data.get("task_breakdowns")
    if isinstance(task_breakdowns, list):
        seen_tasks: set[str] = set()
        duplicate_tasks: set[str] = set()
        task_deps: dict[str, list[str]] = {}
        for task in task_breakdowns:
            if not isinstance(task, Mapping):
                continue
            tid = task.get("task_id")
            if isinstance(tid, str):
                if tid in seen_tasks and tid not in duplicate_tasks:
                    errors.append(f"Duplicate task_id in task_breakdowns: {tid}")
                    duplicate_tasks.add(tid)
                seen_tasks.add(tid)
                deps: list[str] = []
                for dep_field in ("blocked_by", "dependencies"):
                    for dep in task.get(dep_field, []):
                        if isinstance(dep, str):
                            deps.append(dep)
                task_deps[tid] = deps
            for path_field in ("file_scopes", "files"):
                for fpath in task.get(path_field, []):
                    if isinstance(fpath, str) and not _is_repo_relative(fpath):
                        errors.append(
                            f"Path must be repository-relative ({tid}.{path_field}): {fpath}"
                        )

        for tid, deps in task_deps.items():
            for dep in deps:
                if dep not in seen_tasks:
                    errors.append(f"Unknown task dependency {dep} in {tid}")

        dep_order = data.get("dependency_order")
        if isinstance(dep_order, list):
            for ordered_id in dep_order:
                if isinstance(ordered_id, str) and ordered_id not in seen_tasks:
                    errors.append(f"Unknown task_id in dependency_order: {ordered_id}")

        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                errors.append(f"Task dependency cycle: {node}")
                return
            if node in done or node not in task_deps:
                return
            visiting.add(node)
            for dep in task_deps[node]:
                visit(dep)
            visiting.remove(node)
            done.add(node)

        for tid in task_deps:
            visit(tid)

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

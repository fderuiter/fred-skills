#!/usr/bin/env python3
"""Validate software-architecture artifact structure and cross-record uniqueness."""

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


def _check_deliverable_paths(paths: Any, label: str) -> list[str]:
    return [
        f"Deliverable path must be repository-relative ({label}): {raw_path}"
        for raw_path in paths
        if isinstance(raw_path, str) and not _is_repo_relative(raw_path)
    ]


def _check_top_and_coverage_paths(data: Mapping[str, Any]) -> list[str]:
    errors = _check_deliverable_paths(data.get("paths", []), "paths")
    for cov in data.get("coverage", []):
        if isinstance(cov, Mapping):
            errors.extend(
                _check_deliverable_paths(cov.get("paths", []), "coverage.paths")
            )
    return errors


def _record_unique_key(
    key: Any,
    seen: set[str],
    duplicates: set[str],
    dup_prefix: str,
    errors: list[str],
) -> None:
    if not isinstance(key, str):
        return
    if key in seen and key not in duplicates:
        errors.append(f"{dup_prefix}: {key}")
        duplicates.add(key)
    seen.add(key)


def _check_keyed_records(
    records: Any, key_field: str, dup_prefix: str, fallback_label: str
) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    errors: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        key = record.get(key_field)
        _record_unique_key(key, seen, duplicates, dup_prefix, errors)
        errors.extend(
            _check_deliverable_paths(
                record.get("paths", []), f"{key or fallback_label}.paths"
            )
        )
    return errors


def _index_components(
    components: Any,
) -> tuple[set[str], dict[str, list[str]], list[str]]:
    seen_components: set[str] = set()
    dup_components: set[str] = set()
    component_deps: dict[str, list[str]] = {}
    errors: list[str] = []
    for component in components:
        if not isinstance(component, Mapping):
            continue
        cid = component.get("id")
        _record_unique_key(
            cid, seen_components, dup_components, "Duplicate IDs in components", errors
        )
        if isinstance(cid, str):
            component_deps[cid] = [
                dep for dep in component.get("depends_on", []) if isinstance(dep, str)
            ]
        errors.extend(
            _check_deliverable_paths(
                component.get("paths", []), f"{cid or 'component'}.paths"
            )
        )
    return seen_components, component_deps, errors


def _visit_component(
    node: str,
    component_deps: Mapping[str, list[str]],
    visiting: set[str],
    done: set[str],
    errors: list[str],
) -> None:
    if node in visiting:
        errors.append(f"Component dependency cycle: {node}")
        return
    if node in done or node not in component_deps:
        return
    visiting.add(node)
    for dep in component_deps[node]:
        _visit_component(dep, component_deps, visiting, done, errors)
    visiting.remove(node)
    done.add(node)


def _check_component_dependencies(
    seen_components: set[str], component_deps: Mapping[str, list[str]]
) -> list[str]:
    errors = [
        f"Unknown component dependency {dep} in {cid}"
        for cid, deps in component_deps.items()
        for dep in deps
        if dep not in seen_components
    ]
    visiting: set[str] = set()
    done: set[str] = set()
    for cid in component_deps:
        _visit_component(cid, component_deps, visiting, done, errors)
    return errors


def _check_components(components: Any) -> list[str]:
    seen_components, component_deps, errors = _index_components(components)
    errors.extend(_check_component_dependencies(seen_components, component_deps))
    return errors


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    errors.extend(_check_top_and_coverage_paths(data))
    errors.extend(
        _check_keyed_records(
            data.get("boundaries", []),
            "name",
            "Duplicate names in boundaries",
            "boundary",
        )
    )
    errors.extend(_check_components(data.get("components", [])))
    errors.extend(
        _check_keyed_records(
            data.get("decisions", []),
            "id",
            "Duplicate IDs in decisions",
            "decision",
        )
    )
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

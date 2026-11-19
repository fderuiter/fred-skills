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

    for top_path in data.get("paths", []):
        if isinstance(top_path, str) and not _is_repo_relative(top_path):
            errors.append(
                f"Deliverable path must be repository-relative (paths): {top_path}"
            )

    for cov in data.get("coverage", []):
        if isinstance(cov, Mapping):
            for cov_path in cov.get("paths", []):
                if isinstance(cov_path, str) and not _is_repo_relative(cov_path):
                    errors.append(
                        f"Deliverable path must be repository-relative (coverage.paths): {cov_path}"
                    )

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
        for b_path in boundary.get("paths", []):
            if isinstance(b_path, str) and not _is_repo_relative(b_path):
                errors.append(
                    f"Deliverable path must be repository-relative ({b_name or 'boundary'}.paths): {b_path}"
                )

    seen_components: set[str] = set()
    dup_components: set[str] = set()
    component_deps: dict[str, list[str]] = {}
    for component in data.get("components", []):
        if not isinstance(component, Mapping):
            continue
        cid = component.get("id")
        if isinstance(cid, str):
            if cid in seen_components and cid not in dup_components:
                errors.append(f"Duplicate IDs in components: {cid}")
                dup_components.add(cid)
            seen_components.add(cid)
            component_deps[cid] = [
                dep for dep in component.get("depends_on", []) if isinstance(dep, str)
            ]
        for c_path in component.get("paths", []):
            if isinstance(c_path, str) and not _is_repo_relative(c_path):
                errors.append(
                    f"Deliverable path must be repository-relative ({cid or 'component'}.paths): {c_path}"
                )

    for cid, deps in component_deps.items():
        for dep in deps:
            if dep not in seen_components:
                errors.append(f"Unknown component dependency {dep} in {cid}")

    visiting: set[str] = set()
    done: set[str] = set()

    def visit_component(node: str) -> None:
        if node in visiting:
            errors.append(f"Component dependency cycle: {node}")
            return
        if node in done or node not in component_deps:
            return
        visiting.add(node)
        for dep in component_deps[node]:
            visit_component(dep)
        visiting.remove(node)
        done.add(node)

    for cid in component_deps:
        visit_component(cid)

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
        for d_path in decision.get("paths", []):
            if isinstance(d_path, str) and not _is_repo_relative(d_path):
                errors.append(
                    f"Deliverable path must be repository-relative ({did or 'decision'}.paths): {d_path}"
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

#!/usr/bin/env python3
"""Validate audit structure and cross-record consistency, not correctness of claims."""

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
    maps: dict[str, dict[str, Mapping[str, Any]]] = {}
    for field in ("evidence", "contracts", "findings", "issues"):
        seen: set[str] = set()
        duplicates: set[str] = set()
        for x in data.get(field, []):
            i = x.get("id")
            if isinstance(i, str):
                if i in seen and i not in duplicates:
                    errors.append(f"Duplicate IDs in {field}: {i}")
                    duplicates.add(i)
                seen.add(i)
        maps[field] = {
            x["id"]: x
            for x in data.get(field, [])
            if isinstance(x, Mapping) and isinstance(x.get("id"), str)
        }

    def refs(record: Mapping[str, Any], field: str, target: str) -> None:
        rid = record.get("id") or record.get("topic") or "unknown"
        for value in record.get(field, []):
            if value not in maps.get(target, {}):
                errors.append(f"Unknown {target} reference {value} in {rid} ({field})")

    for ev in data.get("evidence", []):
        if ev["line_end"] < ev["line_start"]:
            errors.append(f"Reversed lines: {ev['id']}")
        p = pathlib.PurePosixPath(ev["path"])
        win_p = pathlib.PureWindowsPath(ev["path"])
        if (
            p.is_absolute()
            or win_p.is_absolute()
            or ".." in p.parts
            or "\\" in ev["path"]
        ):
            errors.append(f"Evidence path must be repository-relative: {ev['id']}")
    for c in data.get("contracts", []):
        refs(c, "evidence_ids", "evidence")
    for c in data.get("coverage", []):
        refs(c, "evidence_ids", "evidence")
    for f in data.get("findings", []):
        refs(f, "evidence_ids", "evidence")
        refs(f, "contract_ids", "contracts")
        refs(f, "related_findings", "findings")
        if f["status"] in ("confirmed", "supported") and (
            not f["evidence_ids"] or not f["contract_ids"]
        ):
            errors.append(f"Actionable defect lacks contract/evidence: {f['id']}")
        if f["status"] == "confirmed" and (
            f["validation"]["state"] != "executed" or not f["validation"]["commands"]
        ):
            errors.append(f"Confirmed finding lacks executed command: {f['id']}")
        if f["status"] == "refuted" and f["severity"] != "info":
            errors.append(f"Refuted candidate must be info: {f['id']}")
    for issue in data.get("issues", []):
        refs(issue, "finding_ids", "findings")
        refs(issue, "depends_on", "issues")
        for fid in issue["finding_ids"]:
            if fid in maps["findings"] and maps["findings"][fid]["status"] == "refuted":
                errors.append(f"Issue includes refuted finding: {issue['id']}")
        if issue["publication"] in ("created", "linked") and not issue["existing_url"]:
            errors.append(f"Published issue lacks verified URL: {issue['id']}")
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(i: str) -> None:
        if i in visiting:
            errors.append(f"Issue dependency cycle: {i}")
            return
        if i in done or i not in maps["issues"]:
            return
        visiting.add(i)
        for dep in maps["issues"][i]["depends_on"]:
            visit(dep)
        visiting.remove(i)
        done.add(i)

    for i in maps.get("issues", {}):
        visit(i)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/audit.schema.json",
    )
    a = parser.parse_args()
    try:
        errors = validate(
            json.loads(a.audit.read_text(encoding="utf-8")),
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
        "PASS: structure, references, and dependency graph; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

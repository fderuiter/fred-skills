#!/usr/bin/env python3
"""Generate standalone Markdown issue drafts or GitHub REST API JSON payloads from audit.json."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

sys.dont_write_bytecode = True


def _load_validator() -> ModuleType:
    local = Path(__file__).resolve().parent / "validate_output.py"
    if not local.is_file():
        local = Path(__file__).resolve().parents[1] / "scripts/validate_output.py"
    spec = importlib.util.spec_from_file_location("fred_issue_validator", local)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load stdlib validator from {local}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _resolve_audit_schema(explicit_schema: Path | None) -> Path:
    if explicit_schema is not None:
        return explicit_schema
    skill_schema = Path(__file__).resolve().parents[1] / "assets/audit.schema.json"
    if skill_schema.is_file():
        return skill_schema
    repo_schema = (
        Path(__file__).resolve().parents[1]
        / "shared/repo-audit/resources/assets/audit.schema.json"
    )
    if repo_schema.is_file():
        return repo_schema
    raise FileNotFoundError("Could not locate assets/audit.schema.json")


def generate_issue_drafts(
    audit: dict[str, Any],
    output: Path,
    *,
    fmt: str = "markdown",
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate audit.json and write local Markdown or GitHub REST API JSON issue drafts."""
    if fmt not in ("markdown", "json"):
        raise ValueError(f"Unsupported issue format: {fmt}")
    if not isinstance(audit, dict):
        raise TypeError("Invalid ledger; expected a JSON object")

    validator = _load_validator()
    if schema is None:
        schema_path = _resolve_audit_schema(None)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema is not None
    errors: list[str] = validator.validate(audit, schema, check_records=True)
    if errors:
        raise ValueError(
            "Invalid ledger; cannot draft issues: " + "; ".join(errors[:5])
        )

    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(
            "Issue output directory must be empty; stale drafts will not be mixed with current results"
        )

    findings = {finding["id"]: finding for finding in audit["findings"]}
    evidence = {ev["id"]: ev for ev in audit["evidence"]}
    paths: list[str] = []
    for issue in audit["issues"]:
        if issue["publication"] != "draft":
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue["id"]):
            raise ValueError("Issue IDs must be filename-safe")
        sections = [
            f"# {issue['title']}",
            "Publication state: local draft; no external write performed.",
        ]
        for fid in issue["finding_ids"]:
            finding = findings[fid]
            sections.extend(
                [
                    f"## {fid}: {finding['title']}",
                    f"Evidence: {finding['status']}; severity: {finding['severity']}; confidence: {finding['confidence']}",
                    finding["impact"],
                    "### Preconditions",
                    finding["preconditions"],
                    "### Expected and actual behavior",
                    "Expected: " + finding["expected"],
                    "Actual: " + finding["actual"],
                    "### Source evidence",
                    "\n".join(
                        f"- {evidence[eid]['path']}:{evidence[eid]['line_start']}\u2013{evidence[eid]['line_end']} ({evidence[eid]['symbol']}): {evidence[eid]['context']}"
                        for eid in finding["evidence_ids"]
                    ),
                    "### Contract and cause",
                    "Contract IDs: " + ", ".join(finding["contract_ids"]),
                    finding["root_cause"],
                    "### Failure sequence",
                    "\n".join(
                        f"{idx}. {step}"
                        for idx, step in enumerate(finding["sequence"], 1)
                    ),
                    "### Counterevidence",
                    finding["counterevidence"],
                    "### Proposed correction",
                    finding["remedy"],
                    "### Acceptance",
                    "\n".join("- " + item for item in finding["acceptance"]),
                    "### Validation",
                    finding["validation"]["state"]
                    + ": "
                    + finding["validation"]["result"],
                    "\n".join(
                        "    " + command
                        for command in finding["validation"]["commands"]
                    ),
                ]
            )
        sections.append("Dependencies: " + ", ".join(issue["depends_on"]))
        body_text = "\n\n".join(sections)
        if fmt == "json":
            payload = {
                "title": issue["title"],
                "body": body_text,
                "labels": list(issue.get("labels", [])),
                "assignees": list(issue.get("assignees", [])),
            }
            target = output / f"{issue['id']}.json"
            target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        else:
            target = output / f"{issue['id']}.md"
            target.write_text(body_text + "\n", encoding="utf-8")
        paths.append(str(target))
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "audit",
        type=Path,
        help="Path to audit.json file or audit workspace directory containing audit.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty destination directory for drafted issue files",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Draft format: 'markdown' (.md) or GitHub REST API 'json' (.json)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Explicit path to audit.schema.json (defaults to local assets/audit.schema.json)",
    )
    args = parser.parse_args(argv)

    audit_path = args.audit / "audit.json" if args.audit.is_dir() else args.audit
    try:
        raw_audit = json.loads(audit_path.read_text(encoding="utf-8"))
        schema_path = _resolve_audit_schema(args.schema)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        drafts = generate_issue_drafts(
            raw_audit, args.output, fmt=args.format, schema=schema
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"drafts": drafts, "remote_writes": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convert repository audit findings (audit.json) to SARIF v2.1.0 format."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SEVERITY_TO_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def audit_to_sarif(
    data: dict[str, Any],
    *,
    filter_refuted: bool = False,
    filter_info: bool = False,
) -> dict[str, Any]:
    """Convert audit data conforming to audit.schema.json into SARIF v2.1.0."""
    if not isinstance(data, dict):
        raise ValueError("Audit data must be a JSON object")

    evidence_map: dict[str, dict[str, Any]] = {}
    for ev in data.get("evidence", []):
        if isinstance(ev, dict) and "id" in ev:
            evidence_map[ev["id"]] = ev

    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    rule_indices: dict[str, int] = {}

    for finding in data.get("findings", []):
        if not isinstance(finding, dict):
            continue

        status = finding.get("status", "")
        severity = finding.get("severity", "medium")

        if filter_refuted and status == "refuted":
            continue
        if filter_info and severity == "info":
            continue

        finding_id = finding.get("id") or f"FINDING-{len(results) + 1}"
        title = finding.get("title") or finding_id
        topic = finding.get("topic") or "audit"
        sarif_level = SEVERITY_TO_SARIF_LEVEL.get(severity, "warning")
        remedy = finding.get("remedy", "")
        impact = finding.get("impact", "")
        root_cause = finding.get("root_cause", "")
        actual = finding.get("actual", "")

        # Build rule descriptor if not already added
        if finding_id not in rule_indices:
            tags = [t for t in finding.get("tags", []) if isinstance(t, str)]
            if topic and topic not in tags:
                tags.append(topic)

            help_lines: list[str] = []
            if topic:
                help_lines.append(f"Topic: {topic}")
            if title:
                help_lines.append(f"Title: {title}")
            if impact:
                help_lines.append(f"Impact: {impact}")
            if root_cause:
                help_lines.append(f"Root Cause: {root_cause}")
            if remedy:
                help_lines.append(f"Remedy: {remedy}")
            help_text = "\n".join(help_lines)

            markdown_lines: list[str] = [f"### {title}"]
            if topic:
                markdown_lines.append(f"**Topic:** {topic}")
            if impact:
                markdown_lines.append(f"**Impact:** {impact}")
            if root_cause:
                markdown_lines.append(f"**Root Cause:** {root_cause}")
            if remedy:
                markdown_lines.append(f"\n#### Suggested Remedy\n{remedy}")
            help_markdown = "\n".join(markdown_lines)

            rule: dict[str, Any] = {
                "id": finding_id,
                "name": topic or finding_id,
                "shortDescription": {"text": title},
                "defaultConfiguration": {"level": sarif_level},
                "help": {
                    "text": help_text,
                    "markdown": help_markdown,
                },
                "properties": {
                    "id": finding_id,
                    "topic": topic,
                    "severity": severity,
                    "status": status,
                    "confidence": finding.get("confidence", "unknown"),
                    "tags": tags,
                },
            }
            if remedy:
                rule["properties"]["remedy"] = remedy
            if impact:
                rule["fullDescription"] = {"text": impact}
            elif actual:
                rule["fullDescription"] = {"text": actual}

            rule_indices[finding_id] = len(rules)
            rules.append(rule)

        rule_index = rule_indices[finding_id]

        # Map locations from evidence
        locations: list[dict[str, Any]] = []
        for eid in finding.get("evidence_ids", []):
            ev = evidence_map.get(eid)
            if not ev or not isinstance(ev, dict):
                continue
            path_str = ev.get("path")
            if not path_str or not isinstance(path_str, str):
                continue

            clean_uri = path_str.replace("\\", "/").lstrip("/")
            physical_loc: dict[str, Any] = {
                "artifactLocation": {
                    "uri": clean_uri,
                    "uriBaseId": "%SRCROOT%",
                }
            }

            line_start = ev.get("line_start")
            line_end = ev.get("line_end", line_start)
            if isinstance(line_start, int) and line_start >= 1:
                region: dict[str, Any] = {"startLine": line_start}
                if isinstance(line_end, int) and line_end >= line_start:
                    region["endLine"] = line_end
                else:
                    region["endLine"] = line_start
                physical_loc["region"] = region

            loc: dict[str, Any] = {"physicalLocation": physical_loc}
            if ev.get("symbol"):
                loc["logicalLocations"] = [{"name": ev["symbol"]}]
            if ev.get("context"):
                loc["message"] = {"text": ev["context"]}

            locations.append(loc)

        # Build result message
        message_parts: list[str] = [title]
        if impact:
            message_parts.append(impact)
        elif actual:
            message_parts.append(actual)
        if remedy:
            message_parts.append(f"Remedy: {remedy}")

        msg_text = " - ".join(message_parts) if len(message_parts) > 1 else title

        result: dict[str, Any] = {
            "ruleId": finding_id,
            "ruleIndex": rule_index,
            "level": sarif_level,
            "message": {"text": msg_text},
            "locations": locations,
            "properties": {
                "findingId": finding_id,
                "topic": topic,
                "status": status,
                "severity": severity,
                "confidence": finding.get("confidence", "unknown"),
            },
        }
        if remedy:
            result["properties"]["remedy"] = remedy

        results.append(result)

    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "fred-skills",
                "version": "1.0",
                "informationUri": "https://github.com/fderuiter/fred-skills",
                "rules": rules,
            }
        },
        "results": results,
    }

    if "repository" in data and isinstance(data["repository"], dict):
        run["properties"] = {"repository": data["repository"]}

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }


def convert_file(
    input_path: Path,
    output_path: Path | None = None,
    *,
    filter_refuted: bool = False,
    filter_info: bool = False,
    indent: int = 2,
) -> dict[str, Any]:
    """Read audit.json from input_path, convert to SARIF, and optionally write to output_path."""
    try:
        content = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read input file '{input_path}': {exc}") from exc

    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"Invalid JSON in '{input_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Input file '{input_path}' must contain a JSON object")

    sarif = audit_to_sarif(
        data,
        filter_refuted=filter_refuted,
        filter_info=filter_info,
    )

    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(sarif, indent=indent, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise ValueError(f"Cannot write output file '{output_path}': {exc}") from exc

    return sarif


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert repository audit findings (audit.json) to SARIF v2.1.0 format.",
    )
    parser.add_argument("audit", type=Path, help="Path to input audit.json file")
    parser.add_argument(
        "output_pos",
        nargs="?",
        type=Path,
        default=None,
        metavar="output",
        help="Optional path to output SARIF file",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_opt",
        type=Path,
        default=None,
        help="Path to output SARIF file",
    )
    parser.add_argument(
        "--filter-refuted",
        "--exclude-refuted",
        dest="filter_refuted",
        action="store_true",
        help="Exclude refuted findings from SARIF results",
    )
    parser.add_argument(
        "--filter-info",
        "--exclude-info",
        dest="filter_info",
        action="store_true",
        help="Exclude informational findings from SARIF results",
    )
    parser.add_argument(
        "--filter-refuted-or-info",
        "--exclude-refuted-or-info",
        dest="filter_refuted_or_info",
        action="store_true",
        help="Exclude both refuted and informational findings",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Number of spaces for JSON indentation (default: 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    output_path = args.output_opt or args.output_pos
    filter_refuted = args.filter_refuted or args.filter_refuted_or_info
    filter_info = args.filter_info or args.filter_refuted_or_info

    try:
        sarif = convert_file(
            args.audit,
            output_path=output_path,
            filter_refuted=filter_refuted,
            filter_info=filter_info,
            indent=args.indent,
        )
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if output_path is None:
        print(json.dumps(sarif, indent=args.indent, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Convert repository audit findings (audit.json) to SARIF v2.1.0 format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SEVERITY_TO_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


class PhysicalLocationResult(dict):
    """Container for SARIF physicalLocation with dual physical/location compatibility."""

    def __init__(self, data: dict[str, Any], ev: dict[str, Any] | None = None) -> None:
        super().__init__(data)
        self._ev = ev or {}

    def __getitem__(self, key: str) -> Any:
        if key == "physicalLocation":
            return self
        if key == "logicalLocations" and self._ev.get("symbol"):
            return [{"name": self._ev["symbol"]}]
        if key == "message" and self._ev.get("context"):
            return {"text": self._ev["context"]}
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        if key == "physicalLocation":
            return True
        if key == "logicalLocations" and self._ev.get("symbol"):
            return True
        if key == "message" and self._ev.get("context"):
            return True
        return super().__contains__(key)


def _extract_region(line_start: Any, line_end: Any) -> dict[str, int] | None:
    """Normalize start and end line numbers into a SARIF region descriptor."""
    if not isinstance(line_start, int) or line_start < 1:
        return None
    end = (
        line_end if isinstance(line_end, int) and line_end >= line_start else line_start
    )
    return {"startLine": line_start, "endLine": end}


def _resolve_evidence_fields(
    evidence_or_path: dict[str, Any] | str,
    line_start: int | None,
    line_end: int | None,
) -> tuple[dict[str, Any], Any, Any, Any]:
    """Resolve raw evidence dict or path into standardized fields."""
    if isinstance(evidence_or_path, dict):
        ev = evidence_or_path
        l_start = ev.get("line_start") if line_start is None else line_start
        l_end = ev.get("line_end", l_start) if line_end is None else line_end
        return ev, ev.get("path"), l_start, l_end
    return {}, evidence_or_path, line_start, line_end


def _extract_physical_location(
    evidence_or_path: dict[str, Any] | str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> PhysicalLocationResult | None:
    """Extract and normalize physical location and region from evidence or path."""
    ev, path_str, l_start, l_end = _resolve_evidence_fields(
        evidence_or_path, line_start, line_end
    )
    if not isinstance(path_str, str) or not path_str:
        return None

    clean_uri = path_str.replace("\\", "/").lstrip("/")
    physical_loc: dict[str, Any] = {
        "artifactLocation": {
            "uri": clean_uri,
            "uriBaseId": "%SRCROOT%",
        }
    }
    region = _extract_region(l_start, l_end)
    if region:
        physical_loc["region"] = region
    return PhysicalLocationResult(physical_loc, ev=ev)


def _extract_evidence_location(ev: Any) -> dict[str, Any] | None:
    """Map a single evidence record to a SARIF location object."""
    if not isinstance(ev, dict):
        return None
    phys = _extract_physical_location(ev)
    if not phys:
        return None

    loc: dict[str, Any] = {"physicalLocation": phys}
    if ev.get("symbol"):
        loc["logicalLocations"] = [{"name": ev["symbol"]}]
    if ev.get("context"):
        loc["message"] = {"text": ev["context"]}
    return loc


def _collect_finding_locations(
    finding: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve evidence IDs for a finding into formatted SARIF location dictionaries."""
    locations: list[dict[str, Any]] = []
    for eid in finding.get("evidence_ids", []):
        loc = _extract_evidence_location(evidence_map.get(eid))
        if loc:
            locations.append(loc)
    return locations


def _extract_dict_help_args(d: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Extract help parameter tuple from a finding dictionary."""
    t = d.get("title")
    title = t if t else d.get("id", "")
    return (
        title,
        d.get("topic", "audit"),
        d.get("impact", ""),
        d.get("root_cause", ""),
        d.get("remedy", ""),
    )


def _format_help_markdown(
    title: Any,
    topic: str = "",
    impact: str = "",
    root_cause: str = "",
    remedy: str = "",
) -> dict[str, str]:
    """Format plain text and markdown strings for rule help content."""
    if isinstance(title, dict):
        title, topic, impact, root_cause, remedy = _extract_dict_help_args(title)

    plain = [
        f"{lbl}: {val}"
        for lbl, val in (
            ("Topic", topic),
            ("Title", title),
            ("Impact", impact),
            ("Root Cause", root_cause),
            ("Remedy", remedy),
        )
        if val
    ]
    md = [f"### {title}"]
    for lbl, val in (("Topic", topic), ("Impact", impact), ("Root Cause", root_cause)):
        if val:
            md.append(f"**{lbl}:** {val}")
    if remedy:
        md.append(f"\n#### Suggested Remedy\n{remedy}")

    return {"text": "\n".join(plain), "markdown": "\n".join(md)}


def _build_rule_tags(tags_raw: Any, topic: str) -> list[str]:
    """Compile tags list ensuring the topic identifier is included."""
    tags = (
        [t for t in tags_raw if isinstance(t, str)]
        if isinstance(tags_raw, list)
        else []
    )
    if topic and topic not in tags:
        tags.append(topic)
    return tags


def _rule_description(impact: str, actual: str) -> dict[str, str] | None:
    """Construct fullDescription text prioritizing impact over actual."""
    if impact:
        return {"text": impact}
    if actual:
        return {"text": actual}
    return None


def _resolve_meta(
    finding: dict[str, Any],
    finding_id: str | None,
    sarif_level: str | None,
) -> tuple[str, str, str, str, str]:
    """Resolve normalized identifiers and metadata fields for rules and results."""
    fid = finding_id or finding.get("id") or "FINDING-1"
    sev = finding.get("severity", "medium")
    level = sarif_level or SEVERITY_TO_SARIF_LEVEL.get(sev, "warning")
    title = finding.get("title") or fid
    topic = finding.get("topic") or "audit"
    return fid, level, title, topic, sev


def _build_sarif_rule(
    finding: dict[str, Any],
    finding_id: str | None = None,
    sarif_level: str | None = None,
) -> dict[str, Any]:
    """Generate a SARIF rule definition dictionary with formatted help and properties."""
    fid, level, title, topic, sev = _resolve_meta(finding, finding_id, sarif_level)
    remedy = finding.get("remedy", "")
    props: dict[str, Any] = {
        "id": fid,
        "topic": topic,
        "severity": sev,
        "status": finding.get("status", ""),
        "confidence": finding.get("confidence", "unknown"),
        "tags": _build_rule_tags(finding.get("tags"), topic),
    }
    if remedy:
        props["remedy"] = remedy

    rule: dict[str, Any] = {
        "id": fid,
        "name": topic or fid,
        "shortDescription": {"text": title},
        "defaultConfiguration": {"level": level},
        "help": _format_help_markdown(
            title,
            topic=topic,
            impact=finding.get("impact", ""),
            root_cause=finding.get("root_cause", ""),
            remedy=remedy,
        ),
        "properties": props,
    }
    desc = _rule_description(finding.get("impact", ""), finding.get("actual", ""))
    if desc:
        rule["fullDescription"] = desc
    return rule


def _format_result_message(title: str, impact: str, actual: str, remedy: str) -> str:
    """Assemble result message string from finding details."""
    parts = [title]
    if impact:
        parts.append(impact)
    elif actual:
        parts.append(actual)
    if remedy:
        parts.append(f"Remedy: {remedy}")
    return " - ".join(parts) if len(parts) > 1 else title


def _build_sarif_result(
    finding: dict[str, Any],
    rule_index: int = 0,
    locations: list[dict[str, Any]] | None = None,
    finding_id: str | None = None,
    sarif_level: str | None = None,
) -> dict[str, Any]:
    """Construct a SARIF result item formatted with message and locations."""
    fid, level, title, topic, sev = _resolve_meta(finding, finding_id, sarif_level)
    remedy = finding.get("remedy", "")
    msg = _format_result_message(
        title, finding.get("impact", ""), finding.get("actual", ""), remedy
    )

    props: dict[str, Any] = {
        "findingId": fid,
        "topic": topic,
        "status": finding.get("status", ""),
        "severity": sev,
        "confidence": finding.get("confidence", "unknown"),
    }
    if remedy:
        props["remedy"] = remedy

    return {
        "ruleId": fid,
        "ruleIndex": rule_index,
        "level": level,
        "message": {"text": msg},
        "locations": [] if locations is None else locations,
        "properties": props,
    }


def _build_evidence_map(evidence_list: Any) -> dict[str, dict[str, Any]]:
    """Index evidence items by their identifier."""
    if not isinstance(evidence_list, list):
        return {}
    return {ev["id"]: ev for ev in evidence_list if isinstance(ev, dict) and "id" in ev}


def _should_skip_finding(
    finding: Any,
    filter_refuted: bool,
    filter_info: bool,
) -> bool:
    """Evaluate whether a finding should be excluded based on filtering flags."""
    if not isinstance(finding, dict):
        return True
    if filter_refuted and finding.get("status") == "refuted":
        return True
    return bool(filter_info and finding.get("severity") == "info")


def _ensure_rule(
    finding: dict[str, Any],
    finding_id: str,
    rules: list[dict[str, Any]],
    rule_indices: dict[str, int],
) -> int:
    """Register rule descriptor if not yet recorded, returning its index."""
    if finding_id not in rule_indices:
        rule_indices[finding_id] = len(rules)
        rules.append(_build_sarif_rule(finding, finding_id=finding_id))
    return rule_indices[finding_id]


def _build_sarif_run(
    rules: list[dict[str, Any]],
    results: list[dict[str, Any]],
    repository: Any = None,
) -> dict[str, Any]:
    """Construct the SARIF run structure containing tool driver rules and results."""
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
    if isinstance(repository, dict):
        run["properties"] = {"repository": repository}
    return run


def _detect_domain_artifact(data: dict[str, Any]) -> str | None:
    """Identify non-audit domain payloads so SARIF export fails with clear guidance."""
    if "run_id" in data:
        return None
    if "verdict" in data or ("summary" in data and "findings" in data):
        return "review.json"
    if "test_cases" in data or ("component" in data and "framework" in data):
        return "testing.json"
    if (
        "boundaries" in data
        or "decisions" in data
        or ("title" in data and "status" in data and "findings" not in data)
    ):
        return "architecture.json"
    return None


def audit_to_sarif(
    data: dict[str, Any],
    *,
    filter_refuted: bool = False,
    filter_info: bool = False,
) -> dict[str, Any]:
    """Convert audit data conforming to audit.schema.json into SARIF v2.1.0."""
    if not isinstance(data, dict):
        raise TypeError("Audit data must be a JSON object")
    domain_artifact = _detect_domain_artifact(data)
    if domain_artifact is not None:
        raise ValueError(
            f"SARIF export ('export-sarif') requires an 'audit.json' ledger; domain artifact '{domain_artifact}' is not supported"
        )

    evidence_map = _build_evidence_map(data.get("evidence", []))
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    rule_indices: dict[str, int] = {}

    for finding in data.get("findings", []):
        if _should_skip_finding(finding, filter_refuted, filter_info):
            continue

        finding_id = finding.get("id") or f"FINDING-{len(results) + 1}"
        rule_index = _ensure_rule(finding, finding_id, rules, rule_indices)
        locations = _collect_finding_locations(finding, evidence_map)
        results.append(
            _build_sarif_result(
                finding,
                rule_index=rule_index,
                locations=locations,
                finding_id=finding_id,
            )
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [_build_sarif_run(rules, results, data.get("repository"))],
    }


def _load_audit_file(input_path: Path) -> dict[str, Any]:
    """Read and parse audit.json from a file or directory path."""
    target = Path(input_path)
    if target.is_dir():
        ledger_path = target / "audit.json"
        if not ledger_path.is_file():
            for domain_name in ("architecture.json", "review.json", "testing.json"):
                if (target / domain_name).is_file():
                    raise ValueError(
                        f"Audit ledger 'audit.json' not found in directory: {input_path}; SARIF export ('export-sarif') requires 'audit.json' and does not support domain artifact '{domain_name}'"
                    )
            raise ValueError(
                f"Audit ledger 'audit.json' not found in directory: {input_path}"
            )
        target = ledger_path

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read input file '{input_path}': {exc}") from exc

    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"Invalid JSON in '{input_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise TypeError(f"Input file '{input_path}' must contain a JSON object")
    return data


def convert_file(
    input_path: Path,
    output_path: Path | None = None,
    *,
    filter_refuted: bool = False,
    filter_info: bool = False,
    indent: int = 2,
) -> dict[str, Any]:
    """Read audit.json from input_path, convert to SARIF, and optionally write to output_path."""
    data = _load_audit_file(input_path)
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
            raise ValueError(
                f"Cannot write output file '{output_path}': {exc}"
            ) from exc

    return sarif


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert repository audit findings (audit.json) to SARIF v2.1.0 format.",
    )
    parser.add_argument(
        "audit",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Path to input audit.json file or audit directory (default: current directory)",
    )
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
    except (ValueError, TypeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if output_path is None:
        print(json.dumps(sarif, indent=args.indent, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Convert repository audit findings (audit.json) to SARIF v2.1.0 format."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

FINGERPRINT_KEY = "fred-skills/v1"

SEVERITY_TO_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
    "advisory": "note",
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


def _digest(data: Any) -> str:
    """Compute canonical SHA-256 hex digest matching fredlib.common.digest."""
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _locations_from_sarif_items(
    locations: list[dict[str, Any]] | None,
) -> list[tuple[str, str]]:
    """Extract sorted unique (path, symbol) pairs from formatted SARIF locations."""
    pairs: set[tuple[str, str]] = set()
    for loc in locations or []:
        if not isinstance(loc, dict):
            continue
        phys = loc.get("physicalLocation")
        ev = getattr(phys, "_ev", None)
        if isinstance(ev, dict) and ("path" in ev or "symbol" in ev):
            pairs.add((str(ev.get("path", "")), str(ev.get("symbol", ""))))
            continue
        uri = ""
        if isinstance(phys, dict):
            art = phys.get("artifactLocation")
            if isinstance(art, dict):
                uri = str(art.get("uri", ""))
        sym = ""
        logical = loc.get("logicalLocations")
        if isinstance(logical, list) and logical and isinstance(logical[0], dict):
            sym = str(logical[0].get("name", ""))
        if uri or sym:
            pairs.add((uri, sym))
    return sorted(pairs)


def _finding_fingerprint(
    finding: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]] | None = None,
    locations: list[dict[str, Any]] | None = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint matching fredlib.runs.fingerprint."""
    if evidence_map is not None:
        raw_ids = finding.get("evidence_ids")
        loc_pairs = sorted(
            {
                (
                    str(evidence_map[eid].get("path", "")),
                    str(evidence_map[eid].get("symbol", "")),
                )
                for eid in (raw_ids if isinstance(raw_ids, list) else [])
                if isinstance(eid, str) and eid in evidence_map
            }
        )
    else:
        loc_pairs = _locations_from_sarif_items(locations)
    return _digest(
        {
            "topic": str(finding.get("topic", "")),
            "root_cause": str(finding.get("root_cause", "")),
            "locations": loc_pairs,
        }
    )


def _derive_rule_id(finding: dict[str, Any], finding_fingerprint: str) -> str:
    """Derive a deterministic SARIF rule identifier from topic and content fingerprint."""
    raw_topic = str(finding.get("topic") or "audit").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw_topic).strip("-") or "audit"
    return f"fred/{slug}/{finding_fingerprint[:12]}"


def _resolve_meta(
    finding: dict[str, Any],
    finding_id: str | None,
    sarif_level: str | None,
    fallback_title: str | None = None,
) -> tuple[str, str, str, str, str]:
    """Resolve normalized identifiers and metadata fields for rules and results."""
    fid = finding_id or finding.get("id") or "FINDING-1"
    sev = finding.get("severity", "medium")
    level = sarif_level or SEVERITY_TO_SARIF_LEVEL.get(sev, "warning")
    title = finding.get("title") or fallback_title or fid
    topic = finding.get("topic") or "audit"
    return fid, level, title, topic, sev


def _build_sarif_rule(
    finding: dict[str, Any],
    finding_id: str | None = None,
    sarif_level: str | None = None,
    *,
    rule_id: str | None = None,
    finding_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Generate a SARIF rule definition dictionary with formatted help and properties."""
    fp = finding_fingerprint or _finding_fingerprint(finding)
    resolved_rule_id = rule_id or _derive_rule_id(finding, fp)
    _fid, level, title, topic, sev = _resolve_meta(
        finding, finding_id, sarif_level, fallback_title=resolved_rule_id
    )
    remedy = finding.get("remedy", "")
    props: dict[str, Any] = {
        "id": resolved_rule_id,
        "topic": topic,
        "severity": sev,
        "status": finding.get("status", ""),
        "confidence": finding.get("confidence", "unknown"),
        "tags": _build_rule_tags(finding.get("tags"), topic),
    }
    if remedy:
        props["remedy"] = remedy

    rule: dict[str, Any] = {
        "id": resolved_rule_id,
        "name": topic or resolved_rule_id,
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
    *,
    rule_id: str | None = None,
    finding_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Construct a SARIF result item formatted with message, fingerprints, and locations."""
    fp = finding_fingerprint or _finding_fingerprint(finding, locations=locations)
    resolved_rule_id = rule_id or _derive_rule_id(finding, fp)
    fid, level, title, topic, sev = _resolve_meta(
        finding, finding_id, sarif_level, fallback_title=resolved_rule_id
    )
    remedy = finding.get("remedy", "")
    msg = _format_result_message(
        title, finding.get("impact", ""), finding.get("actual", ""), remedy
    )

    status = finding.get("status", "")
    is_refuted = status == "refuted"
    result_kind = "pass" if is_refuted else "fail"
    result_level = "none" if is_refuted and sarif_level is None else level

    props: dict[str, Any] = {
        "findingId": fid,
        "topic": topic,
        "status": status,
        "severity": sev,
        "confidence": finding.get("confidence", "unknown"),
    }
    if remedy:
        props["remedy"] = remedy

    return {
        "ruleId": resolved_rule_id,
        "ruleIndex": rule_index,
        "kind": result_kind,
        "level": result_level,
        "message": {"text": msg},
        "locations": [] if locations is None else locations,
        "partialFingerprints": {FINGERPRINT_KEY: fp},
        "fingerprints": {FINGERPRINT_KEY: fp},
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
    return bool(filter_info and finding.get("severity") in {"info", "advisory"})


def _ensure_rule(
    finding: dict[str, Any],
    rule_id: str,
    rules: list[dict[str, Any]],
    rule_indices: dict[str, int],
    *,
    finding_id: str | None = None,
    finding_fingerprint: str | None = None,
) -> int:
    """Register rule descriptor per deterministic rule ID if not yet recorded."""
    if rule_id not in rule_indices:
        rule_indices[rule_id] = len(rules)
        rules.append(
            _build_sarif_rule(
                finding,
                finding_id=finding_id,
                rule_id=rule_id,
                finding_fingerprint=finding_fingerprint,
            )
        )
    return rule_indices[rule_id]


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


_NON_AUDIT_ARTIFACT_MARKERS: tuple[tuple[str, frozenset[str]], ...] = (
    ("remediation.json", frozenset({"records"})),
    (
        "reconciliation.json",
        frozenset(
            {
                "finding_fingerprint_matches",
                "fingerprint_matches",
                "remediation_state_transitions",
                "state_transitions",
                "unmapped_records",
            }
        ),
    ),
    (
        "finding-verification.json",
        frozenset(
            {
                "reproduction_status",
                "safe_control_outcomes",
                "confidence_rating",
            }
        ),
    ),
    (
        "regression.json",
        frozenset(
            {
                "failure_boundaries",
                "failure_boundary",
                "test_command_traces",
                "assertion_expectations",
            }
        ),
    ),
    (
        "plan.json",
        frozenset(
            {
                "task_breakdowns",
                "dependency_order",
                "file_scopes",
                "plan_id",
            }
        ),
    ),
    (
        "fix.json",
        frozenset(
            {
                "file_changes",
                "diff_summaries",
                "diff_summary",
                "execution_results",
                "compatibility_limits",
            }
        ),
    ),
    (
        "verification.json",
        frozenset(
            {
                "original_reproduced",
                "regression_passed",
                "before_revision",
                "after_revision",
                "safe_controls_passed",
            }
        ),
    ),
    ("observations.json", frozenset({"observations"})),
    ("review.json", frozenset({"verdict"})),
    ("testing.json", frozenset({"test_cases"})),
    (
        "spec.json",
        frozenset(
            {
                "interview_decisions",
                "new_file_scopes",
                "out_of_scope",
                "verification_commands",
            }
        ),
    ),
    ("architecture.json", frozenset({"boundaries", "decisions", "components"})),
    ("run.json", frozenset({"plan", "authority", "checkpoints"})),
)

_NON_AUDIT_ARTIFACT_NAMES: tuple[str, ...] = (
    "architecture.json",
    "review.json",
    "testing.json",
    "spec.json",
    "finding-verification.json",
    "regression.json",
    "plan.json",
    "fix.json",
    "verification.json",
    "reconciliation.json",
    "remediation.json",
    "observations.json",
    "scanner-observations.json",
    "run.json",
)


def _detect_domain_artifact(data: object) -> str | None:
    """Identify non-audit domain or workflow payloads so SARIF export fails with clear guidance."""
    if not isinstance(data, dict):
        return None
    if (
        "findings" in data
        and "verdict" not in data
        and not any(
            key in data
            for key in (
                "test_cases",
                "boundaries",
                "decisions",
                "components",
                "interview_decisions",
            )
        )
        and not (
            "summary" in data
            and not any(
                key in data for key in ("evidence", "contracts", "repository", "issues")
            )
        )
        and any(
            key in data
            for key in ("evidence", "coverage", "contracts", "run_id", "repository")
        )
    ):
        return None
    keys = set(data)
    for artifact_name, markers in _NON_AUDIT_ARTIFACT_MARKERS:
        if keys & markers:
            return artifact_name
    if "summary" in data and "findings" in data:
        return "review.json"
    if "component" in data and "framework" in data:
        return "testing.json"
    if "title" in data and "status" in data and "findings" not in data:
        return "architecture.json"
    if "run_id" in data:
        return "run.json"
    return None


_REVIEW_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "snapshot",
        "verdict",
        "summary",
        "coverage",
        "paths",
        "findings",
    }
)
_REVIEW_REQUIRED_KEYS = frozenset({"schema_version", "verdict", "summary", "findings"})
_REVIEW_VERDICTS = frozenset({"approve", "request_changes", "comment"})
_REVIEW_SEVERITIES = frozenset({"critical", "high", "medium", "low", "advisory"})
_REVIEW_FINDING_ALLOWED_KEYS = frozenset(
    {
        "id",
        "title",
        "severity",
        "file",
        "paths",
        "line",
        "description",
        "recommendation",
    }
)
_REVIEW_FINDING_REQUIRED_KEYS = frozenset(
    {"id", "title", "severity", "file", "description"}
)
_REVIEW_COVERAGE_ALLOWED_KEYS = frozenset(
    {"topic", "scope", "status", "notes", "next_action", "paths"}
)
_REVIEW_COVERAGE_STATUSES = frozenset(
    {"reviewed", "partial", "not-reviewed", "out-of-scope"}
)


def _is_repo_relative_path(raw_path: object) -> bool:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        return False
    posix = PurePosixPath(raw_path)
    win = PureWindowsPath(raw_path)
    return not (
        posix.is_absolute()
        or win.is_absolute()
        or not posix.parts
        or posix.parts == (".",)
        or any(part in {"..", ".git"} for part in posix.parts)
    )


def _invalid_review_error(detail: str) -> ValueError:
    return ValueError(
        f"Invalid 'review.json' artifact for SARIF export ('export-sarif' requires a valid 'audit.json' or 'review.json'): {detail}"
    )


def _validate_review_path_list(raw_paths: object, label: str) -> None:
    if not isinstance(raw_paths, list):
        raise _invalid_review_error(f"'{label}' must be an array of relative paths")
    for item in raw_paths:
        if not _is_repo_relative_path(item):
            raise _invalid_review_error(
                f"path in '{label}' must be repository-relative: {item!r}"
            )


def _validate_review_coverage(raw_coverage: object) -> None:
    if not isinstance(raw_coverage, list):
        raise _invalid_review_error("'coverage' must be a JSON array")
    for entry in raw_coverage:
        if not isinstance(entry, dict):
            raise _invalid_review_error("each coverage entry must be a JSON object")
        extra = set(entry) - _REVIEW_COVERAGE_ALLOWED_KEYS
        if extra:
            raise _invalid_review_error(
                f"unexpected coverage properties: {sorted(extra)}"
            )
        for req_str in ("topic", "scope"):
            val = entry.get(req_str)
            if not isinstance(val, str) or not val:
                raise _invalid_review_error(
                    f"coverage '{req_str}' must be a nonempty string"
                )
        if entry.get("status") not in _REVIEW_COVERAGE_STATUSES:
            raise _invalid_review_error(
                f"invalid coverage status: {entry.get('status')!r}"
            )
        for opt_str in ("notes", "next_action"):
            if opt_str in entry and not isinstance(entry[opt_str], str):
                raise _invalid_review_error(f"coverage '{opt_str}' must be a string")
        if "paths" in entry:
            _validate_review_path_list(entry["paths"], "coverage.paths")


def _validate_review_data(data: dict[str, Any]) -> None:
    missing = sorted(_REVIEW_REQUIRED_KEYS - set(data))
    if missing:
        raise _invalid_review_error(f"missing required properties: {missing}")
    extra_top = set(data) - _REVIEW_ALLOWED_KEYS
    if extra_top:
        raise _invalid_review_error(f"unexpected properties: {sorted(extra_top)}")
    if data.get("schema_version") != "1.0":
        raise _invalid_review_error("'schema_version' must be '1.0'")
    verdict = data.get("verdict")
    if verdict not in _REVIEW_VERDICTS:
        raise _invalid_review_error(f"invalid 'verdict': {verdict!r}")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary:
        raise _invalid_review_error("'summary' must be a nonempty string")
    for opt_nonempty in ("run_id", "snapshot"):
        if opt_nonempty in data:
            val = data[opt_nonempty]
            if not isinstance(val, str) or not val:
                raise _invalid_review_error(
                    f"'{opt_nonempty}' must be a nonempty string"
                )
    if "paths" in data:
        _validate_review_path_list(data["paths"], "paths")
    if "coverage" in data:
        _validate_review_coverage(data["coverage"])
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise _invalid_review_error("'findings' must be a JSON array")

    seen_ids: set[str] = set()
    blocking_ids: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise _invalid_review_error("each finding in 'findings' must be an object")
        missing_f = sorted(_REVIEW_FINDING_REQUIRED_KEYS - set(finding))
        if missing_f:
            raise _invalid_review_error(
                f"finding missing required properties: {missing_f}"
            )
        extra_f = set(finding) - _REVIEW_FINDING_ALLOWED_KEYS
        if extra_f:
            raise _invalid_review_error(
                f"finding has unexpected properties: {sorted(extra_f)}"
            )
        fid = finding.get("id")
        if not isinstance(fid, str) or not fid:
            raise _invalid_review_error("finding 'id' must be a nonempty string")
        if fid in seen_ids:
            raise _invalid_review_error(f"duplicate finding ID: {fid}")
        seen_ids.add(fid)
        for str_field in ("title", "description"):
            fval = finding.get(str_field)
            if not isinstance(fval, str) or not fval:
                raise _invalid_review_error(
                    f"finding '{str_field}' must be a nonempty string"
                )
        sev = finding.get("severity")
        if sev not in _REVIEW_SEVERITIES:
            raise _invalid_review_error(f"invalid finding severity: {sev!r}")
        if sev in {"critical", "high"}:
            blocking_ids.append(fid)
        if not _is_repo_relative_path(finding.get("file")):
            raise _invalid_review_error(
                f"finding 'file' must be repository-relative: {finding.get('file')!r}"
            )
        if "paths" in finding:
            _validate_review_path_list(finding["paths"], f"{fid}.paths")
        if "line" in finding:
            line_val = finding["line"]
            if (
                not isinstance(line_val, int)
                or isinstance(line_val, bool)
                or line_val < 1
            ):
                raise _invalid_review_error(
                    f"finding 'line' must be a positive integer: {line_val!r}"
                )
        if "recommendation" in finding and not isinstance(
            finding["recommendation"], str
        ):
            raise _invalid_review_error("finding 'recommendation' must be a string")
    if verdict == "approve" and blocking_ids:
        raise _invalid_review_error(
            f"verdict 'approve' conflicts with critical/high findings: {', '.join(blocking_ids)}"
        )


def _collect_review_finding_locations(finding: dict[str, Any]) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    line_num = finding.get("line")
    primary_file = str(finding["file"])
    primary_phys = _extract_physical_location(
        {"path": primary_file, "line_start": line_num, "line_end": line_num}
    )
    seen_uris: set[str] = set()
    if primary_phys is not None:
        locations.append({"physicalLocation": primary_phys})
        seen_uris.add(primary_phys["artifactLocation"]["uri"])
    for extra_path in finding.get("paths", []) or []:
        extra_phys = _extract_physical_location({"path": str(extra_path)})
        if (
            extra_phys is not None
            and extra_phys["artifactLocation"]["uri"] not in seen_uris
        ):
            locations.append({"physicalLocation": extra_phys})
            seen_uris.add(extra_phys["artifactLocation"]["uri"])
    return locations


def _review_to_sarif(
    data: dict[str, Any],
    *,
    filter_info: bool = False,
) -> dict[str, Any]:
    _validate_review_data(data)
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    rule_indices: dict[str, int] = {}

    for finding in data["findings"]:
        if _should_skip_finding(finding, False, filter_info):
            continue
        finding_id = str(finding["id"])
        description = str(finding["description"])
        remedy = str(finding.get("recommendation") or "")
        normalized: dict[str, Any] = {
            "id": finding_id,
            "topic": "code-review",
            "title": str(finding["title"]),
            "severity": str(finding["severity"]),
            "status": "",
            "confidence": "unknown",
            "impact": description,
            "root_cause": description,
            "remedy": remedy,
        }
        locations = _collect_review_finding_locations(finding)
        fp = _finding_fingerprint(normalized, locations=locations)
        rule_id = _derive_rule_id(normalized, fp)
        rule_index = _ensure_rule(
            normalized,
            rule_id,
            rules,
            rule_indices,
            finding_id=finding_id,
            finding_fingerprint=fp,
        )
        results.append(
            _build_sarif_result(
                normalized,
                rule_index=rule_index,
                locations=locations,
                finding_id=finding_id,
                rule_id=rule_id,
                finding_fingerprint=fp,
            )
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [_build_sarif_run(rules, results)],
    }


def audit_to_sarif(
    data: dict[str, Any],
    *,
    filter_refuted: bool = False,
    filter_info: bool = False,
) -> dict[str, Any]:
    """Convert audit.json or review.json data into SARIF v2.1.0."""
    if not isinstance(data, dict):
        raise TypeError("Audit data must be a JSON object")
    domain_artifact = _detect_domain_artifact(data)
    if domain_artifact == "review.json":
        return _review_to_sarif(data, filter_info=filter_info)
    if domain_artifact is not None:
        raise ValueError(
            f"SARIF export ('export-sarif') requires an 'audit.json' ledger; domain artifact '{domain_artifact}' is not supported"
        )
    if "findings" not in data:
        raise ValueError(
            "SARIF export ('export-sarif') requires an 'audit.json' ledger containing a 'findings' array"
        )
    if not isinstance(data["findings"], list):
        raise TypeError(
            "SARIF export ('export-sarif') requires 'findings' in 'audit.json' to be a JSON array"
        )

    evidence_map = _build_evidence_map(data.get("evidence", []))
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    rule_indices: dict[str, int] = {}

    for finding in data["findings"]:
        if _should_skip_finding(finding, filter_refuted, filter_info):
            continue

        finding_id = finding.get("id") or f"FINDING-{len(results) + 1}"
        fp = _finding_fingerprint(finding, evidence_map)
        rule_id = _derive_rule_id(finding, fp)
        rule_index = _ensure_rule(
            finding,
            rule_id,
            rules,
            rule_indices,
            finding_id=finding_id,
            finding_fingerprint=fp,
        )
        locations = _collect_finding_locations(finding, evidence_map)
        results.append(
            _build_sarif_result(
                finding,
                rule_index=rule_index,
                locations=locations,
                finding_id=finding_id,
                rule_id=rule_id,
                finding_fingerprint=fp,
            )
        )

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [_build_sarif_run(rules, results, data.get("repository"))],
    }


def _resolve_directory_sarif_target(input_path: Path, target: Path) -> Path:
    ledger_path = target / "audit.json"
    if ledger_path.is_file():
        return ledger_path
    review_path = target / "review.json"
    other_primary = [
        name
        for name in ("architecture.json", "testing.json", "spec.json")
        if (target / name).is_file()
    ]
    if review_path.is_file() and not other_primary:
        return review_path
    for domain_name in _NON_AUDIT_ARTIFACT_NAMES:
        if domain_name != "review.json" and (target / domain_name).is_file():
            raise ValueError(
                f"Audit ledger 'audit.json' not found in directory: {input_path}; SARIF export ('export-sarif') requires 'audit.json' and does not support domain artifact '{domain_name}'"
            )
    raise ValueError(f"Audit ledger 'audit.json' not found in directory: {input_path}")


def _load_audit_file(input_path: Path) -> dict[str, Any]:
    """Read and parse audit.json or review.json from a file or directory path."""
    target = Path(input_path)
    if target.is_dir():
        target = _resolve_directory_sarif_target(input_path, target)

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
    if target.name == "review.json":
        _validate_review_data(data)
        return data
    if target.name in _NON_AUDIT_ARTIFACT_NAMES:
        raise ValueError(
            f"SARIF export ('export-sarif') requires an 'audit.json' ledger; domain artifact '{target.name}' is not supported"
        )
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

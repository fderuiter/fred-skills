from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from .common import FredCliError, read_json_object, relative_path

UriKind = Literal["abs", "rel", "root_marker"]


@dataclass(frozen=True, slots=True)
class ParsedSarifUri:
    kind: UriKind
    value: str


_ROOT_BASE_ID_KEYS = (
    "%SRCROOT%",
    "SRCROOT",
    "%ROOTPATH%",
    "ROOTPATH",
    "%PROJECTROOT%",
    "PROJECTROOT",
    "PROJECT_ROOT",
    "%WORKSPACE%",
    "WORKSPACE",
    "REPOROOT",
    "REPO_ROOT",
)

_NON_FAIL_RESULT_KINDS = frozenset(
    {
        "pass",
        "open",
        "review",
        "notApplicable",
        "informational",
    }
)
_SKIPPED_RESULT_KINDS = frozenset({"pass", "notApplicable"})
_RETAINED_RESULT_KINDS = frozenset({"fail", "open", "review", "informational"})
_VALID_SARIF_LEVELS = frozenset({"none", "note", "warning", "error"})
_ACTIVE_SUPPRESSION_STATUSES = frozenset({"underReview", "rejected"})
_VALID_SUPPRESSION_KINDS = frozenset({"inSource", "external"})
_AUDIT_PROPERTY_KEYS = (
    "findingId",
    "topic",
    "status",
    "severity",
    "confidence",
    "remedy",
)
_RULE_FALLBACK_PROPERTY_KEYS = (
    "topic",
    "status",
    "severity",
    "confidence",
    "remedy",
)


def _canonicalize_abs_posix(raw_path: str) -> str | None:
    if (
        len(raw_path) >= 3
        and raw_path[0] == "/"
        and raw_path[1].isalpha()
        and raw_path[2] == ":"
    ):
        raw_path = raw_path[1].upper() + raw_path[2:]
    elif len(raw_path) >= 2 and raw_path[0].isalpha() and raw_path[1] == ":":
        raw_path = raw_path[0].upper() + raw_path[1:]

    is_win_drive = len(raw_path) >= 2 and raw_path[0].isalpha() and raw_path[1] == ":"
    if is_win_drive:
        drive = raw_path[:2]
        rest = raw_path[2:]
        if rest and not rest.startswith("/"):
            return None
        parts = [seg for seg in rest.split("/") if seg and seg != "."]
        return f"{drive}/{'/'.join(parts)}" if parts else f"{drive}/"

    if not raw_path.startswith("/"):
        return None
    parts = [seg for seg in raw_path.split("/") if seg and seg != "."]
    return "/" + "/".join(parts) if parts else "/"


def _has_disallowed_segments(path_text: str) -> bool:
    segments = path_text.split("/")
    return ".." in segments or ".git" in segments


def _parse_windows_drive_uri(raw_uri: str) -> ParsedSarifUri | None:
    if _has_disallowed_segments(raw_uri[2:]):
        return None
    canon = _canonicalize_abs_posix(raw_uri)
    return ParsedSarifUri("abs", canon) if canon else None


def _parse_file_scheme_uri(
    scheme: str, netloc: str, path: str
) -> ParsedSarifUri | None:
    if scheme.lower() != "file" or netloc.lower() not in {"", "localhost"}:
        return None
    decoded = unquote(path)
    if (
        not decoded
        or "\x00" in decoded
        or "\\" in decoded
        or _has_disallowed_segments(decoded)
    ):
        return None
    canon = _canonicalize_abs_posix(decoded)
    return ParsedSarifUri("abs", canon) if canon else None


def _parse_schemeless_uri(path: str, *, allow_empty_rel: bool) -> ParsedSarifUri | None:
    decoded = unquote(path)
    if "\x00" in decoded or "\\" in decoded or _has_disallowed_segments(decoded):
        return None
    if decoded.startswith("/"):
        canon = _canonicalize_abs_posix(decoded)
        return ParsedSarifUri("abs", canon) if canon else None
    pure = PurePosixPath(decoded)
    if not pure.parts or pure.parts == (".",):
        return ParsedSarifUri("rel", "") if allow_empty_rel else None
    try:
        checked = relative_path(decoded)
    except ValueError:
        return None
    return ParsedSarifUri("rel", checked.as_posix())


def _parse_sarif_uri(
    raw_uri: object, *, allow_empty_rel: bool = False
) -> ParsedSarifUri | None:
    if not isinstance(raw_uri, str) or "\x00" in raw_uri or "\\" in raw_uri:
        return None
    if not raw_uri:
        return ParsedSarifUri("rel", "") if allow_empty_rel else None
    if len(raw_uri) >= 2 and raw_uri[0].isalpha() and raw_uri[1] == ":":
        return _parse_windows_drive_uri(raw_uri)

    parsed = urlsplit(raw_uri)
    if parsed.query or parsed.fragment:
        return None
    if parsed.scheme:
        return _parse_file_scheme_uri(parsed.scheme, parsed.netloc, parsed.path)
    if parsed.netloc:
        return None
    return _parse_schemeless_uri(parsed.path, allow_empty_rel=allow_empty_rel)


def _normalize_fs_roots(root: Path) -> list[str]:
    expanded = Path(root).expanduser()
    candidates = [
        Path(os.path.abspath(os.fspath(expanded))).as_posix(),
        expanded.resolve().as_posix(),
    ]
    normalized: list[str] = []
    for item in candidates:
        canon = _canonicalize_abs_posix(item)
        if canon and canon not in normalized:
            normalized.append(canon)
    return normalized


def _is_usable_abs_root(path_str: str) -> bool:
    if not path_str or path_str == "/":
        return False
    return not (len(path_str) == 3 and path_str[1:] == ":/")


def _join_usable_abs_root(base_abs: str, rel_val: str) -> str | None:
    joined = f"{base_abs.rstrip('/')}/{rel_val}" if rel_val else base_abs
    canon = _canonicalize_abs_posix(joined)
    return canon if canon and _is_usable_abs_root(canon) else None


def _join_with_parent_base(
    child_val: str, parent: ParsedSarifUri
) -> ParsedSarifUri | None:
    if parent.kind == "abs":
        usable = _join_usable_abs_root(parent.value, child_val)
        return ParsedSarifUri("abs", usable) if usable else None
    if parent.kind == "rel":
        joined = (
            f"{parent.value}/{child_val}".strip("/")
            if parent.value and child_val
            else (parent.value or child_val)
        )
        return ParsedSarifUri("rel", joined)
    return ParsedSarifUri("rel", child_val)


def _resolve_empty_base_entry(
    parent_id: object,
    original_base_ids: dict[Any, Any],
    resolved: dict[str, ParsedSarifUri | None],
    visiting: set[str],
) -> ParsedSarifUri | None:
    if isinstance(parent_id, str) and parent_id in original_base_ids:
        return _resolve_base_entry(parent_id, original_base_ids, resolved, visiting)
    return ParsedSarifUri("root_marker", "")


def _compute_base_entry(
    entry: dict[str, Any],
    original_base_ids: dict[Any, Any],
    resolved: dict[str, ParsedSarifUri | None],
    visiting: set[str],
) -> ParsedSarifUri | None:
    raw_uri = entry.get("uri")
    parent_id = entry.get("uriBaseId")
    if raw_uri is None or raw_uri == "":
        return _resolve_empty_base_entry(
            parent_id, original_base_ids, resolved, visiting
        )

    parsed = _parse_sarif_uri(raw_uri, allow_empty_rel=True)
    if parsed is None:
        return None
    if parsed.kind == "abs":
        return parsed if _is_usable_abs_root(parsed.value) else None

    if isinstance(parent_id, str) and parent_id:
        if parent_id not in original_base_ids:
            return ParsedSarifUri("rel", parsed.value)
        parent = _resolve_base_entry(parent_id, original_base_ids, resolved, visiting)
        return _join_with_parent_base(parsed.value, parent) if parent else None

    return ParsedSarifUri("rel", parsed.value)


def _resolve_base_entry(
    key: str,
    original_base_ids: dict[Any, Any],
    resolved: dict[str, ParsedSarifUri | None],
    visiting: set[str],
) -> ParsedSarifUri | None:
    if key in resolved:
        return resolved[key]
    if key in visiting:
        return None
    entry = original_base_ids.get(key)
    if not isinstance(entry, dict):
        resolved[key] = None
        return None
    visiting.add(key)
    try:
        res = _compute_base_entry(entry, original_base_ids, resolved, visiting)
        resolved[key] = res
        return res
    finally:
        visiting.discard(key)


def _resolve_base_map(
    original_base_ids: object,
) -> dict[str, ParsedSarifUri | None]:
    if not isinstance(original_base_ids, dict):
        return {}
    resolved: dict[str, ParsedSarifUri | None] = {}
    visiting: set[str] = set()
    for base_key in original_base_ids:
        if isinstance(base_key, str):
            _resolve_base_entry(base_key, original_base_ids, resolved, visiting)
    return resolved


def _extract_location_abs_dir(
    loc_obj: object, base_map: dict[str, ParsedSarifUri | None]
) -> str | None:
    if not isinstance(loc_obj, dict):
        return None
    raw_uri = loc_obj.get("uri")
    base_id = loc_obj.get("uriBaseId")
    if not raw_uri and isinstance(base_id, str) and base_id in base_map:
        mapped = base_map[base_id]
        if mapped and mapped.kind == "abs" and _is_usable_abs_root(mapped.value):
            return mapped.value
        return None
    parsed = _parse_sarif_uri(raw_uri, allow_empty_rel=True)
    if parsed is None:
        return None
    if parsed.kind == "abs":
        return parsed.value if _is_usable_abs_root(parsed.value) else None
    if isinstance(base_id, str) and base_id in base_map:
        mapped = base_map[base_id]
        if mapped and mapped.kind == "abs":
            return _join_usable_abs_root(mapped.value, parsed.value)
    return None


def _append_usable_root(roots: list[str], candidate: str | None) -> None:
    if candidate and _is_usable_abs_root(candidate) and candidate not in roots:
        roots.append(candidate)


def _add_well_known_base_roots(
    roots: list[str], base_map: dict[str, ParsedSarifUri | None]
) -> None:
    upper_to_key = {k.upper(): k for k in base_map}
    for well_known in _ROOT_BASE_ID_KEYS:
        matched_key = upper_to_key.get(well_known)
        if matched_key is not None:
            entry = base_map[matched_key]
            if entry and entry.kind == "abs":
                _append_usable_root(roots, entry.value)


def _add_version_control_roots(
    roots: list[str], run: dict[str, Any], base_map: dict[str, ParsedSarifUri | None]
) -> None:
    vc_list = run.get("versionControlProvenance")
    if not isinstance(vc_list, list):
        return
    for vc in vc_list:
        if isinstance(vc, dict):
            _append_usable_root(
                roots, _extract_location_abs_dir(vc.get("mappedTo"), base_map)
            )


def _nonneg_int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _add_invocation_roots(
    roots: list[str],
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    invocation_index: object,
) -> None:
    invocations = run.get("invocations")
    if not isinstance(invocations, list):
        return
    inv_idx = _nonneg_int_or_none(invocation_index)
    if inv_idx is not None and inv_idx < len(invocations):
        inv = invocations[inv_idx]
        if isinstance(inv, dict):
            _append_usable_root(
                roots, _extract_location_abs_dir(inv.get("workingDirectory"), base_map)
            )
    for inv in invocations:
        if isinstance(inv, dict):
            _append_usable_root(
                roots, _extract_location_abs_dir(inv.get("workingDirectory"), base_map)
            )


def _add_fallback_top_level_base_roots(
    roots: list[str], run: dict[str, Any], base_map: dict[str, ParsedSarifUri | None]
) -> None:
    raw_bases = run.get("originalUriBaseIds")
    if not isinstance(raw_bases, dict):
        return
    for key, entry in base_map.items():
        raw_entry = raw_bases.get(key)
        if (
            isinstance(raw_entry, dict)
            and not raw_entry.get("uriBaseId")
            and entry
            and entry.kind == "abs"
        ):
            _append_usable_root(roots, entry.value)


def _collect_sarif_roots(
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    invocation_index: object = None,
) -> list[str]:
    roots: list[str] = []
    _add_well_known_base_roots(roots, base_map)
    _add_version_control_roots(roots, run, base_map)
    _add_invocation_roots(roots, run, base_map, invocation_index)
    special = run.get("specialLocations")
    if isinstance(special, dict):
        _append_usable_root(
            roots, _extract_location_abs_dir(special.get("displayBase"), base_map)
        )
    if not roots:
        _add_fallback_top_level_base_roots(roots, run, base_map)
    return roots


def _relativize_against_roots(
    target_abs: str, candidate_roots: list[str]
) -> str | None:
    for base_abs in candidate_roots:
        if not _is_usable_abs_root(base_abs):
            continue
        prefix = base_abs.rstrip("/") + "/"
        if target_abs.startswith(prefix):
            rel = target_abs[len(prefix) :]
            try:
                checked = relative_path(rel)
            except ValueError:
                return None
            if checked.parts:
                return checked.as_posix()
    return None


def _resolve_artifact_location_dict(
    artifact_loc: object, run: dict[str, Any]
) -> dict[str, Any] | None:
    if not isinstance(artifact_loc, dict):
        return None
    if artifact_loc.get("uri"):
        return artifact_loc
    idx = _nonneg_int_or_none(artifact_loc.get("index"))
    artifacts = run.get("artifacts")
    if idx is not None and isinstance(artifacts, list) and idx < len(artifacts):
        art = artifacts[idx]
        if isinstance(art, dict) and isinstance(art.get("location"), dict):
            return art["location"]
    return artifact_loc


def _checked_nonempty_relative_posix(raw_rel: str) -> str | None:
    try:
        checked = relative_path(raw_rel)
    except ValueError:
        return None
    return checked.as_posix() if checked.parts else None


def _resolve_relative_sarif_path(
    rel_val: str,
    resolved_loc: dict[str, Any],
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    explicit_roots: list[str] | None,
    sarif_roots: list[str],
) -> str | None:
    base_id = resolved_loc.get("uriBaseId")
    raw_bases = run.get("originalUriBaseIds")
    if (
        isinstance(base_id, str)
        and base_id
        and isinstance(raw_bases, dict)
        and base_id in raw_bases
    ):
        mapped = base_map.get(base_id)
        if mapped is None:
            return None
        if mapped.kind == "abs":
            canon = _canonicalize_abs_posix(f"{mapped.value.rstrip('/')}/{rel_val}")
            if not canon:
                return None
            candidate_roots = list(
                dict.fromkeys([*(explicit_roots or []), *sarif_roots])
            )
            return _relativize_against_roots(canon, candidate_roots)
        if mapped.kind == "rel" and mapped.value:
            rel_val = f"{mapped.value.rstrip('/')}/{rel_val}"
    return _checked_nonempty_relative_posix(rel_val)


def _normalize_sarif_artifact_path(
    artifact_loc: object,
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    explicit_roots: list[str] | None,
    sarif_roots: list[str],
) -> str | None:
    resolved_loc = _resolve_artifact_location_dict(artifact_loc, run)
    if not isinstance(resolved_loc, dict):
        return None
    parsed = _parse_sarif_uri(resolved_loc.get("uri"))
    if parsed is None:
        return None
    if parsed.kind == "abs":
        allowed_roots = explicit_roots if explicit_roots is not None else sarif_roots
        return _relativize_against_roots(parsed.value, allowed_roots)
    return _resolve_relative_sarif_path(
        parsed.value, resolved_loc, run, base_map, explicit_roots, sarif_roots
    )


def _match_component_by_field(
    field: str, value: object, components: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    for comp in components:
        if comp.get(field) == value:
            return comp
    return None


def _select_tool_component(
    tc_ref: object, driver: dict[str, Any], extensions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not isinstance(tc_ref, dict):
        return None
    idx = _nonneg_int_or_none(tc_ref.get("index"))
    if idx is not None and idx < len(extensions):
        return extensions[idx]
    components = [driver, *extensions]
    return _match_component_by_field(
        "guid", tc_ref.get("guid"), components
    ) or _match_component_by_field("name", tc_ref.get("name"), components)


def _lookup_rule_in_component(
    component: dict[str, Any],
    rule_id: str | None,
    rule_guid: str | None,
    rule_index: int | None,
) -> dict[str, Any] | None:
    rules = component.get("rules")
    if not isinstance(rules, list):
        return None
    if isinstance(rule_index, int) and 0 <= rule_index < len(rules):
        candidate = rules[rule_index]
        if isinstance(candidate, dict) and (
            not rule_id or not candidate.get("id") or candidate.get("id") == rule_id
        ):
            return candidate
    if rule_guid:
        for item in rules:
            if isinstance(item, dict) and item.get("guid") == rule_guid:
                return item
    if rule_id:
        for item in rules:
            if isinstance(item, dict) and item.get("id") == rule_id:
                return item
    return None


def _find_sarif_rule(
    result: dict[str, Any],
    driver: dict[str, Any],
    extensions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    raw_rule_ref = result.get("rule")
    rule_ref: dict[str, Any] = raw_rule_ref if isinstance(raw_rule_ref, dict) else {}
    raw_rule_id = result.get("ruleId") or rule_ref.get("id")
    rule_id = raw_rule_id if isinstance(raw_rule_id, str) and raw_rule_id else None
    raw_guid = rule_ref.get("guid")
    rule_guid = raw_guid if isinstance(raw_guid, str) and raw_guid else None
    idx = _nonneg_int_or_none(rule_ref.get("index"))
    if idx is None:
        idx = _nonneg_int_or_none(result.get("ruleIndex"))

    target_comp = _select_tool_component(
        rule_ref.get("toolComponent"), driver, extensions
    )
    if target_comp is not None:
        matched = _lookup_rule_in_component(target_comp, rule_id, rule_guid, idx)
        if matched is not None:
            return matched

    matched = _lookup_rule_in_component(
        driver, rule_id, rule_guid, idx if target_comp is None else None
    )
    if matched is not None:
        return matched

    for ext in extensions:
        matched = _lookup_rule_in_component(ext, rule_id, rule_guid, None)
        if matched is not None:
            return matched
    return None


def _override_entry_level(
    override: object, rule_id: str | None, res_idx: object
) -> str | None:
    if not isinstance(override, dict):
        return None
    desc = override.get("descriptor")
    cfg = override.get("configuration")
    if not isinstance(desc, dict) or not isinstance(cfg, dict):
        return None
    level = cfg.get("level")
    if not isinstance(level, str) or level not in _VALID_SARIF_LEVELS:
        return None
    matches_id = bool(rule_id and desc.get("id") == rule_id)
    norm_res_idx = _nonneg_int_or_none(res_idx)
    desc_idx = _nonneg_int_or_none(desc.get("index"))
    matches_index = (
        norm_res_idx is not None and desc_idx == norm_res_idx and not desc.get("id")
    )
    return level if (matches_id or matches_index) else None


def _find_override_level(
    result: dict[str, Any], run: dict[str, Any], rule_id: str | None
) -> str | None:
    invocations = run.get("invocations")
    if not isinstance(invocations, list):
        return None
    inv_idx = _nonneg_int_or_none(result.get("invocationIndex"))
    ordered = (
        [invocations[inv_idx]]
        if inv_idx is not None and inv_idx < len(invocations)
        else invocations
    )
    raw_rule_ref = result.get("rule")
    rule_ref: dict[str, Any] = raw_rule_ref if isinstance(raw_rule_ref, dict) else {}
    ref_index = _nonneg_int_or_none(rule_ref.get("index"))
    res_idx = (
        ref_index
        if ref_index is not None
        else _nonneg_int_or_none(result.get("ruleIndex"))
    )

    for inv in ordered:
        overrides = (
            inv.get("ruleConfigurationOverrides") if isinstance(inv, dict) else None
        )
        if not isinstance(overrides, list):
            continue
        for ov in overrides:
            level = _override_entry_level(ov, rule_id, res_idx)
            if level:
                return level
    return None


def _resolve_sarif_level(
    result: dict[str, Any],
    matched_rule: dict[str, Any] | None,
    run: dict[str, Any],
    rule_id: str | None,
) -> str:
    kind = result.get("kind")
    if isinstance(kind, str) and kind in _NON_FAIL_RESULT_KINDS:
        return "none"
    explicit = result.get("level")
    if isinstance(explicit, str) and explicit in _VALID_SARIF_LEVELS:
        return explicit
    override_level = _find_override_level(result, run, rule_id)
    if override_level:
        return override_level
    default_cfg = (
        matched_rule.get("defaultConfiguration")
        if isinstance(matched_rule, dict)
        else None
    )
    rule_level = default_cfg.get("level") if isinstance(default_cfg, dict) else None
    if isinstance(rule_level, str) and rule_level in _VALID_SARIF_LEVELS:
        return rule_level
    return "warning"


def _symbol_from_logical_record(record: dict[str, Any]) -> str | None:
    for field in ("name", "fullyQualifiedName", "decoratedName"):
        val = record.get(field)
        if isinstance(val, str) and val:
            return val
    return None


def _extract_logical_symbol(
    location_item: dict[str, Any], run: dict[str, Any]
) -> str | None:
    logical_list = location_item.get("logicalLocations")
    if not isinstance(logical_list, list):
        return None
    run_logical = run.get("logicalLocations")
    for entry in logical_list:
        if not isinstance(entry, dict):
            continue
        direct = _symbol_from_logical_record(entry)
        if direct:
            return direct
        idx = _nonneg_int_or_none(entry.get("index"))
        if (
            idx is not None
            and isinstance(run_logical, list)
            and idx < len(run_logical)
            and isinstance(run_logical[idx], dict)
        ):
            indexed = _symbol_from_logical_record(run_logical[idx])
            if indexed:
                return indexed
    return None


def _extract_security_severity(
    result: dict[str, Any], matched_rule: dict[str, Any] | None
) -> str | float | int | None:
    for obj in (result, matched_rule):
        props = obj.get("properties") if isinstance(obj, dict) else None
        if not isinstance(props, dict):
            continue
        sec_sev = props.get("security-severity")
        if (
            isinstance(sec_sev, (str, int, float))
            and not isinstance(sec_sev, bool)
            and sec_sev != ""
        ):
            return sec_sev
    return None


def _clean_fingerprint_map(raw_map: object) -> dict[str, str] | None:
    if not isinstance(raw_map, dict):
        return None
    cleaned = {
        k: v
        for k, v in raw_map.items()
        if isinstance(k, str) and k and isinstance(v, str) and v
    }
    return cleaned or None


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _build_location_record(
    item: dict[str, Any],
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    explicit_roots: list[str] | None,
    sarif_roots: list[str],
) -> dict[str, Any] | None:
    raw_physical = item.get("physicalLocation")
    physical: dict[str, Any] = raw_physical if isinstance(raw_physical, dict) else {}
    norm_path = _normalize_sarif_artifact_path(
        physical.get("artifactLocation"),
        run,
        base_map,
        explicit_roots,
        sarif_roots,
    )
    if not norm_path:
        return None
    raw_region = physical.get("region")
    region: dict[str, Any] = raw_region if isinstance(raw_region, dict) else {}
    start_line = _positive_int_or_none(region.get("startLine"))
    end_line = _positive_int_or_none(region.get("endLine"))
    loc_record: dict[str, Any] = {
        "path": norm_path,
        "line": start_line,
    }
    if start_line is not None and end_line is not None and end_line >= start_line:
        loc_record["end_line"] = end_line
    symbol = _extract_logical_symbol(item, run)
    if symbol:
        loc_record["symbol"] = symbol
    return loc_record


def _extract_result_locations(
    raw_locations: object,
    sarif_path: Path,
    run: dict[str, Any],
    base_map: dict[str, ParsedSarifUri | None],
    explicit_roots: list[str] | None,
    sarif_roots: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw_locations, list):
        raise FredCliError(
            f"Malformed SARIF file '{sarif_path}': 'locations' in result must be a JSON array",
            code="invalid_sarif",
            hint="Ensure each SARIF result's 'locations' property is a JSON array.",
        )
    locations: list[dict[str, Any]] = []
    for item in raw_locations:
        if not isinstance(item, dict):
            continue
        loc_record = _build_location_record(
            item, run, base_map, explicit_roots, sarif_roots
        )
        if loc_record is not None:
            locations.append(loc_record)
    return locations


def _normalize_active_suppression(
    sup: dict[str, Any],
) -> tuple[bool, dict[str, str] | None]:
    status = sup.get("status")
    if status is None or status == "" or status == "accepted":
        return True, None
    if not isinstance(status, str) or status not in _ACTIVE_SUPPRESSION_STATUSES:
        return False, None
    raw_kind = sup.get("kind")
    sup_kind = (
        raw_kind
        if isinstance(raw_kind, str) and raw_kind in _VALID_SUPPRESSION_KINDS
        else "external"
    )
    entry: dict[str, str] = {"kind": sup_kind, "status": status}
    justification = sup.get("justification")
    if isinstance(justification, str):
        entry["justification"] = justification
    guid = sup.get("guid")
    if isinstance(guid, str) and guid:
        entry["guid"] = guid
    return False, entry


def _inspect_result_suppressions(
    result: dict[str, Any], sarif_path: Path
) -> tuple[bool, list[dict[str, str]]]:
    raw_suppressions = result.get("suppressions")
    if raw_suppressions is None:
        return False, []
    if not isinstance(raw_suppressions, list):
        raise FredCliError(
            f"Malformed SARIF file '{sarif_path}': 'suppressions' in result must be a JSON array",
            code="invalid_sarif",
            hint="Ensure each SARIF result's 'suppressions' property is a JSON array.",
        )
    active: list[dict[str, str]] = []
    for item in raw_suppressions:
        if not isinstance(item, dict):
            continue
        accepted, normalized = _normalize_active_suppression(item)
        if accepted:
            return True, []
        if normalized is not None:
            active.append(normalized)
    return False, active


def _copy_nonempty_str_props(
    source: object, keys: tuple[str, ...], dest: dict[str, str]
) -> None:
    if not isinstance(source, dict):
        return
    for key in keys:
        val = source.get(key)
        if key not in dest and isinstance(val, str) and val:
            dest[key] = val


def _extract_audit_properties(
    result: dict[str, Any], matched_rule: dict[str, Any] | None
) -> dict[str, str] | None:
    extracted: dict[str, str] = {}
    _copy_nonempty_str_props(result.get("properties"), _AUDIT_PROPERTY_KEYS, extracted)
    if isinstance(matched_rule, dict):
        _copy_nonempty_str_props(
            matched_rule.get("properties"), _RULE_FALLBACK_PROPERTY_KEYS, extracted
        )
    return extracted or None


def _attach_optional_observation_fields(
    observation: dict[str, Any],
    result: dict[str, Any],
    matched_rule: dict[str, Any] | None,
    active_suppressions: list[dict[str, str]],
) -> None:
    raw_kind = result.get("kind")
    if isinstance(raw_kind, str) and raw_kind in _RETAINED_RESULT_KINDS:
        observation["kind"] = raw_kind
    sec_severity = _extract_security_severity(result, matched_rule)
    if sec_severity is not None:
        observation["security_severity"] = sec_severity
    fps = _clean_fingerprint_map(result.get("fingerprints"))
    if fps:
        observation["fingerprints"] = fps
    partial_fps = _clean_fingerprint_map(result.get("partialFingerprints"))
    if partial_fps:
        observation["partial_fingerprints"] = partial_fps
    if active_suppressions:
        observation["suppressions"] = active_suppressions
    audit_props = _extract_audit_properties(result, matched_rule)
    if audit_props:
        observation["properties"] = audit_props


def _resolve_rule_id(
    result: dict[str, Any], matched_rule: dict[str, Any] | None
) -> str:
    raw_rule_ref = result.get("rule")
    rule_ref: dict[str, Any] = raw_rule_ref if isinstance(raw_rule_ref, dict) else {}
    for candidate in (
        result.get("ruleId"),
        rule_ref.get("id"),
        matched_rule.get("id") if isinstance(matched_rule, dict) else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return "unknown"


def _import_sarif_result(
    result: dict[str, Any],
    sarif_path: Path,
    run: dict[str, Any],
    driver: dict[str, Any],
    extensions: list[dict[str, Any]],
    base_map: dict[str, ParsedSarifUri | None],
    explicit_roots: list[str] | None,
    default_sarif_roots: list[str],
) -> dict[str, Any] | None:
    raw_kind = result.get("kind")
    if isinstance(raw_kind, str) and raw_kind in _SKIPPED_RESULT_KINDS:
        return None
    is_suppressed, active_suppressions = _inspect_result_suppressions(
        result, sarif_path
    )
    if is_suppressed:
        return None
    inv_idx = _nonneg_int_or_none(result.get("invocationIndex"))
    sarif_roots = (
        _collect_sarif_roots(run, base_map, inv_idx)
        if inv_idx is not None
        else default_sarif_roots
    )
    matched_rule = _find_sarif_rule(result, driver, extensions)
    rule_id = _resolve_rule_id(result, matched_rule)
    reported_level = _resolve_sarif_level(
        result,
        matched_rule,
        run,
        rule_id if rule_id != "unknown" else None,
    )
    locations = _extract_result_locations(
        result.get("locations", []),
        sarif_path,
        run,
        base_map,
        explicit_roots,
        sarif_roots,
    )
    raw_msg = result.get("message")
    msg_obj: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else {}
    raw_tool = driver.get("name")
    raw_ver = driver.get("version") or driver.get("semanticVersion")
    raw_text = msg_obj.get("text")
    observation: dict[str, Any] = {
        "tool": raw_tool if isinstance(raw_tool, str) and raw_tool else "unknown",
        "tool_version": str(raw_ver) if raw_ver is not None else "unknown",
        "rule": rule_id,
        "reported_level": reported_level,
        "message": str(raw_text) if raw_text is not None else "No text message",
        "locations": locations,
        "evidence_state": "scanner-reported; agent verification not performed",
    }
    _attach_optional_observation_fields(
        observation, result, matched_rule, active_suppressions
    )
    return observation


def _import_sarif_run(
    run: dict[str, Any],
    sarif_path: Path,
    explicit_roots: list[str] | None,
) -> list[dict[str, Any]]:
    raw_tool = run.get("tool")
    tool_obj: dict[str, Any] = raw_tool if isinstance(raw_tool, dict) else {}
    raw_driver = tool_obj.get("driver")
    driver: dict[str, Any] = raw_driver if isinstance(raw_driver, dict) else {}
    raw_exts = tool_obj.get("extensions")
    extensions = (
        [ext for ext in raw_exts if isinstance(ext, dict)]
        if isinstance(raw_exts, list)
        else []
    )
    base_map = _resolve_base_map(run.get("originalUriBaseIds"))
    default_sarif_roots = _collect_sarif_roots(run, base_map)

    raw_results = run.get("results", [])
    if not isinstance(raw_results, list):
        raise FredCliError(
            f"Malformed SARIF file '{sarif_path}': 'results' in run must be a JSON array",
            code="invalid_sarif",
            hint="Ensure each SARIF run's 'results' property is a JSON array.",
        )
    imported: list[dict[str, Any]] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        obs = _import_sarif_result(
            result,
            sarif_path,
            run,
            driver,
            extensions,
            base_map,
            explicit_roots,
            default_sarif_roots,
        )
        if obs is not None:
            imported.append(obs)
    return imported


def import_sarif(path: Path, root: Path | None = None) -> dict:
    document = read_json_object(
        path,
        label="SARIF file",
        hint="Pass a valid SARIF 2.1.0 JSON object containing a 'runs' array.",
    )
    raw_runs = document.get("runs", [])
    if not isinstance(raw_runs, list):
        raise FredCliError(
            f"Malformed SARIF file '{path}': 'runs' must be a JSON array",
            code="invalid_sarif",
            hint="Ensure the SARIF document conforms to SARIF 2.1.0 with a top-level 'runs' array.",
        )
    explicit_roots = _normalize_fs_roots(Path(root)) if root is not None else None
    observations: list[dict[str, Any]] = []
    for run in raw_runs:
        if isinstance(run, dict):
            observations.extend(_import_sarif_run(run, path, explicit_roots))

    return {
        "schema_version": "1.0",
        "observations": observations,
        "limitations": [
            "Imported scanner claims are not confirmed audit findings; original source revision and modeling scope require verification."
        ],
    }

#!/usr/bin/env python3
"""Validate audit structure and cross-record consistency, not correctness of claims."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any

_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "maxLength",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)

_BUNDLE_INDICATOR_KEYS = frozenset(
    {
        "failure_classification",
        "recovery",
        "redaction",
        "scrubbed_transcript",
        "session_id",
        "transcript_discovery",
    }
)


def _is_json_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_json_integer(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    return isinstance(val, int) or (isinstance(val, float) and val.is_integer())


_TYPE_PREDICATES: dict[str, Callable[[Any], bool]] = {
    "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    "integer": _is_json_integer,
    "number": _is_json_number,
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, Mapping),
}


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if _is_json_number(left) and _is_json_number(right):
        return bool(left == right)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left.keys()) == set(right.keys()) and all(
            _json_equal(left[k], right[k]) for k in left
        )
    return type(left) is type(right) and bool(left == right)


def _validate_type_keyword(expected: Any) -> None:
    names = [expected] if isinstance(expected, str) else expected
    if not isinstance(names, list) or not names:
        raise ValueError(f"Invalid JSON Schema type specification: {expected!r}")
    for name in names:
        if not isinstance(name, str) or name not in _TYPE_PREDICATES:
            raise ValueError(f"Unsupported JSON Schema type: {name!r}")


def _matches_type(val: Any, expected: Any) -> bool:
    names = [expected] if isinstance(expected, str) else list(expected)
    return any(_TYPE_PREDICATES[name](val) for name in names)


def _resolve_local_ref(ref: Any, root_schema: Mapping[str, Any]) -> Any:
    if ref == "#":
        return root_schema
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f"Unsupported JSON Schema $ref: {ref!r}")
    target: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            raise ValueError(f"Unresolvable JSON Schema $ref: {ref!r}")
        target = target[part]
    return target


def _validate_schema_scalars(node: Mapping[str, Any]) -> None:
    if "type" in node:
        _validate_type_keyword(node["type"])
    if "enum" in node and not isinstance(node["enum"], list):
        raise ValueError("Invalid JSON Schema enum: expected array")
    if "required" in node:
        req = node["required"]
        if not isinstance(req, list) or not all(isinstance(k, str) for k in req):
            raise ValueError("Invalid JSON Schema required: expected string array")
    if "pattern" in node:
        if not isinstance(node["pattern"], str):
            raise ValueError("Invalid JSON Schema pattern: expected string")
        re.compile(node["pattern"])


def _validate_subschema_map(
    mapping_node: Any, label: str, root_schema: Mapping[str, Any]
) -> None:
    if not isinstance(mapping_node, Mapping):
        raise TypeError(f"Invalid JSON Schema {label}: expected object")
    for sub in mapping_node.values():
        _validate_schema_tree(sub, root_schema)


def _validate_subschema_list(
    list_node: Any, label: str, root_schema: Mapping[str, Any]
) -> None:
    if not isinstance(list_node, list) or not list_node:
        raise ValueError(f"Invalid JSON Schema {label}: expected non-empty array")
    for sub in list_node:
        _validate_schema_tree(sub, root_schema)


def _validate_schema_children(
    node: Mapping[str, Any], root_schema: Mapping[str, Any]
) -> None:
    if "$ref" in node:
        _resolve_local_ref(node["$ref"], root_schema)
    for map_key in ("$defs", "properties"):
        if map_key in node:
            _validate_subschema_map(node[map_key], map_key, root_schema)
    for sub_key in ("items", "additionalProperties"):
        if sub_key in node:
            _validate_schema_tree(node[sub_key], root_schema)
    for comb_key in ("anyOf", "oneOf"):
        if comb_key in node:
            _validate_subschema_list(node[comb_key], comb_key, root_schema)


def _validate_schema_tree(node: Any, root_schema: Mapping[str, Any]) -> None:
    if isinstance(node, bool):
        return
    if not isinstance(node, Mapping):
        raise TypeError("Invalid JSON Schema node: expected object or boolean")
    unknown = sorted(set(node.keys()) - _SUPPORTED_SCHEMA_KEYWORDS)
    if unknown:
        raise ValueError(f"Unsupported JSON Schema keyword(s): {', '.join(unknown)}")
    _validate_schema_scalars(node)
    _validate_schema_children(node, root_schema)


def _format_loc(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "root"


def _eval_type_const_enum(val: Any, node: Mapping[str, Any], loc: str) -> list[str]:
    errors: list[str] = []
    if "type" in node and not _matches_type(val, node["type"]):
        errors.append(f"{loc}: {val!r} is not of type {node['type']!r}")
    if "const" in node and not _json_equal(val, node["const"]):
        errors.append(f"{loc}: {node['const']!r} was expected")
    if "enum" in node and not any(_json_equal(val, opt) for opt in node["enum"]):
        errors.append(f"{loc}: {val!r} is not one of {node['enum']!r}")
    if "minimum" in node and _is_json_number(val) and val < node["minimum"]:
        errors.append(f"{loc}: {val!r} is less than the minimum of {node['minimum']!r}")
    return errors


def _eval_string_keywords(val: Any, node: Mapping[str, Any], loc: str) -> list[str]:
    if not isinstance(val, str):
        return []
    errors: list[str] = []
    if "minLength" in node and len(val) < node["minLength"]:
        errors.append(f"{loc}: {val!r} is too short")
    if "maxLength" in node and len(val) > node["maxLength"]:
        errors.append(f"{loc}: {val!r} is too long")
    if "pattern" in node and re.search(node["pattern"], val) is None:
        errors.append(f"{loc}: {val!r} does not match {node['pattern']!r}")
    return errors


def _eval_array_keywords(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if not isinstance(val, list):
        return []
    errors: list[str] = []
    if "minItems" in node and len(val) < node["minItems"]:
        errors.append(f"{_format_loc(path)}: {val!r} is too short")
    if "items" in node:
        item_schema = node["items"]
        for idx, item in enumerate(val):
            errors.extend(
                _eval_schema(
                    item, item_schema, root_schema, (*path, str(idx)), seen_refs
                )
            )
    return errors


def _eval_additional_props(
    val: Mapping[Any, Any],
    add_props: Any,
    defined: set[Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    extras = [k for k in val if k not in defined]
    if not extras or add_props is True:
        return []
    if add_props is False:
        quoted = ", ".join(repr(str(k)) for k in extras)
        verb = "was" if len(extras) == 1 else "were"
        return [
            f"{_format_loc(path)}: Additional properties are not allowed ({quoted} {verb} unexpected)"
        ]
    errors: list[str] = []
    for key in extras:
        errors.extend(
            _eval_schema(val[key], add_props, root_schema, (*path, str(key)), seen_refs)
        )
    return errors


def _eval_object_keywords(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if not isinstance(val, Mapping):
        return []
    loc = _format_loc(path)
    props = node.get("properties", {})
    errors = [
        f"{loc}: {req_key!r} is a required property"
        for req_key in node.get("required", [])
        if req_key not in val
    ]
    for prop_key, prop_schema in props.items():
        if prop_key in val:
            errors.extend(
                _eval_schema(
                    val[prop_key],
                    prop_schema,
                    root_schema,
                    (*path, str(prop_key)),
                    seen_refs,
                )
            )
    if "additionalProperties" in node:
        errors.extend(
            _eval_additional_props(
                val,
                node["additionalProperties"],
                set(props.keys()),
                root_schema,
                path,
                seen_refs,
            )
        )
    return errors


def _eval_combinators(
    val: Any,
    node: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    loc = _format_loc(path)
    errors: list[str] = []
    if "anyOf" in node and not any(
        not _eval_schema(val, sub, root_schema, path, seen_refs)
        for sub in node["anyOf"]
    ):
        errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
    if "oneOf" in node:
        matches = sum(
            1
            for sub in node["oneOf"]
            if not _eval_schema(val, sub, root_schema, path, seen_refs)
        )
        if matches == 0:
            errors.append(f"{loc}: {val!r} is not valid under any of the given schemas")
        elif matches > 1:
            errors.append(f"{loc}: {val!r} is valid under multiple given schemas")
    return errors


def _eval_schema(
    val: Any,
    node: Any,
    root_schema: Mapping[str, Any],
    path: tuple[str, ...],
    seen_refs: frozenset[tuple[tuple[str, ...], str]],
) -> list[str]:
    if isinstance(node, bool):
        return (
            []
            if node
            else [f"{_format_loc(path)}: False schema does not allow {val!r}"]
        )
    errors: list[str] = []
    if "$ref" in node:
        ref = str(node["$ref"])
        ref_token = (path, ref)
        if ref_token in seen_refs:
            raise ValueError(f"Cyclic JSON Schema $ref: {ref!r}")
        target = _resolve_local_ref(ref, root_schema)
        errors.extend(
            _eval_schema(val, target, root_schema, path, seen_refs | {ref_token})
        )
    loc = _format_loc(path)
    errors.extend(_eval_type_const_enum(val, node, loc))
    errors.extend(_eval_string_keywords(val, node, loc))
    errors.extend(_eval_array_keywords(val, node, root_schema, path, seen_refs))
    errors.extend(_eval_object_keywords(val, node, root_schema, path, seen_refs))
    errors.extend(_eval_combinators(val, node, root_schema, path, seen_refs))
    return errors


def _schema_errors(data: Any, schema: Mapping[str, Any]) -> list[str]:
    _validate_schema_tree(schema, schema)
    return _eval_schema(data, schema, schema, (), frozenset())


def _is_diagnostic_bundle_payload(data: Any) -> bool:
    if not isinstance(data, Mapping) or "findings" in data:
        return False
    return bool(_BUNDLE_INDICATOR_KEYS & set(data.keys()))


def _select_schema_from_assets(
    assets_dir: pathlib.Path, want_bundle: bool
) -> pathlib.Path | None:
    preferred = "bundle.schema.json" if want_bundle else "audit.schema.json"
    direct = assets_dir / preferred
    if direct.is_file():
        return direct
    candidates = sorted(
        p
        for p in assets_dir.glob("*.schema.json")
        if not p.name.startswith("evaluation-scenarios")
    )
    return candidates[0] if candidates else None


def _resolve_schema_path(
    explicit_schema: pathlib.Path | None, data: Any
) -> pathlib.Path:
    if explicit_schema is not None:
        return explicit_schema
    want_bundle = _is_diagnostic_bundle_payload(data)
    parent_dir = pathlib.Path(__file__).resolve().parents[1]
    assets_dir = parent_dir / "assets"
    if assets_dir.is_dir():
        selected = _select_schema_from_assets(assets_dir, want_bundle)
        if selected is not None:
            return selected
    repo_fallback = (
        parent_dir / "skills/repo-audit-diagnostics/assets/bundle.schema.json"
        if want_bundle
        else parent_dir / "shared/repo-audit/resources/assets/audit.schema.json"
    )
    if repo_fallback.is_file():
        return repo_fallback
    return assets_dir / ("bundle.schema.json" if want_bundle else "audit.schema.json")


def _index_field(
    items: list[Any], field: str, errors: list[str]
) -> dict[str, Mapping[str, Any]]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in seen and item_id not in duplicates:
            errors.append(f"Duplicate IDs in {field}: {item_id}")
            duplicates.add(item_id)
        seen.add(item_id)
        indexed[item_id] = item
    return indexed


def _index_records(
    data: Mapping[str, Any], errors: list[str]
) -> dict[str, dict[str, Mapping[str, Any]]]:
    return {
        field: _index_field(data.get(field, []), field, errors)
        for field in ("evidence", "contracts", "findings", "issues")
    }


def _check_refs(
    record: Mapping[str, Any],
    field: str,
    target: str,
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    rid = record.get("id") or record.get("topic") or "unknown"
    target_map = maps.get(target, {})
    return [
        f"Unknown {target} reference {value} in {rid} ({field})"
        for value in record.get(field, [])
        if value not in target_map
    ]


def _is_repo_relative_evidence_path(raw_path: str) -> bool:
    if not raw_path or "\\" in raw_path:
        return False
    p = pathlib.PurePosixPath(raw_path)
    win_p = pathlib.PureWindowsPath(raw_path)
    if p.is_absolute() or win_p.is_absolute() or bool(win_p.drive):
        return False
    return bool(p.parts) and ".." not in p.parts and ".git" not in p.parts


def _check_evidence(evidence_items: list[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for ev in evidence_items:
        if ev["line_end"] < ev["line_start"]:
            errors.append(f"Reversed lines: {ev['id']}")
        if not _is_repo_relative_evidence_path(ev["path"]):
            errors.append(f"Evidence path must be repository-relative: {ev['id']}")
    return errors


def _check_contract_and_coverage_refs(
    data: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for contract in data.get("contracts", []):
        errors.extend(_check_refs(contract, "evidence_ids", "evidence", maps))
    for cov in data.get("coverage", []):
        errors.extend(_check_refs(cov, "evidence_ids", "evidence", maps))
    return errors


def _check_single_finding(
    finding: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors = _check_refs(finding, "evidence_ids", "evidence", maps)
    errors.extend(_check_refs(finding, "contract_ids", "contracts", maps))
    errors.extend(_check_refs(finding, "related_findings", "findings", maps))
    status = finding["status"]
    if status in ("confirmed", "supported") and (
        not finding["evidence_ids"] or not finding["contract_ids"]
    ):
        errors.append(f"Actionable defect lacks contract/evidence: {finding['id']}")
    if status == "confirmed" and (
        finding["validation"]["state"] != "executed"
        or not finding["validation"]["commands"]
    ):
        errors.append(f"Confirmed finding lacks executed command: {finding['id']}")
    if status == "refuted" and finding["severity"] != "info":
        errors.append(f"Refuted candidate must be info: {finding['id']}")
    return errors


def _check_findings(
    findings: list[Mapping[str, Any]],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for finding in findings:
        errors.extend(_check_single_finding(finding, maps))
    return errors


def _check_single_issue(
    issue: Mapping[str, Any],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors = _check_refs(issue, "finding_ids", "findings", maps)
    errors.extend(_check_refs(issue, "depends_on", "issues", maps))
    findings_map = maps["findings"]
    errors.extend(
        f"Issue includes refuted finding: {issue['id']}"
        for fid in issue["finding_ids"]
        if fid in findings_map and findings_map[fid]["status"] == "refuted"
    )
    if issue["publication"] in ("created", "linked") and not issue["existing_url"]:
        errors.append(f"Published issue lacks verified URL: {issue['id']}")
    return errors


def _check_issues(
    issues: list[Mapping[str, Any]],
    maps: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[str]:
    errors: list[str] = []
    for issue in issues:
        errors.extend(_check_single_issue(issue, maps))
    return errors


def _visit_issue(
    issue_id: str,
    issues_map: Mapping[str, Mapping[str, Any]],
    visiting: set[str],
    done: set[str],
    errors: list[str],
) -> None:
    if issue_id in visiting:
        errors.append(f"Issue dependency cycle: {issue_id}")
        return
    if issue_id in done or issue_id not in issues_map:
        return
    visiting.add(issue_id)
    for dep in issues_map[issue_id]["depends_on"]:
        _visit_issue(dep, issues_map, visiting, done, errors)
    visiting.remove(issue_id)
    done.add(issue_id)


def _find_issue_cycles(issues_map: Mapping[str, Mapping[str, Any]]) -> list[str]:
    visiting: set[str] = set()
    done: set[str] = set()
    errors: list[str] = []
    for issue_id in issues_map:
        _visit_issue(issue_id, issues_map, visiting, done, errors)
    return errors


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    errors = _schema_errors(data, schema)
    if errors or not isinstance(data, Mapping):
        return errors
    if schema.get("title") == "AuditSessionDiagnosticBundle":
        return errors
    maps = _index_records(data, errors)
    errors.extend(_check_evidence(data.get("evidence", [])))
    errors.extend(_check_contract_and_coverage_refs(data, maps))
    errors.extend(_check_findings(data.get("findings", []), maps))
    errors.extend(_check_issues(data.get("issues", []), maps))
    errors.extend(_find_issue_cycles(maps.get("issues", {})))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=pathlib.Path)
    parser.add_argument("--schema", type=pathlib.Path, default=None)
    a = parser.parse_args()
    try:
        payload = json.loads(a.audit.read_text(encoding="utf-8"))
        schema_path = _resolve_schema_path(a.schema, payload)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = validate(payload, schema)
    except (OSError, TypeError, ValueError) as e:
        print(f"Invalid input: {e}", file=sys.stderr)
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

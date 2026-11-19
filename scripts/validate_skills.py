"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

import jsonschema
import yaml
from skill_tools import (
    ROOT,
    estimate_tokens,
    extract_relative_links,
    find_deep_reference_chains,
    read_frontmatter,
    resolve_relative_link_target,
    suite_members,
)

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_PROMPT_CHARS = 1200
MAX_PROMPT_LINES = 20
MAX_METADATA_TOKENS = 100
MAX_BODY_TOKENS = 5000
MAX_REFERENCE_TOKENS = 5000
MIN_WORKED_EXAMPLE_BLOCKS = 2
FENCE_OPEN_PATTERN = re.compile(r"^\s*```([A-Za-z0-9_+-]*)\s*$")
FENCE_CLOSE_PATTERN = re.compile(r"^\s*```\s*$")
PLACEHOLDER_PATTERN = re.compile(r"<[^>]+>")
ALLOWED_PROMPT_PLACEHOLDERS = frozenset(
    {
        "<audit workspace/reports>",
        "<critical workflows>",
        "<existing audit workspace, if any>",
        "<existing audit workspace>",
        "<finding/run artifacts>",
        "<path-to-diagnostic-bundle-output>",
        "<path-to-run-workspace-directory>",
        "<path-to-session-transcript>",
        "<repository/revision>",
        "<repository>",
        "<revision>",
    }
)
ALLOWED_PLACEHOLDERS = ALLOWED_PROMPT_PLACEHOLDERS

NEGATIVE_CONSTRAINT_PATTERN = re.compile(
    r"\b(do not|never|must not|cannot|should not|prohibit\w*|forbid\w*|avoid\w*|disallow\w*)\b",
    re.IGNORECASE,
)
GUARDRAIL_HEADING_PATTERN = re.compile(
    r"^##+\s+.*\b(guardrail\w*|prohibit\w*|constraint\w*|non-goal\w*|out of scope)\b",
    re.IGNORECASE,
)
DELIVERY_HEADING_PATTERN = re.compile(
    r"^##+\s+.*\b(deliver\w*|produce)\b",
    re.IGNORECASE,
)
BOUNDARY_SECTION_PATTERN = re.compile(
    r"^##+\s+.*\b(guardrail\w*|prohibit\w*|constraint\w*|non-goal\w*|out of scope|deliver\w*|produce)\b",
    re.IGNORECASE | re.MULTILINE,
)
PROMPT_HEADING_PATTERN = re.compile(r"^\s*#+", re.MULTILINE)
REPEATED_WORD_PATTERN = re.compile(r"\b([A-Za-z0-9]+)\s+\1\b", re.IGNORECASE)
GENERIC_ROUTING_PROMPT_PATTERN = re.compile(
    r"\b(spelling\s+mistake|fix\s+a\s+typo|\btypo\b|proofread\b|grammar\s+check|"
    r"project\s+description\s+typo|readme\s+spelling)\b",
    re.IGNORECASE,
)
LEGACY_AUDIT_ACTIVATION_NOTE_PATTERN = re.compile(
    r"not\s+an\s+instruction\s+to\s+perform\s+an\s+audit",
    re.IGNORECASE,
)
AUDIT_WORD_PATTERN = re.compile(r"\baudit\b", re.IGNORECASE)
WORKFLOW_MISPLACED_AUDIT_NOTE_PATTERN = re.compile(
    r"\b(perform\s+an\s+audit|audit\s+specialist|audit\s+activation)\b",
    re.IGNORECASE,
)
EXPECTED_FINDINGS_NOTE_PATTERN = re.compile(
    r"\bexpected\s+findings\b",
    re.IGNORECASE,
)


def _validate_ui_icon(package: Path, field: str, raw_value: object) -> list[str]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return [f"Missing or invalid UI {field}"]
    if (
        "\\" in raw_value
        or raw_value.startswith("/")
        or Path(raw_value).is_absolute()
        or PureWindowsPath(raw_value).is_absolute()
    ):
        return [f"UI {field} must be a relative package-local SVG path: {raw_value}"]
    candidate = package / raw_value
    resolved_package = package.resolve()
    try:
        resolved_candidate = candidate.resolve()
    except OSError as exc:
        return [f"UI {field} could not be resolved ({raw_value}): {exc}"]
    if not resolved_candidate.is_relative_to(resolved_package):
        return [f"UI {field} escapes standalone package: {raw_value}"]
    if candidate.is_symlink():
        return [f"UI {field} must not be a symlink: {raw_value}"]
    if candidate.suffix.lower() != ".svg":
        return [f"UI {field} must point to an .svg file: {raw_value}"]
    if not candidate.is_file():
        return [f"Missing UI {field} file: {raw_value}"]
    try:
        svg_root = ET.fromstring(candidate.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as exc:
        return [f"Invalid SVG in UI {field} ({raw_value}): {exc}"]
    tag = svg_root.tag.rsplit("}", 1)[-1]
    if tag != "svg":
        return [f"Invalid SVG root element in UI {field} ({raw_value}): {tag}"]
    return []


def _infer_suite_and_siblings(package: Path, skill_name: str) -> tuple[str, set[str]]:
    candidate_root = package.parent.parent
    suites_file = candidate_root / "catalog/suites.json"
    if suites_file.is_file():
        try:
            members = suite_members(candidate_root)
            if skill_name in members:
                suite_name = str(members[skill_name]["suite"])
                siblings = {
                    s
                    for s, info in members.items()
                    if info.get("suite") == suite_name and s != skill_name
                }
                return suite_name, siblings
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if skill_name.startswith("arch-"):
        return "software-architecture", set()
    if skill_name.startswith("review-"):
        return "code-review", set()
    if skill_name.startswith("test-"):
        return "automated-testing", set()
    if skill_name.startswith("repo-") and not skill_name.startswith("repo-audit-"):
        return "repository-workflows", set()
    return "repository-audits", set()


def _validate_scenario_semantics(
    package: Path, skill_name: str, scenarios: list[object]
) -> list[str]:
    problems: list[str] = []
    suite_name, siblings = _infer_suite_and_siblings(package, skill_name)
    non_routing_prompts: set[str] = set()
    routing_scenarios: list[dict] = []

    for item in scenarios:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", ""))
        prompt = str(item.get("prompt", ""))
        note = str(item.get("evaluation_note", ""))
        kind = sid.rsplit("-", 1)[-1] if "-" in sid else ""

        if GENERIC_ROUTING_PROMPT_PATTERN.search(prompt):
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): generic typo or proofreading prompt is disallowed; use a near-neighbor sibling prompt"
            )

        if LEGACY_AUDIT_ACTIVATION_NOTE_PATTERN.search(note):
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): legacy audit activation note must be replaced with a suite-accurate sibling routing note"
            )

        if suite_name in {
            "software-architecture",
            "code-review",
            "automated-testing",
        } and AUDIT_WORD_PATTERN.search(note):
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): misplaced audit terminology in {suite_name} evaluation_note"
            )
        elif (
            suite_name == "repository-workflows"
            and WORKFLOW_MISPLACED_AUDIT_NOTE_PATTERN.search(note)
        ):
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): misplaced audit activation note in repository-workflows evaluation_note"
            )

        if suite_name in {
            "software-architecture",
            "automated-testing",
            "repository-workflows",
        } and EXPECTED_FINDINGS_NOTE_PATTERN.search(note):
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): misplaced 'expected findings' note in {suite_name}; reference the suite contract instead"
            )

        if kind == "routing":
            routing_scenarios.append(item)
        elif prompt.strip():
            non_routing_prompts.add(prompt.strip())

    for r_item in routing_scenarios:
        sid = str(r_item.get("id", ""))
        r_prompt = str(r_item.get("prompt", "")).strip()
        sibling = r_item.get("sibling_skill")
        r_note = str(r_item.get("evaluation_note", ""))
        r_behaviors = " ".join(
            str(b) for b in r_item.get("expected_behavior", []) if isinstance(b, str)
        )
        if not isinstance(sibling, str) or not sibling.strip():
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): routing scenario requires sibling_skill"
            )
            continue
        if sibling == skill_name:
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): sibling_skill '{sibling}' must differ from '{skill_name}'"
            )
        elif siblings and sibling not in siblings:
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): sibling_skill '{sibling}' is not a member of suite '{suite_name}'"
            )
        if r_prompt in non_routing_prompts:
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): routing prompt must target a sibling boundary rather than repeating the skill's own prompt"
            )
        if sibling not in r_note and sibling not in r_behaviors:
            problems.append(
                f"references/evaluation-scenarios.json ({sid}): routing evaluation_note or expected_behavior must reference sibling_skill '{sibling}'"
            )

    return problems


_SCENARIO_SCHEMA: dict | None = None
_VALIDATED_SCHEMA_TEXTS: set[str] = set()


def _check_draft202012_schema(raw_text: str, data: object) -> None:
    """Validate a JSON Schema against Draft 2020-12, caching valid schema texts."""
    if raw_text in _VALIDATED_SCHEMA_TEXTS:
        return
    jsonschema.Draft202012Validator.check_schema(data)  # type: ignore[arg-type]
    _VALIDATED_SCHEMA_TEXTS.add(raw_text)


def get_scenario_schema(schema_path: Path | None = None) -> dict | None:
    global _SCENARIO_SCHEMA
    if schema_path is not None:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    if _SCENARIO_SCHEMA is None:
        default_path = ROOT / "schemas/evaluation-scenarios.schema.json"
        if default_path.is_file():
            _SCENARIO_SCHEMA = json.loads(default_path.read_text(encoding="utf-8"))
    return _SCENARIO_SCHEMA


def _check_skill_guardrails(body: str) -> list[str]:
    """Validate explicit negative prohibitions and guardrail/delivery placement in SKILL.md."""
    issues: list[str] = []
    has_prohibition = bool(NEGATIVE_CONSTRAINT_PATTERN.search(body))
    if not has_prohibition:
        issues.append(
            "SKILL.md: skill markdown lacks explicit negative prohibitions "
            "(e.g. 'do not', 'never', 'must not')"
        )

    sections = [
        sec for sec in re.split(r"(?=^##+\s+)", body, flags=re.MULTILINE) if sec.strip()
    ]
    guardrail_sections = [
        sec for sec in sections if GUARDRAIL_HEADING_PATTERN.match(sec)
    ]
    delivery_sections = [sec for sec in sections if DELIVERY_HEADING_PATTERN.match(sec)]

    if not guardrail_sections and not delivery_sections:
        issues.append(
            "SKILL.md: skill markdown lacks boundary sections "
            "(require a dedicated ## Guardrails heading or ## Deliver section)"
        )
        return issues

    guardrail_enforced = any(
        NEGATIVE_CONSTRAINT_PATTERN.search(sec) for sec in guardrail_sections
    )
    delivery_enforced = any(
        NEGATIVE_CONSTRAINT_PATTERN.search(sec) for sec in delivery_sections
    )
    if not (guardrail_enforced or delivery_enforced):
        issues.append(
            "SKILL.md: require a dedicated guardrail heading with prohibitions "
            "or explicit negative prohibitions inside the delivery section"
        )
    return issues


def _prompt_mentions_schema(prompt_content: str, output_schemas: list[Path]) -> bool:
    """Return True when prompt.md references the skill's output schema contract."""
    if not output_schemas:
        return False
    if re.search(r"\boutput\s+schema\b", prompt_content, re.IGNORECASE):
        return True
    for schema_path in output_schemas:
        filename = schema_path.name
        if re.search(re.escape(filename), prompt_content, re.IGNORECASE):
            return True
        stem = filename.removesuffix(".schema.json")
        stem_words = re.escape(stem).replace(r"\-", r"[\s\-]+")
        stem_pattern = (
            rf"\b{stem_words}\s+(?:template\s+and\s+)?schema\b"
            rf"|\bschema\s+for\s+{stem_words}\b"
        )
        if re.search(stem_pattern, prompt_content, re.IGNORECASE):
            return True
    return False


def _validate_closed_schema(data: object, schema_rel: str) -> list[str]:
    """Reject empty/open schemas and require additionalProperties: false on objects with properties."""
    if not isinstance(data, dict) or not data:
        return [f"{schema_rel}: empty or non-object JSON schema is not allowed"]

    schema_errors: list[str] = []
    root_props = data.get("properties")
    if not isinstance(root_props, dict) or not root_props:
        schema_errors.append(
            f"{schema_rel}: root schema must define non-empty 'properties'"
        )

    def _walk(node: object, json_path: str) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            if node.get("additionalProperties") is not False:
                schema_errors.append(
                    f"{schema_rel}: structured object at {json_path} with 'properties' "
                    "must set 'additionalProperties': false"
                )
            for prop_name, prop_schema in props.items():
                _walk(prop_schema, f"{json_path}.properties.{prop_name}")

        for map_key in (
            "$defs",
            "definitions",
            "patternProperties",
            "dependentSchemas",
        ):
            sub_map = node.get(map_key)
            if isinstance(sub_map, dict):
                for sub_name, sub_schema in sub_map.items():
                    _walk(sub_schema, f"{json_path}.{map_key}.{sub_name}")

        for single_key in (
            "items",
            "additionalProperties",
            "not",
            "if",
            "then",
            "else",
            "contains",
            "propertyNames",
            "unevaluatedItems",
            "unevaluatedProperties",
        ):
            sub_node = node.get(single_key)
            if isinstance(sub_node, dict):
                _walk(sub_node, f"{json_path}.{single_key}")

        for list_key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            sub_list = node.get(list_key)
            if isinstance(sub_list, list):
                for idx, item_schema in enumerate(sub_list):
                    _walk(item_schema, f"{json_path}.{list_key}[{idx}]")

    _walk(data, "$")
    return schema_errors


def _discover_output_schemas(package: Path) -> tuple[list[Path], list[Path]]:
    """Return (assets_schemas, effective_output_schemas) for package."""
    assets_schemas = sorted(
        p
        for p in package.glob("assets/*.schema.json")
        if not p.name.startswith("evaluation-scenarios")
    )
    if assets_schemas:
        return assets_schemas, assets_schemas
    fallback_schemas = sorted(
        p
        for p in package.glob("*.schema.json")
        if not p.name.startswith("evaluation-scenarios")
    )
    return assets_schemas, fallback_schemas


def _extract_fenced_blocks(markdown_text: str) -> tuple[list[tuple[str, str]], bool]:
    """Return ([(lang, code_body), ...], has_unclosed_fence) from Markdown text."""
    blocks: list[tuple[str, str]] = []
    in_fence = False
    fence_lang = ""
    current_lines: list[str] = []
    for raw_line in markdown_text.splitlines():
        if not in_fence:
            open_match = FENCE_OPEN_PATTERN.match(raw_line)
            if open_match:
                in_fence = True
                fence_lang = open_match.group(1).strip().lower()
                current_lines = []
        else:
            if FENCE_CLOSE_PATTERN.match(raw_line):
                blocks.append((fence_lang, "\n".join(current_lines)))
                in_fence = False
                fence_lang = ""
                current_lines = []
            else:
                current_lines.append(raw_line)
    return blocks, in_fence


def _extract_noncode_lines(markdown_text: str) -> list[str]:
    """Return non-empty stripped prose/heading lines outside fenced code blocks."""
    noncode: list[str] = []
    in_fence = False
    for raw_line in markdown_text.splitlines():
        if not in_fence:
            if FENCE_OPEN_PATTERN.match(raw_line):
                in_fence = True
                continue
            stripped = raw_line.strip()
            if stripped:
                noncode.append(stripped)
        elif FENCE_CLOSE_PATTERN.match(raw_line):
            in_fence = False
    return noncode


def _load_valid_output_validators(
    package: Path, output_schemas: Sequence[Path]
) -> list[tuple[str, jsonschema.Draft202012Validator]]:
    validators: list[tuple[str, jsonschema.Draft202012Validator]] = []
    for schema_path in output_schemas:
        if not schema_path.is_file() or schema_path.is_symlink():
            continue
        rel_posix = schema_path.relative_to(package).as_posix()
        try:
            raw_json = schema_path.read_text(encoding="utf-8")
            schema_data = json.loads(raw_json)
            if not isinstance(schema_data, dict) or not schema_data:
                continue
            _check_draft202012_schema(raw_json, schema_data)
            if _validate_closed_schema(schema_data, rel_posix):
                continue
            validators.append(
                (
                    rel_posix,
                    jsonschema.Draft202012Validator(
                        schema_data,
                        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
                    ),
                )
            )
        except (OSError, ValueError, jsonschema.SchemaError):
            continue
    return validators


def _validate_worked_examples(
    package: Path,
    resolved_skill_links: set[Path],
    output_schemas: Sequence[Path],
    skill_raw_text: str,
) -> tuple[list[str], int, bool]:
    """Validate worked-example presence, link reachability, fenced block count, and JSON schema conformance."""
    errors: list[str] = []
    candidates = [
        package / "references/method.md",
        package / "references/examples.md",
    ]
    present_files = [
        p for p in candidates if p.exists() and not p.is_symlink() and p.is_file()
    ]
    if not present_files:
        errors.append(
            "Missing worked examples: require references/examples.md or references/method.md "
            f"with >= {MIN_WORKED_EXAMPLE_BLOCKS} fenced code blocks"
        )
        return errors, 0, False

    total_blocks = 0
    json_blocks: list[tuple[str, int, str]] = []
    skill_noncode_set = {
        ln.strip()
        for ln in skill_raw_text.splitlines()
        if ln.strip() and ln.strip() != "---"
    }

    for ref_file in present_files:
        rel_posix = ref_file.relative_to(package).as_posix()
        if ref_file.resolve() not in resolved_skill_links:
            errors.append(
                f"SKILL.md: missing markdown link to worked examples {rel_posix}"
            )
        try:
            text = ref_file.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{rel_posix}: {exc}")
            continue

        blocks, unclosed = _extract_fenced_blocks(text)
        if unclosed:
            errors.append(
                f"{rel_posix}: unclosed triple-backtick code fence in worked examples"
            )
        total_blocks += len(blocks)
        for idx, (lang, code_body) in enumerate(blocks, start=1):
            if lang == "json":
                json_blocks.append((rel_posix, idx, code_body))

        if ref_file.name == "method.md":
            duplicated_from_skill = [
                ln for ln in _extract_noncode_lines(text) if ln in skill_noncode_set
            ]
            if duplicated_from_skill:
                errors.append(
                    f"{rel_posix}: method.md repeats SKILL.md prose verbatim "
                    f"(e.g. {duplicated_from_skill[0]!r})"
                )

    present_rels = ", ".join(p.relative_to(package).as_posix() for p in present_files)
    if total_blocks < MIN_WORKED_EXAMPLE_BLOCKS:
        errors.append(
            f"Worked examples require at least {MIN_WORKED_EXAMPLE_BLOCKS} fenced code blocks "
            f"across {present_rels} (found {total_blocks})"
        )
    if not json_blocks:
        errors.append(
            f"Worked examples require at least 1 fenced json output example in {present_rels}"
        )
    else:
        schema_validators = _load_valid_output_validators(package, output_schemas)
        for rel_posix, block_idx, raw_json_block in json_blocks:
            try:
                parsed_json = json.loads(raw_json_block)
            except ValueError as exc:
                errors.append(
                    f"{rel_posix}: invalid JSON in worked example block #{block_idx}: {exc}"
                )
                continue
            if schema_validators:
                matched = False
                sample_failure = ""
                for schema_rel, validator in schema_validators:
                    block_errs = sorted(
                        validator.iter_errors(parsed_json),
                        key=lambda e: str(list(e.absolute_path)),
                    )
                    if not block_errs:
                        matched = True
                        break
                    if not sample_failure:
                        first = block_errs[0]
                        loc = "/".join(map(str, first.absolute_path)) or "root"
                        sample_failure = f"{schema_rel} -> {loc}: {first.message}"
                if not matched:
                    errors.append(
                        f"{rel_posix}: worked example json block #{block_idx} fails output schema validation "
                        f"({sample_failure})"
                    )

    has_valid_examples = (
        len(errors) == 0
        and total_blocks >= MIN_WORKED_EXAMPLE_BLOCKS
        and len(json_blocks) >= 1
    )
    return errors, total_blocks, has_valid_examples


def _validate_protocol_depth(package: Path, skill_raw_text: str) -> list[str]:
    """Require protocol reference files (references/*protocol.md) to exceed SKILL.md depth."""
    errors: list[str] = []
    refs_dir = package / "references"
    if not refs_dir.is_dir():
        return errors
    skill_lines = len(skill_raw_text.splitlines())
    skill_chars = len(skill_raw_text)
    for proto_path in sorted(refs_dir.glob("*protocol.md")):
        if proto_path.is_symlink() or not proto_path.is_file():
            continue
        rel_posix = proto_path.relative_to(package).as_posix()
        try:
            proto_text = proto_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{rel_posix}: {exc}")
            continue
        proto_lines = len(proto_text.splitlines())
        proto_chars = len(proto_text)
        if proto_lines <= skill_lines or proto_chars <= skill_chars:
            errors.append(
                f"{rel_posix}: reference inversion detected "
                f"({proto_lines} lines / {proto_chars} chars <= SKILL.md {skill_lines} lines / {skill_chars} chars); "
                "protocol reference must exceed SKILL.md depth"
            )
    return errors


def _validate_bundled_scripts(package: Path) -> tuple[list[str], bool]:
    """Require at least one executable scripts/validate_*.py validator and valid shebang/mode on scripts/*.py."""
    errors: list[str] = []
    scripts_dir = package / "scripts"
    py_scripts = (
        sorted(
            p for p in scripts_dir.glob("*.py") if not p.is_symlink() and p.is_file()
        )
        if scripts_dir.is_dir() and not scripts_dir.is_symlink()
        else []
    )
    validator_scripts = [p for p in py_scripts if p.name.startswith("validate_")]
    if not validator_scripts:
        errors.append(
            "Missing bundled validator script: expected executable scripts/validate_*.py"
        )

    for script_path in py_scripts:
        rel_posix = script_path.relative_to(package).as_posix()
        try:
            script_text = script_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{rel_posix}: {exc}")
            continue
        first_line = script_text.splitlines()[0] if script_text else ""
        if not (first_line.startswith("#!") and "python" in first_line):
            errors.append(
                f"{rel_posix}: bundled script must start with a Python shebang (#!/usr/bin/env python3)"
            )
        if os.name != "nt" and not os.access(script_path, os.X_OK):
            errors.append(
                f"{rel_posix}: bundled script must have executable permissions (chmod 0755)"
            )

    return errors, (len(validator_scripts) > 0 and len(errors) == 0)


def _evaluate_method_boilerplate_duplication(
    packages: Sequence[Path],
) -> tuple[list[str], float, float]:
    """Compute cross-package non-code line duplication across specialist references/method.md files."""
    per_pkg_lines: dict[str, list[str]] = {}
    line_to_pkgs: dict[str, list[str]] = {}

    for pkg in packages:
        method_path = pkg / "references/method.md"
        if not method_path.is_file() or method_path.is_symlink():
            continue
        try:
            noncode_lines = _extract_noncode_lines(
                method_path.read_text(encoding="utf-8")
            )
        except OSError:
            continue
        per_pkg_lines[pkg.name] = noncode_lines
        for ln in set(noncode_lines):
            line_to_pkgs.setdefault(ln, []).append(pkg.name)

    if len(per_pkg_lines) <= 1:
        return [], 0.0, 0.0

    duplicated_lines = {ln: pkgs for ln, pkgs in line_to_pkgs.items() if len(pkgs) > 1}
    total_instances = sum(len(lines) for lines in per_pkg_lines.values())
    duplicated_instances = sum(
        1 for lines in per_pkg_lines.values() for ln in lines if ln in duplicated_lines
    )
    dup_rate = (duplicated_instances / total_instances) if total_instances > 0 else 0.0
    dup_pct = round(dup_rate * 100.0, 2)

    errors: list[str] = []
    if duplicated_lines:
        sample_line, sample_pkgs = min(
            duplicated_lines.items(), key=lambda item: (-len(item[1]), item[0])
        )
        preview = ", ".join(sorted(sample_pkgs)[:4])
        errors.append(
            f"Specialist references/method.md boilerplate duplication detected ({dup_pct}% across "
            f"{len(per_pkg_lines)} files; e.g. {sample_line!r} in {preview})"
        )
    return errors, round(dup_rate, 4), dup_pct


def validate_skill(package: Path, scenario_schema: dict | None = None) -> list[str]:
    errors = []
    skill_md_path = package / "SKILL.md"
    try:
        skill_raw_text = skill_md_path.read_text(encoding="utf-8")
        metadata, body = read_frontmatter(skill_md_path)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        return [str(exc)]
    name = metadata.get("name")
    if (
        not isinstance(name, str)
        or not NAME.fullmatch(name)
        or len(name) > 64
        or name != package.name
    ):
        errors.append("Invalid name or directory mismatch")
    description = metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        errors.append(
            "Description must be a nonempty string of at most 1024 characters"
        )
    if isinstance(name, str) and isinstance(description, str):
        metadata_tokens = estimate_tokens(f"{name} {description.strip()}")
        if metadata_tokens > MAX_METADATA_TOKENS:
            errors.append(
                f"SKILL.md: frontmatter metadata exceeds token budget "
                f"({metadata_tokens} > {MAX_METADATA_TOKENS} estimated tokens)"
            )
    if not body.strip() or len(body.splitlines()) >= 500:
        errors.append("Skill body must be nonempty and under 500 lines")
    body_tokens = estimate_tokens(body)
    if body_tokens >= MAX_BODY_TOKENS:
        errors.append(
            f"SKILL.md: skill body exceeds token budget "
            f"({body_tokens} >= {MAX_BODY_TOKENS} estimated tokens; must be under {MAX_BODY_TOKENS})"
        )
    errors.extend(_check_skill_guardrails(body))

    resolved_skill_links = {
        resolved
        for target in extract_relative_links(body)
        if (resolved := resolve_relative_link_target(target, package)) is not None
    }
    assets_schemas, output_schemas = _discover_output_schemas(package)
    if not output_schemas:
        errors.append(
            f"Missing output schema: {package.name} must define an output schema (*.schema.json in assets/)"
        )
    else:
        for schema_file in assets_schemas:
            if schema_file.resolve() not in resolved_skill_links:
                errors.append(
                    f"SKILL.md: missing markdown link to output schema "
                    f"{schema_file.relative_to(package).as_posix()}"
                )

    worked_errors, _, _ = _validate_worked_examples(
        package, resolved_skill_links, output_schemas, skill_raw_text
    )
    errors.extend(worked_errors)
    errors.extend(_validate_protocol_depth(package, skill_raw_text))
    script_errors, _ = _validate_bundled_scripts(package)
    errors.extend(script_errors)

    prompt_path = package / "assets/prompt.md"
    prompt_rel = prompt_path.relative_to(package).as_posix()
    if not prompt_path.is_file():
        errors.append(f"Missing prompt template: {prompt_rel}")
    else:
        try:
            prompt_content = prompt_path.read_text(encoding="utf-8")
            if len(prompt_content) > MAX_PROMPT_CHARS:
                errors.append(
                    f"{prompt_rel}: prompt template exceeds budget "
                    f"({len(prompt_content)} > {MAX_PROMPT_CHARS} characters)"
                )
            lines = prompt_content.splitlines()
            if len(lines) > MAX_PROMPT_LINES:
                errors.append(
                    f"{prompt_rel}: prompt template exceeds line budget "
                    f"({len(lines)} > {MAX_PROMPT_LINES} lines)"
                )
            if PROMPT_HEADING_PATTERN.search(prompt_content):
                errors.append(
                    f"{prompt_rel}: prompt template must not contain Markdown headings"
                )
            if "```" in prompt_content:
                errors.append(
                    f"{prompt_rel}: prompt template must not contain triple-backtick code fences"
                )
            placeholders = PLACEHOLDER_PATTERN.findall(prompt_content)
            unauthorized = sorted(
                {p for p in placeholders if p not in ALLOWED_PROMPT_PLACEHOLDERS}
            )
            if unauthorized:
                allowed_str = ", ".join(sorted(ALLOWED_PROMPT_PLACEHOLDERS))
                for bad in unauthorized:
                    errors.append(
                        f"{prompt_rel}: unauthorized placeholder '{bad}' "
                        f"(allowed: {allowed_str})"
                    )
            if not NEGATIVE_CONSTRAINT_PATTERN.search(prompt_content):
                errors.append(
                    f"{prompt_rel}: prompt template lacks negative constraints or boundary guardrails "
                    "(explicit negative prohibitions required)"
                )
            if output_schemas and not _prompt_mentions_schema(
                prompt_content, output_schemas
            ):
                example_schema = output_schemas[0].relative_to(package).as_posix()
                errors.append(
                    f"{prompt_rel}: prompt template must mention the output schema contract "
                    f"(e.g. {example_schema})"
                )
        except OSError as exc:
            errors.append(f"{prompt_rel}: {exc}")

    scenarios_path = package / "references/evaluation-scenarios.json"
    if not scenarios_path.is_file():
        errors.append(
            "Missing evaluation scenarios: references/evaluation-scenarios.json"
        )
    else:
        try:
            scenarios_data = json.loads(scenarios_path.read_text(encoding="utf-8"))
            active_schema = scenario_schema or get_scenario_schema()
            if active_schema is not None:
                validator = jsonschema.Draft202012Validator(active_schema)
                schema_errors = sorted(
                    validator.iter_errors(scenarios_data),
                    key=lambda e: str(e.path),
                )
                for err in schema_errors:
                    errors.append(
                        f"references/evaluation-scenarios.json schema error: {err.message}"
                    )
                if not schema_errors:
                    scenarios = scenarios_data.get("scenarios", [])
                    types = {
                        s.get("id", "").rsplit("-", 1)[-1]
                        for s in scenarios
                        if isinstance(s, dict) and isinstance(s.get("id"), str)
                    }
                    if not {"failure", "safe", "routing"}.issubset(types):
                        errors.append(
                            "references/evaluation-scenarios.json must include failure, safe, and routing scenarios"
                        )
                if isinstance(scenarios_data, dict) and isinstance(
                    scenarios_data.get("scenarios"), list
                ):
                    errors.extend(
                        _validate_scenario_semantics(
                            package,
                            name if isinstance(name, str) else package.name,
                            scenarios_data["scenarios"],
                        )
                    )
        except (OSError, ValueError):
            pass  # Malformed JSON or read error is caught during package file traversal below

    package_resolved = package.resolve()
    references_dir = (package / "references").resolve()
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            errors.append(
                f"Symlink breaks standalone packaging: {path.relative_to(package).as_posix()}"
            )
            continue
        if not path.is_file():
            continue
        rel_posix = path.relative_to(package).as_posix()
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            errors.append(f"Generated Python cache in package: {rel_posix}")
        try:
            if path.suffix == ".json":
                raw_json = path.read_text(encoding="utf-8")
                data = json.loads(raw_json)
                if path.name.endswith(".schema.json"):
                    _check_draft202012_schema(raw_json, data)
                    if not path.name.startswith("evaluation-scenarios"):
                        errors.extend(_validate_closed_schema(data, rel_posix))
            if path.suffix == ".md":
                md_text = path.read_text(encoding="utf-8")
                if path.resolve().is_relative_to(references_dir):
                    ref_tokens = estimate_tokens(md_text)
                    if ref_tokens >= MAX_REFERENCE_TOKENS:
                        errors.append(
                            f"{rel_posix}: reference markdown exceeds token budget "
                            f"({ref_tokens} >= {MAX_REFERENCE_TOKENS} estimated tokens; "
                            f"must be under {MAX_REFERENCE_TOKENS})"
                        )
                for target in extract_relative_links(md_text):
                    resolved = resolve_relative_link_target(target, path.parent)
                    if resolved is None:
                        continue
                    if not resolved.is_relative_to(package_resolved):
                        errors.append(f"Reference escapes standalone package: {target}")
                    elif not resolved.is_file():
                        errors.append(f"Missing relative reference: {target}")
        except (ValueError, OSError, jsonschema.SchemaError) as exc:
            errors.append(f"{rel_posix}: {exc}")

    for chain in find_deep_reference_chains(package, max_hops=1):
        errors.append(
            f"Reference chain exceeds 1-hop depth limit from SKILL.md: {chain}"
        )

    ui_path = package / "agents/openai.yaml"
    if ui_path.exists():
        try:
            ui_doc = yaml.safe_load(ui_path.read_text(encoding="utf-8"))
            if not isinstance(ui_doc, dict):
                errors.append("Invalid optional UI metadata: expected mapping")
            else:
                ui = ui_doc.get("interface")
                if not isinstance(ui, dict):
                    errors.append("Invalid optional UI metadata: missing interface")
                else:
                    if (
                        not isinstance(ui.get("display_name"), str)
                        or not ui["display_name"].strip()
                    ):
                        errors.append("Missing display name")
                    short = ui.get("short_description")
                    if not isinstance(short, str) or not 25 <= len(short) <= 64:
                        errors.append(
                            "UI short_description must contain 25–64 characters"
                        )
                    elif REPEATED_WORD_PATTERN.search(short):
                        errors.append(
                            "UI short_description contains repeated adjacent words"
                        )
                    prompt = ui.get("default_prompt")
                    if not isinstance(prompt, str) or f"${name}" not in prompt:
                        errors.append("UI default_prompt must name the skill")
                    errors.extend(
                        _validate_ui_icon(package, "icon_small", ui.get("icon_small"))
                    )
                    errors.extend(
                        _validate_ui_icon(package, "icon_large", ui.get("icon_large"))
                    )
                policy = ui_doc.get("policy")
                if not isinstance(policy, dict):
                    errors.append(
                        "Missing UI policy: policy.allow_implicit_invocation must be a boolean"
                    )
                else:
                    allow_implicit = policy.get("allow_implicit_invocation")
                    if not isinstance(allow_implicit, bool):
                        errors.append(
                            "UI policy.allow_implicit_invocation must be a boolean"
                        )
                    if "products" in policy:
                        products = policy.get("products")
                        if (
                            not isinstance(products, list)
                            or not products
                            or not all(
                                isinstance(item, str) and bool(item.strip())
                                for item in products
                            )
                        ):
                            errors.append(
                                "UI policy.products must be a nonempty list of strings"
                            )
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            errors.append(f"Invalid optional UI metadata: {exc}")
    return errors


DEFAULT_ATTEMPT_LEDGER_REL = ".fred-skills/validation-attempts.json"
ZERO_GIT_SHA = "0" * 40


def _git_output(repo: Path, *args: str) -> str | None:
    if not shutil.which("git"):
        return None
    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="surrogateescape").rstrip("\r\n")


def _is_git_worktree_root(root: Path) -> bool:
    toplevel = _git_output(root, "rev-parse", "--show-toplevel")
    if not toplevel:
        return False
    try:
        return Path(toplevel).resolve() == root.resolve()
    except OSError:
        return False


def _resolve_commit_sha(root: Path, ref: str) -> str | None:
    candidate = ref.strip()
    if not candidate or candidate == ZERO_GIT_SHA:
        return None
    direct = _git_output(
        root, "rev-parse", "--verify", "--end-of-options", f"{candidate}^{{commit}}"
    )
    if direct and re.fullmatch(r"[0-9a-f]{40,64}", direct):
        return direct
    if not candidate.startswith(("origin/", "refs/")):
        remote = _git_output(
            root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"origin/{candidate}^{{commit}}",
        )
        if remote and re.fullmatch(r"[0-9a-f]{40,64}", remote):
            return remote
    return None


def _skills_at_commit(root: Path, commit_sha: str) -> set[str]:
    tree_out = _git_output(
        root, "ls-tree", "-r", "--name-only", "-z", commit_sha, "--", "skills"
    )
    if tree_out is None:
        return set()
    skills: set[str] = set()
    for raw_entry in tree_out.split("\0"):
        if not raw_entry:
            continue
        parts = raw_entry.split("/")
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            skills.add(parts[1])
    return skills


def resolve_new_skills(
    root: Path,
    current_packages: Sequence[str],
    base_ref: str | None = None,
) -> tuple[set[str], str | None, str, list[str]]:
    """Identify newly authored skills relative to git history or base_ref.

    Returns (new_skill_names, diff_base_sha, diff_scope, errors).
    """
    current_set = set(current_packages)
    explicit_ref = base_ref.strip() if base_ref is not None else None

    if not _is_git_worktree_root(root):
        if explicit_ref and explicit_ref != ZERO_GIT_SHA:
            return (
                set(),
                None,
                "unversioned",
                [f"Cannot resolve git base ref '{explicit_ref}': not a git repository"],
            )
        return current_set, None, "unversioned", []

    head_sha = _resolve_commit_sha(root, "HEAD")
    if head_sha is None:
        if explicit_ref and explicit_ref != ZERO_GIT_SHA:
            return (
                set(),
                None,
                "unborn-head",
                [f"Cannot resolve git merge-base for '{explicit_ref}': unborn HEAD"],
            )
        return current_set, None, "unborn-head", []

    if explicit_ref is not None:
        if not explicit_ref or explicit_ref == ZERO_GIT_SHA:
            parent_sha = _resolve_commit_sha(root, "HEAD~1")
            if parent_sha is None:
                return current_set, None, "initial-commit", []
            baseline = _skills_at_commit(root, parent_sha)
            return current_set - baseline, parent_sha, "git-diff", []

        target_sha = _resolve_commit_sha(root, explicit_ref)
        if target_sha is None:
            return (
                set(),
                None,
                "git-diff",
                [f"Could not resolve git base ref '{explicit_ref}'"],
            )
        merge_base = _git_output(root, "merge-base", target_sha, head_sha)
        if not merge_base or not re.fullmatch(r"[0-9a-f]{40,64}", merge_base):
            return (
                set(),
                None,
                "git-diff",
                [f"Could not resolve git merge-base between '{explicit_ref}' and HEAD"],
            )
        baseline = _skills_at_commit(root, merge_base)
        return current_set - baseline, merge_base, "git-diff", []

    base_commit = head_sha
    for candidate_branch in ("origin/main", "main"):
        branch_sha = _resolve_commit_sha(root, candidate_branch)
        if branch_sha is not None:
            mb = _git_output(root, "merge-base", branch_sha, head_sha)
            if mb and re.fullmatch(r"[0-9a-f]{40,64}", mb):
                base_commit = mb
                break

    baseline = _skills_at_commit(root, base_commit)
    return current_set - baseline, base_commit, "git-diff", []


def _load_attempt_ledger(ledger_path: Path) -> dict[str, dict[str, Any]]:
    if not ledger_path.is_file():
        return {}
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    skills_obj = payload.get("skills")
    if not isinstance(skills_obj, dict):
        return {}
    return {
        str(k): dict(v)
        for k, v in skills_obj.items()
        if isinstance(k, str) and isinstance(v, dict)
    }


def _save_attempt_ledger(
    ledger_path: Path, entries: Mapping[str, dict[str, Any]]
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "skills": {k: entries[k] for k in sorted(entries)},
    }
    ledger_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def format_telemetry_markdown(telemetry: Mapping[str, Any]) -> str:
    """Format validation telemetry as a GitHub Step Summary Markdown table."""
    new_count = int(telemetry.get("new_skills_count", 0))
    new_first_pass = int(telemetry.get("new_skills_first_pass_count", 0))
    new_passed = int(telemetry.get("new_skills_passed", 0))
    new_rate_pct = telemetry.get("new_skills_first_pass_percentage")
    new_rate_display = (
        f"{new_rate_pct}%" if new_rate_pct is not None else "N/A (0 new skills)"
    )
    diff_base = telemetry.get("diff_base") or "none"

    lines = [
        "### Skill Validation Telemetry",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Diff Scope | `{telemetry.get('diff_scope', 'unknown')}` (`{diff_base}`) |",
        f"| Newly Authored Skills | {new_count} |",
        f"| New-Skill First-Pass Count | {new_first_pass} |",
        f"| New-Skill First-Pass Rate | **{new_rate_display}** |",
        f"| New-Skill Current Passed | {new_passed} / {new_count} |",
        f"| Whole-Repository Packages | {telemetry.get('passed_packages', 0)} / {telemetry.get('total_packages', 0)} ({telemetry.get('repository_guardrail_pass_rate_percentage', 0.0)}%) |",
        f"| Deterministic Constraint Coverage | {telemetry.get('deterministic_constraint_coverage_percentage', 0.0)}% |",
        f"| Output Schema Coverage | {telemetry.get('output_schema_coverage_percentage', 0.0)}% |",
        f"| Skill Worked-Example Coverage | {telemetry.get('skill_worked_example_coverage_percentage', 0.0)}% |",
        f"| Bundled Validator Script Coverage | {telemetry.get('validator_script_coverage_percentage', 0.0)}% |",
        f"| Specialist Method Boilerplate Duplication | {telemetry.get('method_boilerplate_duplication_percentage', 0.0)}% |",
        f"| Execution Time | {telemetry.get('execution_time_seconds', 0.0)}s |",
    ]

    new_pkg_rows = [
        pkg
        for pkg in telemetry.get("packages", [])
        if isinstance(pkg, Mapping) and pkg.get("is_new")
    ]
    if new_pkg_rows:
        lines.extend(
            [
                "",
                "#### Newly Authored Skill Outcomes",
                "",
                "| Skill | Current Valid | First-Pass Valid | Attempts |",
                "| --- | --- | --- | --- |",
            ]
        )
        for pkg in new_pkg_rows:
            lines.append(
                f"| `{pkg['name']}` | `{pkg['valid']}` | `{pkg['first_pass_valid']}` | {pkg['validation_attempts']} |"
            )

    return "\n".join(lines) + "\n"


def _schemas_closed_and_linked(
    package: Path,
    skill_body: str,
    assets_schemas: list[Path],
    output_schemas: list[Path],
) -> bool:
    if not output_schemas:
        return False
    resolved_links = {
        resolved
        for target in extract_relative_links(skill_body)
        if (resolved := resolve_relative_link_target(target, package)) is not None
    }
    if any(s.resolve() not in resolved_links for s in assets_schemas):
        return False
    for schema_path in output_schemas:
        try:
            raw_json = schema_path.read_text(encoding="utf-8")
            data = json.loads(raw_json)
            _check_draft202012_schema(raw_json, data)
        except (OSError, ValueError, jsonschema.SchemaError):
            return False
        rel_posix = schema_path.relative_to(package).as_posix()
        if _validate_closed_schema(data, rel_posix):
            return False
    return True


def validate_with_telemetry(
    root: Path = ROOT,
    *,
    base_ref: str | None = None,
    ledger_path: Path | None = None,
    record_attempts: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    start_time = time.perf_counter()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    problems: list[str] = []
    descriptions: dict[str, str] = {}
    short_descriptions: dict[str, str] = {}
    scenario_schema = None
    schema_path = root / "schemas/evaluation-scenarios.schema.json"
    if not schema_path.is_file():
        problems.append(
            f"Missing evaluation scenario schema: {schema_path.relative_to(root).as_posix()}"
        )
    else:
        try:
            scenario_schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(scenario_schema)
        except (ValueError, OSError, jsonschema.SchemaError) as exc:
            problems.append(f"{schema_path.relative_to(root).as_posix()}: {exc}")
            scenario_schema = None

    skills_dir = root / "skills"
    packages = (
        sorted(path for path in skills_dir.iterdir() if path.is_dir())
        if skills_dir.is_dir()
        else []
    )
    if not packages:
        problems.append("No skill packages found")

    pkg_names = [pkg.name for pkg in packages]
    diff_new_skills, diff_base, diff_scope, diff_errors = resolve_new_skills(
        root=root,
        current_packages=pkg_names,
        base_ref=base_ref,
    )
    problems.extend(diff_errors)

    effective_ledger_path = (
        ledger_path if ledger_path is not None else (root / DEFAULT_ATTEMPT_LEDGER_REL)
    )
    ledger_entries = (
        _load_attempt_ledger(effective_ledger_path) if record_attempts else {}
    )
    ledger_dirty = False

    new_skill_names = set(diff_new_skills)
    if base_ref is None and record_attempts:
        new_skill_names |= set(ledger_entries.keys()) & set(pkg_names)

    package_telemetry: list[dict[str, Any]] = []
    for package in packages:
        pkg_errors = validate_skill(package, scenario_schema=scenario_schema)
        problems.extend(f"{package.name}: {error}" for error in pkg_errors)

        assets_schema_paths, output_schema_paths = _discover_output_schemas(package)
        output_schemas = [
            p.relative_to(package).as_posix() for p in output_schema_paths
        ]

        skill_body = ""
        skill_raw_text = ""
        skill_path = package / "SKILL.md"
        if skill_path.is_file():
            try:
                skill_raw_text = skill_path.read_text(encoding="utf-8")
                _, skill_body = read_frontmatter(skill_path)
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                skill_body = ""

        resolved_pkg_links = {
            resolved
            for target in extract_relative_links(skill_body)
            if (resolved := resolve_relative_link_target(target, package)) is not None
        }
        _, worked_example_blocks, has_worked_examples = _validate_worked_examples(
            package, resolved_pkg_links, output_schema_paths, skill_raw_text
        )
        _, has_validator_script = _validate_bundled_scripts(package)

        has_schema = _schemas_closed_and_linked(
            package, skill_body, assets_schema_paths, output_schema_paths
        )

        prompt_path = package / "assets/prompt.md"
        prompt_text = (
            prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
        )
        has_prompt_constraints = bool(
            NEGATIVE_CONSTRAINT_PATTERN.search(prompt_text)
            and _prompt_mentions_schema(prompt_text, output_schema_paths)
        )

        has_boundary_sections = bool(
            skill_body.strip() and not _check_skill_guardrails(skill_body)
        )

        pkg_valid = len(pkg_errors) == 0
        deterministic_coverage = (
            has_schema and has_prompt_constraints and has_boundary_sections
        )

        is_new = package.name in new_skill_names
        first_pass_valid: bool | None = None
        attempts_count = 0

        if is_new:
            existing_entry = ledger_entries.get(package.name)
            if existing_entry is None:
                first_pass_valid = pkg_valid
                attempts_count = 1
                if record_attempts:
                    ledger_entries[package.name] = {
                        "first_seen_at": now_iso,
                        "last_checked_at": now_iso,
                        "attempts": 1,
                        "first_pass_valid": pkg_valid,
                        "current_valid": pkg_valid,
                        "initial_errors": list(pkg_errors),
                    }
                    ledger_dirty = True
            else:
                first_pass_valid = bool(
                    existing_entry.get("first_pass_valid", pkg_valid)
                )
                prev_attempts = existing_entry.get("attempts", 1)
                attempts_count = (
                    (prev_attempts + 1) if isinstance(prev_attempts, int) else 2
                )
                if record_attempts:
                    existing_entry["last_checked_at"] = now_iso
                    existing_entry["attempts"] = attempts_count
                    existing_entry["current_valid"] = pkg_valid
                    existing_entry["first_pass_valid"] = first_pass_valid
                    ledger_dirty = True

        package_telemetry.append(
            {
                "name": package.name,
                "valid": pkg_valid,
                "is_new": is_new,
                "first_pass_valid": first_pass_valid,
                "validation_attempts": attempts_count,
                "has_output_schema": has_schema,
                "output_schemas": output_schemas,
                "worked_example_blocks": worked_example_blocks,
                "has_worked_examples": has_worked_examples,
                "has_validator_script": has_validator_script,
                "has_prompt_constraints": has_prompt_constraints,
                "has_boundary_sections": has_boundary_sections,
                "deterministic_constraint_coverage": deterministic_coverage,
                "errors": pkg_errors,
            }
        )

        try:
            meta, _ = read_frontmatter(package / "SKILL.md")
            description = meta.get("description")
            if isinstance(description, str):
                if description in descriptions:
                    problems.append(
                        f"Duplicate activation description: {package.name} and {descriptions[description]}"
                    )
                descriptions[description] = package.name
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            pass  # The package-level validator already reports this failure.

        ui_path = package / "agents/openai.yaml"
        if ui_path.is_file() and not ui_path.is_symlink():
            try:
                ui_doc = yaml.safe_load(ui_path.read_text(encoding="utf-8"))
                if isinstance(ui_doc, dict) and isinstance(
                    ui_doc.get("interface"), dict
                ):
                    short_desc = ui_doc["interface"].get("short_description")
                    if isinstance(short_desc, str) and short_desc.strip():
                        normalized_short = short_desc.strip()
                        if normalized_short in short_descriptions:
                            problems.append(
                                f"Duplicate UI short_description: {package.name} and {short_descriptions[normalized_short]}"
                            )
                        else:
                            short_descriptions[normalized_short] = package.name
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                pass  # The package-level validator already reports this failure.

    if record_attempts and ledger_dirty:
        _save_attempt_ledger(effective_ledger_path, ledger_entries)

    try:
        if (root / "catalog/suites.json").is_file():
            for name in suite_members(root):
                skill_dir = root / "skills" / name
                if not (skill_dir / "SKILL.md").is_file():
                    problems.append(f"Suite references absent skill: {name}")
                elif (skill_dir / "agents/openai.yaml").is_symlink() or not (
                    skill_dir / "agents/openai.yaml"
                ).is_file():
                    problems.append(
                        f"{name}: Suite skill missing required UI metadata: agents/openai.yaml"
                    )
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"Invalid suite manifest: {exc}")

    method_dup_errors, method_dup_rate, method_dup_pct = (
        _evaluate_method_boilerplate_duplication(packages)
    )
    problems.extend(method_dup_errors)

    total_pkgs = len(packages)
    passed_pkgs = sum(1 for p in package_telemetry if p["valid"])
    failed_pkgs = total_pkgs - passed_pkgs
    covered_pkgs = sum(
        1 for p in package_telemetry if p["deterministic_constraint_coverage"]
    )
    schema_pkgs = sum(1 for p in package_telemetry if p["has_output_schema"])
    example_pkgs = sum(1 for p in package_telemetry if p["has_worked_examples"])
    validator_pkgs = sum(1 for p in package_telemetry if p["has_validator_script"])

    repo_pass_rate = (passed_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    repo_pass_rate_pct = round(repo_pass_rate * 100.0, 2)
    constraint_cov = (covered_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    constraint_cov_pct = round(constraint_cov * 100.0, 2)
    schema_cov = (schema_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    schema_cov_pct = round(schema_cov * 100.0, 2)
    worked_example_cov = (example_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    worked_example_cov_pct = round(worked_example_cov * 100.0, 2)
    validator_script_cov = (validator_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    validator_script_cov_pct = round(validator_script_cov * 100.0, 2)

    new_pkgs_telemetry = [p for p in package_telemetry if p["is_new"]]
    new_skills_count = len(new_pkgs_telemetry)
    new_skills_passed = sum(1 for p in new_pkgs_telemetry if p["valid"])
    new_skills_first_pass_count = sum(
        1 for p in new_pkgs_telemetry if p["first_pass_valid"] is True
    )

    new_first_pass_rate: float | None
    new_first_pass_pct: float | None
    if new_skills_count > 0:
        raw_new_rate = new_skills_first_pass_count / new_skills_count
        new_first_pass_rate = round(raw_new_rate, 4)
        new_first_pass_pct = round(raw_new_rate * 100.0, 2)
        effective_first_pass_rate = new_first_pass_rate
        effective_first_pass_pct = new_first_pass_pct
    else:
        new_first_pass_rate = None
        new_first_pass_pct = None
        effective_first_pass_rate = round(repo_pass_rate, 4)
        effective_first_pass_pct = repo_pass_rate_pct

    duration = round(time.perf_counter() - start_time, 4)

    telemetry: dict[str, Any] = {
        "timestamp": now_iso,
        "diff_scope": diff_scope,
        "diff_base": diff_base,
        "total_packages": total_pkgs,
        "passed_packages": passed_pkgs,
        "failed_packages": failed_pkgs,
        "repository_guardrail_pass_rate": round(repo_pass_rate, 4),
        "repository_guardrail_pass_rate_percentage": repo_pass_rate_pct,
        "new_skills_count": new_skills_count,
        "new_skills_passed": new_skills_passed,
        "new_skills_first_pass_count": new_skills_first_pass_count,
        "new_skills_first_pass_rate": new_first_pass_rate,
        "new_skills_first_pass_percentage": new_first_pass_pct,
        "first_pass_guardrail_pass_rate": effective_first_pass_rate,
        "first_pass_guardrail_pass_rate_percentage": effective_first_pass_pct,
        "deterministic_constraint_coverage": round(constraint_cov, 4),
        "deterministic_constraint_coverage_percentage": constraint_cov_pct,
        "output_schema_coverage": round(schema_cov, 4),
        "output_schema_coverage_percentage": schema_cov_pct,
        "skill_worked_example_coverage": round(worked_example_cov, 4),
        "skill_worked_example_coverage_percentage": worked_example_cov_pct,
        "validator_script_coverage": round(validator_script_cov, 4),
        "validator_script_coverage_percentage": validator_script_cov_pct,
        "method_boilerplate_duplication_rate": method_dup_rate,
        "method_boilerplate_duplication_percentage": method_dup_pct,
        "execution_time_seconds": duration,
        "packages": package_telemetry,
    }

    return problems, telemetry


def validate(root: Path = ROOT) -> list[str]:
    errors, _ = validate_with_telemetry(root)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--telemetry",
        nargs="?",
        const="validation-telemetry.json",
        default=None,
        metavar="PATH",
        help="Export validation telemetry as structured JSON",
    )
    parser.add_argument(
        "--export-telemetry",
        dest="export_telemetry",
        default=None,
        metavar="PATH",
        help="Export validation telemetry as structured JSON",
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        metavar="REF",
        help="Git base ref or SHA for diff-scoped new-skill telemetry",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to local validation attempt ledger (default: .fred-skills/validation-attempts.json)",
    )
    parser.add_argument(
        "--no-ledger",
        action="store_true",
        help="Disable reading and writing the local validation attempt ledger",
    )
    parser.add_argument(
        "--step-summary",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Append Markdown telemetry summary to PATH or $GITHUB_STEP_SUMMARY",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Root directory of the skill repository",
    )
    args = parser.parse_args(argv)

    errors, telemetry = validate_with_telemetry(
        root=args.root,
        base_ref=args.base_ref,
        ledger_path=args.ledger,
        record_attempts=not args.no_ledger,
    )
    for error in errors:
        print(error)

    telemetry_path = args.export_telemetry or args.telemetry
    if telemetry_path:
        out_file = Path(telemetry_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(
            json.dumps(telemetry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Exported validation telemetry to {out_file.as_posix()}")

    if args.step_summary is not None:
        summary_target = args.step_summary or os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary_target:
            summary_path = Path(summary_target)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            with summary_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(format_telemetry_markdown(telemetry))

    new_rate_display = (
        f"{telemetry['new_skills_first_pass_percentage']}%"
        if telemetry["new_skills_first_pass_percentage"] is not None
        else "N/A (0 new skills)"
    )

    print("=" * 60)
    print("Validation Telemetry:")
    print(f"  Total Packages: {telemetry['total_packages']}")
    print(f"  Passed Packages: {telemetry['passed_packages']}")
    print(f"  Failed Packages: {telemetry['failed_packages']}")
    print(f"  New Skills: {telemetry['new_skills_count']}")
    print(f"  New-skill First-pass Count: {telemetry['new_skills_first_pass_count']}")
    print(f"  New-skill First-pass Rate: {new_rate_display}")
    print(
        f"  First-pass Guardrail Pass Rate: {telemetry['first_pass_guardrail_pass_rate_percentage']}%"
    )
    print(
        f"  Deterministic Constraint Coverage: {telemetry['deterministic_constraint_coverage_percentage']}%"
    )
    print(
        f"  Skill Worked-Example Coverage: {telemetry['skill_worked_example_coverage_percentage']}%"
    )
    print(
        f"  Bundled Validator Script Coverage: {telemetry['validator_script_coverage_percentage']}%"
    )
    print(
        f"  Method Boilerplate Duplication: {telemetry['method_boilerplate_duplication_percentage']}%"
    )
    print(f"  Execution Time: {telemetry['execution_time_seconds']}s")
    print("=" * 60)

    if not errors:
        print("PASS: skill metadata, schemas, and standalone references")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

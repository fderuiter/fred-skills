"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import argparse
import concurrent.futures
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations, pairwise
from pathlib import Path, PureWindowsPath
from typing import Any

import jsonschema
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _doc_invariants import (
    CORE_INVARIANT_DOCS,
    HOST_LAYOUTS_RE,
    RELEASE_HOST_COUNT_RE,
    RELEASE_PACK_COUNT_RE,
    RELEASE_SUITE_COUNT_RE,
    RELEASE_TOTAL_SKILLS_RE,
    DocCountExpectations,
    build_prose_count_rules,
    discover_untagged_release_notes,
    find_prose_count_mismatches,
    git_tag_exists,
    parse_prose_int,
)
from _markdown_utils import (
    extract_fenced_blocks as _extract_fenced_blocks,
)
from _markdown_utils import (
    extract_markdown_preamble,
)
from _markdown_utils import (
    extract_noncode_lines as _extract_noncode_lines,
)
from fredlib import vetting
from fredlib.common import FredCliError, atomic_json, read_json_object
from skill_tools import (
    ROOT,
    estimate_tokens,
    extract_relative_links,
    find_deep_reference_chains,
    git_blob_bytes,
    infer_suite_and_siblings,
    infer_suite_name,
    read_frontmatter,
    resolve_relative_link_target,
    run_local_git,
    skill_to_suite_map,
    suite_members,
)

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_PROMPT_CHARS = 1200
MAX_PROMPT_LINES = 20
MAX_DESCRIPTION_CHARS = 140
MAX_SUITE_METADATA_CHARS = 8000
MAX_STARTER_PACK_CHARS = 1500
MAX_CURATED_PACK_CHARS = 2500
MAX_CROSS_SUITE_JACCARD = 0.20
UMBRELLA_PACK_NAMES = frozenset({"full-audit", "complete"})
EXPLICIT_INVOCATION_ONLY_SKILLS = frozenset(
    {
        "repo-apply-fix",
        "repo-audit-diagnostics",
        "repo-audit-program",
        "repo-audit-synthesis",
        "repo-reconcile-findings",
        "repo-specify-regression",
        "test-case-generation",
    }
)
DESCRIPTION_USE_WHEN_RE = re.compile(r"\bUse when\s+\S", re.IGNORECASE)
DESCRIPTION_DO_NOT_USE_RE = re.compile(
    r"\bDo not use(?:\s+(?:for|when|to))?\s+\S", re.IGNORECASE
)
JACCARD_TOKEN_RE = re.compile(r"[a-z0-9]+")
JACCARD_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "from",
        "in",
        "into",
        "is",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "use",
        "when",
        "with",
    }
)
MAX_METADATA_TOKENS = 100
MAX_BODY_TOKENS = 5000
MAX_REFERENCE_TOKENS = 5000
MIN_WORKED_EXAMPLE_BLOCKS = 2
PLACEHOLDER_PATTERN = re.compile(r"<[^>]+>")
ALLOWED_PROMPT_PLACEHOLDERS = frozenset(
    {
        "<allowed-paths>",
        "<audit workspace/reports>",
        "<critical workflows>",
        "<directory>",
        "<do-not-modify-paths>",
        "<existing audit workspace, if any>",
        "<existing audit workspace>",
        "<finding/run artifacts>",
        "<issue-number>",
        "<path-to-diagnostic-bundle-output>",
        "<path-to-run-workspace-directory>",
        "<path-to-session-transcript>",
        "<repository/revision>",
        "<repository>",
        "<revision>",
        "<symbol>",
    }
)

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
MIN_TRIGGER_CONTENT_WORDS = 4
USE_WHEN_PATTERN = re.compile(r"\buse when\s+(.*\S)", re.IGNORECASE | re.DOTALL)
VAGUE_DESCRIPTION_PATTERN = re.compile(
    r"\b("
    r"helps?\s+with\b|"
    r"use(?:d)?\s+for\s+(?:general|various|common|everyday|productivity|coding|all|any)\b|"
    r"(?:productivity|general|various|miscellaneous|everyday|all-purpose|general-purpose)\s+tasks?\b|"
    r"general[- ]purpose\s+(?:helper|assistant|tool|skill|workflow)\b|"
    r"(?:general|code|coding)\s+(?:helper|assistant)\b|"
    r"catch[- ]all\b|"
    r"anything\s+else\b|"
    r"(?:when|as|whenever)\s+needed\b"
    r")",
    re.IGNORECASE,
)
HEADING_ACRONYM_PATTERN = re.compile(r"\b([A-Z]{2,})s?\b")
STANDALONE_ALPHA_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./$:-])[A-Za-z]{2,}(?![A-Za-z0-9_/$:-]|\.[A-Za-z0-9])"
)
WORD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
CLAUSE_SPLIT_PATTERN = re.compile(r"[.,;:!?()]+")
DESCRIPTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "before",
        "by",
        "do",
        "for",
        "from",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "not",
        "of",
        "on",
        "or",
        "so",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "through",
        "to",
        "under",
        "up",
        "use",
        "used",
        "using",
        "via",
        "was",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "with",
        "within",
        "without",
    }
)
CURSOR_PLUGIN_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
CURSOR_PLUGIN_SEMVER_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")
CURSOR_PLUGIN_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "displayName",
        "description",
        "version",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "logo",
        "rules",
        "agents",
        "skills",
        "commands",
        "hooks",
        "mcpServers",
        "variables",
    }
)
CURSOR_PLUGIN_PATH_FIELDS = (
    "logo",
    "rules",
    "agents",
    "skills",
    "commands",
    "hooks",
    "mcpServers",
)


_NON_AUDIT_NOTE_SUITES = frozenset(
    {
        "software-architecture",
        "code-review",
        "automated-testing",
        "spec-driven-development",
    }
)
_NO_EXPECTED_FINDINGS_SUITES = frozenset(
    {
        "software-architecture",
        "automated-testing",
        "spec-driven-development",
        "repository-workflows",
    }
)
_ALLOWED_PACKAGE_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".svg"})
_DISALLOWED_DOC_FILENAMES = frozenset({"readme.md", "changelog.md"})
_ALLOWED_CC_WHITESPACE = frozenset({"\n", "\r", "\t"})
_NON_PRINTABLE_ASCII_RE = re.compile(r"[^\t\n\r\x20-\x7e]")
_DISALLOWED_SKILL_CAPABILITIES = frozenset(
    {
        "all",
        "dynamic-execution",
        "native-ffi",
        "network",
        "process-execution",
        "shell-execution",
    }
)


def _find_hidden_unicode(text: str) -> str | None:
    if not _NON_PRINTABLE_ASCII_RE.search(text):
        return None
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            return f"U+{ord(ch):04X}"
        if cat == "Cc" and ch not in _ALLOWED_CC_WHITESPACE:
            return f"U+{ord(ch):04X}"
    return None


def _check_text_hygiene(
    text: str, rel_posix: str, *, check_html_comments: bool = False
) -> list[str]:
    if "\x00" in text:
        return [f"Binary file (NUL byte) in skill package: {rel_posix}"]
    issues: list[str] = []
    hidden = _find_hidden_unicode(text)
    if hidden is not None:
        issues.append(
            f"{rel_posix}: disallowed hidden or control Unicode character {hidden}"
        )
    if check_html_comments and "<!--" in text:
        issues.append(f"{rel_posix}: HTML comments are not allowed")
    return issues


def _inspect_skill_md_raw_bytes(skill_md_path: Path) -> list[str]:
    if skill_md_path.is_symlink() or not skill_md_path.is_file():
        return []
    try:
        raw_bytes = skill_md_path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw_bytes:
        return ["Binary file (NUL byte) in skill package: SKILL.md"]
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ["Binary or non-UTF-8 file in skill package: SKILL.md"]
    issues = _check_text_hygiene(raw_text, "SKILL.md", check_html_comments=True)
    issues.extend(
        _format_vetting_finding(item)
        for item in vetting._vet_markdown_text(raw_text, "SKILL.md")
    )
    return issues


def _is_package_script_rel(rel_posix: str, suffix: str) -> bool:
    parts = rel_posix.split("/")
    return len(parts) == 2 and parts[0] == "scripts" and suffix == ".py"


def _check_package_file_allowlist(rel_posix: str, name: str, suffix: str) -> str | None:
    if (
        rel_posix.startswith(".cursor-plugin/")
        and rel_posix != ".cursor-plugin/plugin.json"
    ):
        return f"Unexpected file in .cursor-plugin directory: {rel_posix}"
    if name.lower() in _DISALLOWED_DOC_FILENAMES:
        return f"Disallowed documentation clutter in skill package: {rel_posix}"
    if _is_package_script_rel(rel_posix, suffix):
        return None
    if suffix in _ALLOWED_PACKAGE_TEXT_SUFFIXES:
        return None
    return (
        f"Disallowed or binary file in skill package: {rel_posix} "
        "(allowed UTF-8 text files: .md, .json, .yaml, .svg, scripts/*.py)"
    )


def _skill_read_capabilities(skill_name: str) -> frozenset[str]:
    builtin = vetting.SKILL_CAPABILITY_ALLOWLIST.get(skill_name, frozenset())
    return frozenset(
        cap for cap in builtin if cap not in _DISALLOWED_SKILL_CAPABILITIES
    )


def _format_vetting_finding(finding: Mapping[str, Any]) -> str:
    return (
        f"{finding['file']}:{finding['line']} [{finding['rule']}]: {finding['message']}"
    )


@functools.lru_cache(maxsize=64)
def _cached_python_script_errors(
    source: str,
    rel_posix: str,
    effective: frozenset[str],
) -> tuple[str, ...]:
    findings = vetting._scan_hidden_unicode_text(source, rel_posix, category="script")
    findings.extend(vetting._vet_python_source(source, rel_posix, effective))
    return tuple(_format_vetting_finding(item) for item in findings)


def _inspect_python_script(
    source: str,
    rel_posix: str,
    allowed: frozenset[str] = frozenset(),
) -> list[str]:
    effective = frozenset(
        cap for cap in allowed if cap not in _DISALLOWED_SKILL_CAPABILITIES
    )
    return list(_cached_python_script_errors(source, rel_posix, effective))


def _is_relative_icon_path(raw_value: str) -> bool:
    return not (
        "\\" in raw_value
        or raw_value.startswith("/")
        or Path(raw_value).is_absolute()
        or PureWindowsPath(raw_value).is_absolute()
    )


def _read_cached_utf8(path: Path, cached_texts: dict[Path, str] | None) -> str:
    if cached_texts is not None and path in cached_texts:
        return cached_texts[path]
    content = path.read_text(encoding="utf-8")
    if cached_texts is not None and not path.is_symlink():
        cached_texts[path] = content
    return content


def _validate_svg_file(
    candidate: Path,
    field: str,
    raw_value: str,
    cached_texts: dict[Path, str] | None = None,
) -> list[str]:
    if candidate.suffix.lower() != ".svg":
        return [f"UI {field} must point to an .svg file: {raw_value}"]
    if not candidate.is_file():
        return [f"Missing UI {field} file: {raw_value}"]
    try:
        svg_text = _read_cached_utf8(candidate, cached_texts)
    except (OSError, UnicodeDecodeError) as exc:
        return [f"Invalid SVG in UI {field} ({raw_value}): {exc}"]
    findings = vetting._vet_svg_text(svg_text, raw_value)
    return [
        f"Invalid SVG in UI {field} ({raw_value}) [{item['rule']}]: {item['message']}"
        for item in findings
    ]


def _validate_ui_icon(
    package: Path,
    field: str,
    raw_value: object,
    cached_texts: dict[Path, str] | None = None,
) -> list[str]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return [f"Missing or invalid UI {field}"]
    if not _is_relative_icon_path(raw_value):
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
    return _validate_svg_file(candidate, field, raw_value, cached_texts)


def _check_scenario_note_terminology(sid: str, suite_name: str, note: str) -> list[str]:
    problems: list[str] = []
    if LEGACY_AUDIT_ACTIVATION_NOTE_PATTERN.search(note):
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): legacy audit activation note must be replaced with a suite-accurate sibling routing note"
        )
    if suite_name in _NON_AUDIT_NOTE_SUITES and AUDIT_WORD_PATTERN.search(note):
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
    if (
        suite_name in _NO_EXPECTED_FINDINGS_SUITES
        and EXPECTED_FINDINGS_NOTE_PATTERN.search(note)
    ):
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): misplaced 'expected findings' note in {suite_name}; reference the suite contract instead"
        )
    return problems


def _validate_single_scenario_entry(
    item: Mapping[str, Any], suite_name: str
) -> list[str]:
    sid = str(item.get("id", ""))
    prompt = str(item.get("prompt", ""))
    note = str(item.get("evaluation_note", ""))
    problems: list[str] = []
    if GENERIC_ROUTING_PROMPT_PATTERN.search(prompt):
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): generic typo or proofreading prompt is disallowed; use a near-neighbor sibling prompt"
        )
    problems.extend(_check_scenario_note_terminology(sid, suite_name, note))
    return problems


def _routing_mentions_sibling(r_item: Mapping[str, Any], sibling: str) -> bool:
    r_note = str(r_item.get("evaluation_note", ""))
    raw_behaviors = r_item.get("expected_behavior", [])
    r_behaviors = " ".join(b for b in raw_behaviors if isinstance(b, str))
    return sibling in r_note or sibling in r_behaviors


def _validate_routing_scenario(
    r_item: Mapping[str, Any],
    skill_name: str,
    *,
    suite_name: str,
    siblings: set[str],
    non_routing_prompts: set[str],
) -> list[str]:
    sid = str(r_item.get("id", ""))
    sibling = r_item.get("sibling_skill")
    if not isinstance(sibling, str) or not sibling.strip():
        return [
            f"references/evaluation-scenarios.json ({sid}): routing scenario requires sibling_skill"
        ]
    problems: list[str] = []
    if sibling == skill_name:
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): sibling_skill '{sibling}' must differ from '{skill_name}'"
        )
    elif siblings and sibling not in siblings:
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): sibling_skill '{sibling}' is not a member of suite '{suite_name}'"
        )
    r_prompt = str(r_item.get("prompt", "")).strip()
    if r_prompt in non_routing_prompts:
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): routing prompt must target a sibling boundary rather than repeating the skill's own prompt"
        )
    if not _routing_mentions_sibling(r_item, sibling):
        problems.append(
            f"references/evaluation-scenarios.json ({sid}): routing evaluation_note or expected_behavior must reference sibling_skill '{sibling}'"
        )
    return problems


def _validate_scenario_semantics(
    package: Path, skill_name: str, scenarios: list[object]
) -> list[str]:
    problems: list[str] = []
    suite_name, siblings = infer_suite_and_siblings(skill_name, package.parent.parent)
    non_routing_prompts: set[str] = set()
    routing_scenarios: list[dict[str, Any]] = []

    for item in scenarios:
        if not isinstance(item, dict):
            continue
        problems.extend(_validate_single_scenario_entry(item, suite_name))
        sid = str(item.get("id", ""))
        prompt = str(item.get("prompt", ""))
        kind = sid.rsplit("-", 1)[-1] if "-" in sid else ""
        if kind == "routing":
            routing_scenarios.append(item)
        elif prompt.strip():
            non_routing_prompts.add(prompt.strip())

    for r_item in routing_scenarios:
        problems.extend(
            _validate_routing_scenario(
                r_item,
                skill_name,
                suite_name=suite_name,
                siblings=siblings,
                non_routing_prompts=non_routing_prompts,
            )
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


_SCHEMA_MAP_KEYS = (
    "$defs",
    "definitions",
    "patternProperties",
    "dependentSchemas",
)
_SCHEMA_SINGLE_KEYS = (
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
)
_SCHEMA_LIST_KEYS = ("allOf", "anyOf", "oneOf", "prefixItems")


def _check_node_properties(
    node: Mapping[str, Any],
    json_path: str,
    schema_rel: str,
    schema_errors: list[str],
) -> list[tuple[object, str]]:
    props = node.get("properties")
    if not isinstance(props, dict):
        return []
    if node.get("additionalProperties") is not False:
        schema_errors.append(
            f"{schema_rel}: structured object at {json_path} with 'properties' "
            "must set 'additionalProperties': false"
        )
    return [
        (prop_schema, f"{json_path}.properties.{prop_name}")
        for prop_name, prop_schema in props.items()
    ]


def _collect_map_subschemas(
    node: Mapping[str, Any], json_path: str
) -> list[tuple[object, str]]:
    children: list[tuple[object, str]] = []
    for map_key in _SCHEMA_MAP_KEYS:
        sub_map = node.get(map_key)
        if isinstance(sub_map, dict):
            for sub_name, sub_schema in sub_map.items():
                children.append((sub_schema, f"{json_path}.{map_key}.{sub_name}"))
    return children


def _collect_keyword_subschemas(
    node: Mapping[str, Any], json_path: str
) -> list[tuple[object, str]]:
    children: list[tuple[object, str]] = []
    for single_key in _SCHEMA_SINGLE_KEYS:
        sub_node = node.get(single_key)
        if isinstance(sub_node, dict):
            children.append((sub_node, f"{json_path}.{single_key}"))
    for list_key in _SCHEMA_LIST_KEYS:
        sub_list = node.get(list_key)
        if isinstance(sub_list, list):
            for idx, item_schema in enumerate(sub_list):
                children.append((item_schema, f"{json_path}.{list_key}[{idx}]"))
    return children


def _walk_closed_schema(
    node: object, json_path: str, schema_rel: str, schema_errors: list[str]
) -> None:
    if not isinstance(node, dict):
        return
    children = _check_node_properties(node, json_path, schema_rel, schema_errors)
    children.extend(_collect_map_subschemas(node, json_path))
    children.extend(_collect_keyword_subschemas(node, json_path))
    for child_node, child_path in children:
        _walk_closed_schema(child_node, child_path, schema_rel, schema_errors)


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

    _walk_closed_schema(data, "$", schema_rel, schema_errors)
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


def _build_output_validators(
    package: Path,
    output_schemas: Sequence[Path],
    valid_closed_schemas: Mapping[Path, dict[str, Any]],
) -> list[tuple[str, jsonschema.Draft202012Validator]]:
    validators: list[tuple[str, jsonschema.Draft202012Validator]] = []
    for schema_path in output_schemas:
        schema_data = valid_closed_schemas.get(schema_path)
        if schema_data is None:
            continue
        rel_posix = schema_path.relative_to(package).as_posix()
        validators.append(
            (
                rel_posix,
                jsonschema.Draft202012Validator(
                    schema_data,
                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
                ),
            )
        )
    return validators


def _format_first_schema_error(
    schema_rel: str,
    validator: jsonschema.Draft202012Validator,
    parsed_json: object,
) -> str | None:
    block_errs = sorted(
        validator.iter_errors(parsed_json),
        key=lambda e: str(list(e.absolute_path)),
    )
    if not block_errs:
        return None
    first = block_errs[0]
    loc = "/".join(map(str, first.absolute_path)) or "root"
    return f"{schema_rel} -> {loc}: {first.message}"


def _validate_json_example_block(
    rel_posix: str,
    block_idx: int,
    raw_json_block: str,
    schema_validators: Sequence[tuple[str, jsonschema.Draft202012Validator]],
) -> list[str]:
    try:
        parsed_json = json.loads(raw_json_block)
    except ValueError as exc:
        return [
            f"{rel_posix}: invalid JSON in worked example block #{block_idx}: {exc}"
        ]
    if not schema_validators:
        return []
    failures: list[str] = []
    for schema_rel, validator in schema_validators:
        failure = _format_first_schema_error(schema_rel, validator, parsed_json)
        if failure is None:
            return []
        failures.append(failure)
    msg = (
        f"{rel_posix}: worked example json block #{block_idx} fails output schema validation "
        f"({failures[0]})"
    )
    return [msg]


def _read_cached_markdown(path: Path, cached_texts: Mapping[Path, str]) -> str:
    if path in cached_texts:
        return cached_texts[path]
    return path.read_text(encoding="utf-8")


def _inspect_example_reference_file(
    ref_file: Path,
    package: Path,
    *,
    resolved_skill_links: set[Path],
    skill_noncode_set: set[str],
    cached_texts: Mapping[Path, str],
) -> tuple[list[str], int, list[tuple[str, int, str]], list[str] | None]:
    errors: list[str] = []
    rel_posix = ref_file.relative_to(package).as_posix()
    if ref_file.resolve() not in resolved_skill_links:
        errors.append(f"SKILL.md: missing markdown link to worked examples {rel_posix}")
    try:
        text = _read_cached_markdown(ref_file, cached_texts)
    except OSError as exc:
        return [*errors, f"{rel_posix}: {exc}"], 0, [], None

    blocks, unclosed = _extract_fenced_blocks(text)
    if unclosed:
        errors.append(
            f"{rel_posix}: unclosed triple-backtick code fence in worked examples"
        )
    json_blocks = [
        (rel_posix, idx, code_body)
        for idx, (lang, code_body) in enumerate(blocks, start=1)
        if lang == "json"
    ]
    method_noncode: list[str] | None = None
    if ref_file.name == "method.md":
        method_noncode = _extract_noncode_lines(text)
        duplicated = [ln for ln in method_noncode if ln in skill_noncode_set]
        if duplicated:
            errors.append(
                f"{rel_posix}: method.md repeats SKILL.md prose verbatim "
                f"(e.g. {duplicated[0]!r})"
            )
    return errors, len(blocks), json_blocks, method_noncode


def _validate_worked_examples(
    package: Path,
    *,
    resolved_skill_links: set[Path],
    output_schemas: Sequence[Path],
    valid_closed_schemas: Mapping[Path, dict[str, Any]],
    skill_noncode_set: set[str],
    cached_texts: Mapping[Path, str],
    runnable_validators: Sequence[Path] = (),
    deferred_jobs: list[_ExampleValidatorJob] | None = None,
) -> tuple[list[str], int, bool, list[str] | None]:
    """Validate worked-example presence, link reachability, fenced block count, and JSON schema conformance."""
    candidates = (package / "references/method.md", package / "references/examples.md")
    present_files = [
        p for p in candidates if p.exists() and not p.is_symlink() and p.is_file()
    ]
    if not present_files:
        missing_msg = (
            "Missing worked examples: require references/examples.md or references/method.md "
            f"with >= {MIN_WORKED_EXAMPLE_BLOCKS} fenced code blocks"
        )
        return [missing_msg], 0, False, None

    errors: list[str] = []
    total_blocks = 0
    json_blocks: list[tuple[str, int, str]] = []
    method_noncode: list[str] | None = None
    for ref_file in present_files:
        f_errs, count, f_json, f_method = _inspect_example_reference_file(
            ref_file,
            package,
            resolved_skill_links=resolved_skill_links,
            skill_noncode_set=skill_noncode_set,
            cached_texts=cached_texts,
        )
        errors.extend(f_errs)
        total_blocks += count
        json_blocks.extend(f_json)
        if f_method is not None:
            method_noncode = f_method

    errors.extend(
        _check_worked_example_totals(
            package,
            present_files,
            total_blocks,
            json_blocks,
            output_schemas,
            valid_closed_schemas,
            runnable_validators=runnable_validators,
            deferred_jobs=deferred_jobs,
        )
    )
    has_valid_examples = (
        len(errors) == 0
        and total_blocks >= MIN_WORKED_EXAMPLE_BLOCKS
        and len(json_blocks) >= 1
    )
    return errors, total_blocks, has_valid_examples, method_noncode


@dataclass(frozen=True)
class _ExampleValidatorJob:
    pkg_name: str
    rel_posix: str
    block_idx: int
    raw_json_block: str
    validators: tuple[tuple[Path, str], ...]


def _isolated_validator_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    return env


def _run_single_validator_example(
    script_path: Path,
    script_rel: str,
    rel_posix: str,
    block_idx: int,
    example_file: Path,
    env: Mapping[str, str],
) -> tuple[int, str | None]:
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-S", str(script_path), str(example_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        msg = (
            f"{rel_posix}: worked example json block #{block_idx} "
            f"failed to execute bundled validator {script_rel} ({exc})"
        )
        return 2, msg
    if proc.returncode == 0:
        return 0, None
    raw_detail = (proc.stderr or proc.stdout or "").strip()
    first_line = (
        raw_detail.splitlines()[0] if raw_detail else f"exit code {proc.returncode}"
    )
    msg = (
        f"{rel_posix}: worked example json block #{block_idx} "
        f"failed bundled validator {script_rel} (exit {proc.returncode}: {first_line})"
    )
    return proc.returncode, msg


def _evaluate_validator_job(
    job: _ExampleValidatorJob,
    example_file: Path,
    env: Mapping[str, str],
) -> list[str]:
    outcomes = [
        _run_single_validator_example(
            script_path,
            script_rel,
            job.rel_posix,
            job.block_idx,
            example_file,
            env,
        )
        for script_path, script_rel in job.validators
    ]
    fatal = [msg for rc, msg in outcomes if rc not in (0, 1) and msg is not None]
    if fatal:
        return fatal
    if any(rc == 0 for rc, _ in outcomes):
        return []
    return [msg for _, msg in outcomes if msg is not None]


def _write_and_evaluate_job(
    idx: int,
    job: _ExampleValidatorJob,
    tmp_root: Path,
    env: Mapping[str, str],
) -> tuple[str, list[str]]:
    example_file = tmp_root / f"example-{idx}-{job.block_idx}.json"
    example_file.write_text(job.raw_json_block, encoding="utf-8")
    return job.pkg_name, _evaluate_validator_job(job, example_file, env)


def _execute_validator_jobs(
    jobs: Sequence[_ExampleValidatorJob],
) -> dict[str, list[str]]:
    if not jobs:
        return {}
    env = _isolated_validator_env()
    errors_by_pkg: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="fred-skill-example-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        if len(jobs) == 1:
            results = [_write_and_evaluate_job(0, jobs[0], tmp_root, env)]
        else:
            workers = min(8, len(jobs))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(
                    pool.map(
                        lambda pair: _write_and_evaluate_job(
                            pair[0], pair[1], tmp_root, env
                        ),
                        enumerate(jobs),
                    )
                )
    for pkg_name, job_errors in results:
        if job_errors:
            errors_by_pkg.setdefault(pkg_name, []).extend(job_errors)
    return errors_by_pkg


def _build_package_validator_jobs(
    package: Path,
    valid_json_blocks: Sequence[tuple[str, int, str]],
    runnable_validators: Sequence[Path],
) -> list[_ExampleValidatorJob]:
    if not valid_json_blocks or not runnable_validators:
        return []
    validator_pairs = tuple(
        (script_path, script_path.relative_to(package).as_posix())
        for script_path in runnable_validators
    )
    return [
        _ExampleValidatorJob(
            pkg_name=package.name,
            rel_posix=rel_posix,
            block_idx=block_idx,
            raw_json_block=raw_json_block,
            validators=validator_pairs,
        )
        for rel_posix, block_idx, raw_json_block in valid_json_blocks
    ]


def _check_worked_example_totals(
    package: Path,
    present_files: Sequence[Path],
    total_blocks: int,
    json_blocks: Sequence[tuple[str, int, str]],
    output_schemas: Sequence[Path],
    valid_closed_schemas: Mapping[Path, dict[str, Any]],
    *,
    runnable_validators: Sequence[Path] = (),
    deferred_jobs: list[_ExampleValidatorJob] | None = None,
) -> list[str]:
    errors: list[str] = []
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
        return errors
    schema_validators = _build_output_validators(
        package, output_schemas, valid_closed_schemas
    )
    valid_json_blocks: list[tuple[str, int, str]] = []
    for rel_posix, block_idx, raw_json_block in json_blocks:
        block_errs = _validate_json_example_block(
            rel_posix, block_idx, raw_json_block, schema_validators
        )
        errors.extend(block_errs)
        if not any("invalid JSON in worked example block" in e for e in block_errs):
            valid_json_blocks.append((rel_posix, block_idx, raw_json_block))
    pkg_jobs = _build_package_validator_jobs(
        package, valid_json_blocks, runnable_validators
    )
    if deferred_jobs is not None:
        deferred_jobs.extend(pkg_jobs)
    else:
        errors.extend(_execute_validator_jobs(pkg_jobs).get(package.name, []))
    return errors


def _estimate_skill_dimensions(
    metadata: Mapping[str, Any], body: str
) -> tuple[int, int]:
    fm_lines = 2 + len(metadata)
    fm_chars = 8 + sum(len(f"{k}: {v}\n") for k, v in metadata.items())
    return fm_lines + len(body.splitlines()), fm_chars + len(body)


def _skill_noncode_lines(metadata: Mapping[str, Any], body: str) -> set[str]:
    lines = {ln.strip() for ln in body.splitlines() if ln.strip()}
    for key, val in metadata.items():
        if isinstance(val, str) and val.strip():
            lines.add(f"{key}: {val.strip()}")
            lines.add(f'{key}: "{val.strip()}"')
    return lines


def _validate_protocol_depth(
    package: Path,
    skill_lines: int,
    skill_chars: int,
    cached_texts: Mapping[Path, str],
) -> list[str]:
    """Require protocol reference files (references/*protocol.md) to exceed SKILL.md depth."""
    refs_dir = package / "references"
    if not refs_dir.is_dir():
        return []
    errors: list[str] = []
    for proto_path in sorted(refs_dir.glob("*protocol.md")):
        if proto_path.is_symlink() or not proto_path.is_file():
            continue
        rel_posix = proto_path.relative_to(package).as_posix()
        try:
            proto_text = _read_cached_markdown(proto_path, cached_texts)
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


def _check_single_bundled_script(
    package: Path,
    script_path: Path,
    cached_texts: dict[Path, str] | None = None,
) -> list[str]:
    rel_posix = script_path.relative_to(package).as_posix()
    try:
        script_text = _read_cached_utf8(script_path, cached_texts)
    except OSError as exc:
        return [f"{rel_posix}: {exc}"]
    errors: list[str] = []
    first_line = script_text.splitlines()[0] if script_text else ""
    if not (first_line.startswith("#!") and "python" in first_line):
        errors.append(
            f"{rel_posix}: bundled script must start with a Python shebang (#!/usr/bin/env python3)"
        )
    if os.name != "nt" and not os.access(script_path, os.X_OK):
        errors.append(
            f"{rel_posix}: bundled script must have executable permissions (chmod 0755)"
        )
    return errors


def _validate_bundled_scripts(
    package: Path,
    cached_texts: dict[Path, str] | None = None,
    *,
    scan_errors: Sequence[str] = (),
) -> tuple[list[str], bool, list[Path]]:
    """Require at least one executable scripts/validate_*.py validator with valid shebang and mode."""
    scripts_dir = package / "scripts"
    py_scripts = (
        sorted(
            p for p in scripts_dir.glob("*.py") if not p.is_symlink() and p.is_file()
        )
        if scripts_dir.is_dir() and not scripts_dir.is_symlink()
        else []
    )
    validator_scripts = [p for p in py_scripts if p.name.startswith("validate_")]
    errors: list[str] = []
    runnable: list[Path] = []
    if not validator_scripts:
        errors.append(
            "Missing bundled validator script: expected executable scripts/validate_*.py"
        )
    for script_path in validator_scripts:
        script_errs = _check_single_bundled_script(package, script_path, cached_texts)
        errors.extend(script_errs)
        rel_posix = script_path.relative_to(package).as_posix()
        vetted_clean = not any(err.startswith(f"{rel_posix}:") for err in scan_errors)
        if not script_errs and vetted_clean:
            runnable.append(script_path)
    return errors, (len(validator_scripts) > 0 and len(errors) == 0), runnable


def _collect_duplicated_method_lines(
    per_pkg_lines: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    line_to_pkgs: dict[str, list[str]] = {}
    for pkg_name, noncode_lines in per_pkg_lines.items():
        for ln in set(noncode_lines):
            line_to_pkgs.setdefault(ln, []).append(pkg_name)
    return {ln: pkgs for ln, pkgs in line_to_pkgs.items() if len(pkgs) > 1}


def _evaluate_method_boilerplate_duplication(
    per_pkg_lines: Mapping[str, Sequence[str]],
) -> tuple[list[str], float, float]:
    """Compute cross-package non-code line duplication across specialist references/method.md files."""
    if len(per_pkg_lines) <= 1:
        return [], 0.0, 0.0
    duplicated_lines = _collect_duplicated_method_lines(per_pkg_lines)
    total_instances = sum(len(lines) for lines in per_pkg_lines.values())
    duplicated_instances = sum(
        1 for lines in per_pkg_lines.values() for ln in lines if ln in duplicated_lines
    )
    dup_rate, dup_pct = _rate_pair(duplicated_instances, total_instances)
    if not duplicated_lines:
        return [], dup_rate, dup_pct
    sample_line, sample_pkgs = min(
        duplicated_lines.items(), key=lambda item: (-len(item[1]), item[0])
    )
    preview = ", ".join(sorted(sample_pkgs)[:4])
    dup_msg = (
        f"Specialist references/method.md boilerplate duplication detected ({dup_pct}% across "
        f"{len(per_pkg_lines)} files; e.g. {sample_line!r} in {preview})"
    )
    return [dup_msg], dup_rate, dup_pct


class _ParsedSkillPackage:
    """Single-pass validation errors and parsed metadata for one skill package."""

    __slots__ = (
        "description",
        "deterministic_constraint_coverage",
        "errors",
        "has_boundary_sections",
        "has_output_schema",
        "has_prompt_constraints",
        "has_routing_trigger",
        "has_validator_script",
        "has_worked_examples",
        "method_noncode_lines",
        "output_schemas",
        "prompt_body",
        "short_description",
        "worked_example_blocks",
    )

    def __init__(
        self,
        *,
        errors: list[str],
        output_schemas: list[str] | None = None,
        has_output_schema: bool = False,
        worked_example_blocks: int = 0,
        has_worked_examples: bool = False,
        has_validator_script: bool = False,
        has_prompt_constraints: bool = False,
        has_boundary_sections: bool = False,
        has_linked_companions: bool = True,
        has_routing_trigger: bool = False,
        description: str | None = None,
        short_description: str | None = None,
        prompt_body: str | None = None,
        method_noncode_lines: list[str] | None = None,
    ) -> None:
        self.errors = errors
        self.output_schemas = output_schemas if output_schemas is not None else []
        self.has_output_schema = has_output_schema
        self.worked_example_blocks = worked_example_blocks
        self.has_worked_examples = has_worked_examples
        self.has_validator_script = has_validator_script
        self.has_prompt_constraints = has_prompt_constraints
        self.has_boundary_sections = has_boundary_sections
        self.has_routing_trigger = has_routing_trigger
        self.deterministic_constraint_coverage = (
            has_output_schema
            and has_prompt_constraints
            and has_boundary_sections
            and has_linked_companions
        )
        self.description = description
        self.short_description = short_description
        self.prompt_body = prompt_body
        self.method_noncode_lines = method_noncode_lines


def _is_valid_skill_name(name: object, package_name: str) -> bool:
    return (
        isinstance(name, str)
        and bool(NAME.fullmatch(name))
        and len(name) <= 64
        and name == package_name
    )


def _check_frontmatter_xml_tags(metadata: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in ("name", "description"):
        val = metadata.get(field)
        if isinstance(val, str) and ("<" in val or ">" in val):
            issues.append(
                f"SKILL.md: frontmatter '{field}' must not contain XML tags ('<' or '>')"
            )
    return issues


def _check_description_clauses(stripped: str) -> list[str]:
    errors: list[str] = []
    use_match = DESCRIPTION_USE_WHEN_RE.search(stripped)
    dont_match = DESCRIPTION_DO_NOT_USE_RE.search(stripped)
    use_pos = use_match.start() if use_match is not None else len(stripped)
    dont_pos = dont_match.start() if dont_match is not None else len(stripped)
    first_clause_idx = min(use_pos, dont_pos)
    if not stripped[:first_clause_idx].strip(" .;:,-"):
        errors.append("SKILL.md: description must state capability before 'Use when'")
    if use_match is None:
        errors.append("SKILL.md: description missing 'Use when' trigger clause")
    if dont_match is None:
        errors.append(
            "SKILL.md: description missing 'Do not use' negative-boundary clause"
        )
    elif use_match is not None and dont_pos <= use_pos:
        errors.append("SKILL.md: description must place 'Use when' before 'Do not use'")
    return errors


def _validate_description_contract(description: object) -> list[str]:
    if not isinstance(description, str) or not description.strip():
        return [
            f"Description must be a nonempty string of at most {MAX_DESCRIPTION_CHARS} characters"
        ]
    stripped = description.strip()
    errors: list[str] = []
    if len(stripped) > MAX_DESCRIPTION_CHARS:
        errors.append(
            f"SKILL.md: description exceeds {MAX_DESCRIPTION_CHARS} characters "
            f"({len(stripped)} > {MAX_DESCRIPTION_CHARS})"
        )
    errors.extend(_check_description_clauses(stripped))
    return errors


def _validate_frontmatter_invocation_flag(
    package_name: str, metadata: Mapping[str, Any]
) -> tuple[list[str], bool | None]:
    guarded_msg = (
        "SKILL.md: mutating, publishing, transcript, or multi-pass skill "
        "must set 'disable-model-invocation: true'"
    )
    if "disable-model-invocation" not in metadata:
        if package_name in EXPLICIT_INVOCATION_ONLY_SKILLS:
            return [guarded_msg], False
        return [], False
    raw_flag = metadata["disable-model-invocation"]
    if not isinstance(raw_flag, bool):
        return ["SKILL.md: 'disable-model-invocation' must be a boolean"], None
    if package_name in EXPLICIT_INVOCATION_ONLY_SKILLS and not raw_flag:
        return [guarded_msg], raw_flag
    return [], raw_flag


def _validate_frontmatter_metadata(
    package_name: str, metadata: Mapping[str, Any]
) -> tuple[list[str], bool | None]:
    errors = _check_frontmatter_xml_tags(metadata)
    name = metadata.get("name")
    if not _is_valid_skill_name(name, package_name):
        errors.append("Invalid name or directory mismatch")
    description = metadata.get("description")
    errors.extend(_validate_description_contract(description))
    flag_errors, disable_model_flag = _validate_frontmatter_invocation_flag(
        package_name, metadata
    )
    errors.extend(flag_errors)
    if isinstance(name, str) and isinstance(description, str):
        metadata_tokens = estimate_tokens(f"{name} {description.strip()}")
        if metadata_tokens > MAX_METADATA_TOKENS:
            errors.append(
                f"SKILL.md: frontmatter metadata exceeds token budget "
                f"({metadata_tokens} > {MAX_METADATA_TOKENS} estimated tokens)"
            )
    return errors, disable_model_flag


def _extract_skill_heading(body: str) -> str:
    return next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
        "",
    )


def _trigger_content_words(text: str) -> list[str]:
    return [
        tok.lower()
        for tok in WORD_TOKEN_PATTERN.findall(text)
        if len(tok) >= 2 and tok.lower() not in DESCRIPTION_STOPWORDS
    ]


def _is_lowercased_acronym(token: str, acronym: str) -> bool:
    lower_acr = acronym.lower()
    if token.lower() == lower_acr:
        return token != acronym
    if token.lower() == f"{lower_acr}s":
        return not token.startswith(acronym)
    return False


def _check_heading_acronyms(description: str, heading: str) -> list[str]:
    acronyms = dict.fromkeys(HEADING_ACRONYM_PATTERN.findall(heading))
    if not acronyms:
        return []
    standalone_tokens = STANDALONE_ALPHA_PATTERN.findall(description)
    return [
        f"SKILL.md: description lowercases heading acronym '{acronym}'"
        for acronym in acronyms
        if any(_is_lowercased_acronym(tok, acronym) for tok in standalone_tokens)
    ]


def _validate_description_routing(
    metadata: Mapping[str, Any], body: str
) -> tuple[list[str], bool]:
    description = metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        return [], False
    errors: list[str] = []
    is_vague = bool(VAGUE_DESCRIPTION_PATTERN.search(description))
    if is_vague:
        errors.append(
            "SKILL.md: description contains vague catch-all phrasing; "
            "state concrete capabilities and triggers"
        )
    trigger_match = USE_WHEN_PATTERN.search(description)
    has_trigger = (
        trigger_match is not None
        and len(_trigger_content_words(trigger_match.group(1)))
        >= MIN_TRIGGER_CONTENT_WORDS
    )
    if not has_trigger:
        errors.append(
            "SKILL.md: description must include a 'Use when' trigger clause "
            f"with at least {MIN_TRIGGER_CONTENT_WORDS} content words"
        )
    errors.extend(_check_heading_acronyms(description, _extract_skill_heading(body)))
    return errors, (has_trigger and not is_vague)


def _validate_skill_body(body: str) -> tuple[list[str], bool]:
    errors: list[str] = []
    if not body.strip() or len(body.splitlines()) >= 500:
        errors.append("Skill body must be nonempty and under 500 lines")
    body_tokens = estimate_tokens(body)
    if body_tokens >= MAX_BODY_TOKENS:
        errors.append(
            f"SKILL.md: skill body exceeds token budget "
            f"({body_tokens} >= {MAX_BODY_TOKENS} estimated tokens; must be under {MAX_BODY_TOKENS})"
        )
    guardrail_errors = _check_skill_guardrails(body)
    errors.extend(guardrail_errors)
    has_boundary_sections = bool(body.strip() and not guardrail_errors)
    return errors, has_boundary_sections


def _resolve_skill_links(package: Path, body: str) -> set[Path]:
    return {
        resolved
        for target in extract_relative_links(body)
        if (resolved := resolve_relative_link_target(target, package)) is not None
    }


def _validate_schema_links(
    package: Path,
    resolved_skill_links: set[Path],
    assets_schemas: Sequence[Path],
    output_schemas: Sequence[Path],
) -> list[str]:
    if not output_schemas:
        return [
            f"Missing output schema: {package.name} must define an output schema (*.schema.json in assets/)"
        ]
    errors: list[str] = []
    for schema_file in assets_schemas:
        if schema_file.resolve() not in resolved_skill_links:
            errors.append(
                f"SKILL.md: missing markdown link to output schema "
                f"{schema_file.relative_to(package).as_posix()}"
            )
    return errors


_EXEMPT_COMPANION_RELS = frozenset(
    {
        "references/method.md",
        "references/examples.md",
        "references/evaluation-scenarios.json",
    }
)


def _iter_companion_candidates(package: Path) -> list[Path]:
    candidates: list[Path] = []
    for sub, pattern in (
        ("references", "*.md"),
        ("assets", "*.md"),
        ("scripts", "*.py"),
    ):
        sub_dir = package / sub
        if sub_dir.is_dir() and not sub_dir.is_symlink():
            candidates.extend(sorted(sub_dir.rglob(pattern)))
    return candidates


def _validate_companion_links(
    package: Path, resolved_skill_links: set[Path]
) -> list[str]:
    """Require package-local markdown templates/references and bundled scripts to be linked from SKILL.md."""
    errors: list[str] = []
    for candidate in _iter_companion_candidates(package):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        rel_posix = candidate.relative_to(package).as_posix()
        if rel_posix in _EXEMPT_COMPANION_RELS:
            continue
        if candidate.resolve() not in resolved_skill_links:
            errors.append(
                f"SKILL.md: missing markdown link to companion file {rel_posix}"
            )
    return errors


def _check_prompt_placeholders(prompt_content: str, prompt_rel: str) -> list[str]:
    placeholders = PLACEHOLDER_PATTERN.findall(prompt_content)
    unauthorized = sorted(
        {p for p in placeholders if p not in ALLOWED_PROMPT_PLACEHOLDERS}
    )
    if not unauthorized:
        return []
    allowed_str = ", ".join(sorted(ALLOWED_PROMPT_PLACEHOLDERS))
    return [
        f"{prompt_rel}: unauthorized placeholder '{bad}' (allowed: {allowed_str})"
        for bad in unauthorized
    ]


def _check_prompt_format(prompt_content: str, prompt_rel: str) -> list[str]:
    errors: list[str] = []
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
    errors.extend(_check_prompt_placeholders(prompt_content, prompt_rel))
    return errors


def _normalize_prompt_body(prompt_content: str) -> str | None:
    lines = prompt_content.splitlines()
    if len(lines) <= 1:
        return None
    collapsed = " ".join(" ".join(lines[1:]).split())
    return collapsed or None


def _validate_prompt_template(
    package: Path,
    output_schemas: list[Path],
    cached_texts: dict[Path, str],
) -> tuple[list[str], bool, str | None]:
    prompt_path = package / "assets/prompt.md"
    prompt_rel = prompt_path.relative_to(package).as_posix()
    if not prompt_path.is_file():
        return [f"Missing prompt template: {prompt_rel}"], False, None
    try:
        prompt_content = prompt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (
            [f"Binary or non-UTF-8 file in skill package: {prompt_rel}"],
            False,
            None,
        )
    except OSError as exc:
        return [f"{prompt_rel}: {exc}"], False, None

    if not prompt_path.is_symlink():
        cached_texts[prompt_path] = prompt_content

    errors = _check_prompt_format(prompt_content, prompt_rel)
    has_neg = bool(NEGATIVE_CONSTRAINT_PATTERN.search(prompt_content))
    if not has_neg:
        errors.append(
            f"{prompt_rel}: prompt template lacks negative constraints or boundary guardrails "
            "(explicit negative prohibitions required)"
        )
    mentions_schema = _prompt_mentions_schema(prompt_content, output_schemas)
    if output_schemas and not mentions_schema:
        example_schema = output_schemas[0].relative_to(package).as_posix()
        errors.append(
            f"{prompt_rel}: prompt template must mention the output schema contract "
            f"(e.g. {example_schema})"
        )
    return (
        errors,
        has_neg and mentions_schema,
        _normalize_prompt_body(prompt_content),
    )


def _check_scenarios_against_schema(
    scenarios_data: object, active_schema: dict[str, Any]
) -> list[str]:
    validator = jsonschema.Draft202012Validator(active_schema)
    schema_errors = sorted(
        validator.iter_errors(scenarios_data),
        key=lambda e: str(e.path),
    )
    if schema_errors:
        return [
            f"references/evaluation-scenarios.json schema error: {err.message}"
            for err in schema_errors
        ]
    scenarios = (
        scenarios_data.get("scenarios", []) if isinstance(scenarios_data, dict) else []
    )
    types = {
        s["id"].rsplit("-", 1)[-1]
        for s in scenarios
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }
    if not {"failure", "safe", "routing"}.issubset(types):
        return [
            "references/evaluation-scenarios.json must include failure, safe, and routing scenarios"
        ]
    return []


def _validate_evaluation_scenarios(
    package: Path,
    skill_name: str,
    scenario_schema: dict | None,
    cached_texts: dict[Path, str],
) -> list[str]:
    scenarios_path = package / "references/evaluation-scenarios.json"
    if not scenarios_path.is_file():
        return ["Missing evaluation scenarios: references/evaluation-scenarios.json"]
    try:
        raw_scenarios = scenarios_path.read_text(encoding="utf-8")
        if not scenarios_path.is_symlink():
            cached_texts[scenarios_path] = raw_scenarios
        scenarios_data = json.loads(raw_scenarios)
    except (OSError, ValueError):
        return []  # Malformed JSON or read error is caught during package file traversal

    active_schema = scenario_schema or get_scenario_schema()
    if active_schema is None:
        return []

    errors = _check_scenarios_against_schema(scenarios_data, active_schema)
    if isinstance(scenarios_data, dict) and isinstance(
        scenarios_data.get("scenarios"), list
    ):
        errors.extend(
            _validate_scenario_semantics(
                package, skill_name, scenarios_data["scenarios"]
            )
        )
    return errors


def _extract_cursor_path_strings(value: object) -> tuple[list[str], bool]:
    if isinstance(value, str):
        return [value], bool(value.strip())
    if isinstance(value, list) and value:
        valid = all(isinstance(item, str) and bool(item.strip()) for item in value)
        return ([str(item) for item in value] if valid else []), valid
    return [], False


def _is_safe_cursor_rel_path(raw_path: str) -> bool:
    if not raw_path.strip() or "\\" in raw_path or raw_path.startswith("/"):
        return False
    if PureWindowsPath(raw_path).is_absolute():
        return False
    return ".." not in Path(raw_path).parts


def _check_resolved_cursor_path(
    plugin_dir: Path, field: str, raw_path: str, label: str
) -> list[str]:
    candidate = plugin_dir / raw_path
    if candidate.is_symlink():
        return [f"{label}: '{field}' path '{raw_path}' must not be a symlink"]
    resolved = candidate.resolve()
    if not resolved.is_relative_to(plugin_dir.resolve()) or not resolved.exists():
        return [
            f"{label}: '{field}' path '{raw_path}' does not exist inside plugin directory"
        ]
    if field == "skills" and not (resolved / "SKILL.md").is_file():
        return [f"{label}: 'skills' path '{raw_path}' missing SKILL.md"]
    return []


def _validate_cursor_plugin_path(
    plugin_dir: Path, field: str, raw_path: str, label: str
) -> list[str]:
    if field == "logo" and raw_path.startswith(("http://", "https://")):
        return []
    if not _is_safe_cursor_rel_path(raw_path):
        return [
            f"{label}: '{field}' path '{raw_path}' must be a relative path without '..' or absolute prefixes"
        ]
    return _check_resolved_cursor_path(plugin_dir, field, raw_path, label)


def _validate_cursor_author_and_license(
    data: Mapping[str, Any], label: str
) -> list[str]:
    errors: list[str] = []
    author = data.get("author")
    author_name = author.get("name") if isinstance(author, dict) else None
    if not isinstance(author_name, str) or not author_name.strip():
        errors.append(f"{label}: 'author.name' must be a non-empty string")
    lic = data.get("license")
    if "license" in data and (not isinstance(lic, str) or not lic.strip()):
        errors.append(f"{label}: 'license' must be a non-empty string")
    return errors


def _validate_cursor_metadata_fields(
    data: Mapping[str, Any], expected_name: str, label: str
) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(data.keys()) - CURSOR_PLUGIN_ALLOWED_FIELDS)
    if unknown:
        errors.append(
            f"{label}: unsupported plugin manifest field(s): {', '.join(unknown)}"
        )
    raw_name = data.get("name")
    if not isinstance(raw_name, str) or not CURSOR_PLUGIN_NAME_PATTERN.match(raw_name):
        errors.append(f"{label}: 'name' must be lowercase kebab-case")
    elif raw_name != expected_name:
        errors.append(f"{label}: 'name' must equal '{expected_name}'")
    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append(f"{label}: 'description' must be a non-empty string")
    ver = data.get("version")
    if not isinstance(ver, str) or not CURSOR_PLUGIN_SEMVER_PATTERN.match(ver):
        errors.append(f"{label}: 'version' must be a semantic version")
    errors.extend(_validate_cursor_author_and_license(data, label))
    return errors


def _validate_cursor_field_paths(
    plugin_dir: Path, field: str, values: Sequence[str], label: str
) -> list[str]:
    errors: list[str] = []
    for raw_val in values:
        errors.extend(_validate_cursor_plugin_path(plugin_dir, field, raw_val, label))
    return errors


def _validate_cursor_paths_section(
    plugin_dir: Path, data: Mapping[str, Any], label: str
) -> list[str]:
    errors: list[str] = []
    _, skills_ok = _extract_cursor_path_strings(data.get("skills"))
    if not skills_ok:
        errors.append(
            f"{label}: 'skills' must be a non-empty relative path string or list of strings"
        )
    for field in CURSOR_PLUGIN_PATH_FIELDS:
        if field not in data:
            continue
        vals, ok = _extract_cursor_path_strings(data[field])
        if not ok and field != "skills" and not isinstance(data[field], dict):
            errors.append(f"{label}: invalid '{field}' path specification")
        errors.extend(_validate_cursor_field_paths(plugin_dir, field, vals, label))
    return errors


def _validate_cursor_plugin_manifest(
    plugin_dir: Path,
    expected_name: str,
    data: object,
    label: str = ".cursor-plugin/plugin.json",
) -> list[str]:
    if not isinstance(data, dict):
        return [f"{label}: plugin manifest must be a JSON object"]
    errors = _validate_cursor_metadata_fields(data, expected_name, label)
    errors.extend(_validate_cursor_paths_section(plugin_dir, data, label))
    return errors


def _vet_package_json_manifest(
    rel_posix: str, data: object, raw_json: str
) -> list[str]:
    if not isinstance(data, Mapping):
        return []
    if rel_posix in {".cursor-plugin/plugin.json", ".mcp.json"}:
        findings = vetting._inspect_manifest_object(
            data,
            raw_text=raw_json,
            file_label=rel_posix,
            allowed=frozenset(),
            refs=vetting._ManifestRefs(),
        )
        return [_format_vetting_finding(item) for item in findings]
    return []


def _inspect_json_text(
    path: Path,
    package: Path,
    rel_posix: str,
    raw_json: str,
    valid_closed_schemas: dict[Path, dict[str, Any]],
) -> list[str]:
    data = json.loads(raw_json)
    vet_errs = _vet_package_json_manifest(rel_posix, data, raw_json)
    if rel_posix == ".cursor-plugin/plugin.json":
        return [
            *_validate_cursor_plugin_manifest(package, package.name, data, rel_posix),
            *vet_errs,
        ]
    if not path.name.endswith(".schema.json"):
        return vet_errs
    _check_draft202012_schema(raw_json, data)
    if path.name.startswith("evaluation-scenarios"):
        return vet_errs
    closed_errors = _validate_closed_schema(data, rel_posix)
    if not closed_errors and isinstance(data, dict):
        valid_closed_schemas[path] = data
    return [*closed_errors, *vet_errs]


def _check_markdown_links(
    md_text: str, base_dir: Path, package_resolved: Path
) -> list[str]:
    errors: list[str] = []
    for target in extract_relative_links(md_text):
        resolved = resolve_relative_link_target(target, base_dir)
        if resolved is None:
            continue
        if not resolved.is_relative_to(package_resolved):
            errors.append(f"Reference escapes standalone package: {target}")
        elif not resolved.is_file():
            errors.append(f"Missing relative reference: {target}")
    return errors


def _inspect_markdown_text(
    path: Path,
    rel_posix: str,
    md_text: str,
    *,
    package_resolved: Path,
    references_dir: Path,
) -> list[str]:
    errors: list[str] = []
    if path.resolve().is_relative_to(references_dir):
        ref_tokens = estimate_tokens(md_text)
        if ref_tokens >= MAX_REFERENCE_TOKENS:
            errors.append(
                f"{rel_posix}: reference markdown exceeds token budget "
                f"({ref_tokens} >= {MAX_REFERENCE_TOKENS} estimated tokens; "
                f"must be under {MAX_REFERENCE_TOKENS})"
            )
    errors.extend(_check_markdown_links(md_text, path.parent, package_resolved))
    if rel_posix != "SKILL.md":
        errors.extend(
            _format_vetting_finding(item)
            for item in vetting._vet_markdown_text(md_text, rel_posix)
        )
    return errors


def _vet_static_asset_or_yaml(
    path: Path, package: Path, rel_posix: str, raw_text: str
) -> list[str]:
    if path.suffix == ".svg":
        findings = vetting._vet_svg_text(raw_text, rel_posix)
        return [_format_vetting_finding(item) for item in findings]
    if path.suffix == ".yaml":
        findings = vetting._vet_agent_yaml_text(
            raw_text, rel_posix, boundary_root=package
        )
        return [_format_vetting_finding(item) for item in findings]
    return []


def _inspect_typed_file_content(
    path: Path,
    package: Path,
    rel_posix: str,
    raw_text: str,
    *,
    package_resolved: Path,
    references_dir: Path,
    valid_closed_schemas: dict[Path, dict[str, Any]],
) -> list[str]:
    if path.suffix == ".json":
        return _inspect_json_text(
            path, package, rel_posix, raw_text, valid_closed_schemas
        )
    if path.suffix == ".md":
        return _inspect_markdown_text(
            path,
            rel_posix,
            raw_text,
            package_resolved=package_resolved,
            references_dir=references_dir,
        )
    if path.suffix == ".py":
        return _inspect_python_script(
            raw_text,
            rel_posix,
            _skill_read_capabilities(package.name),
        )
    return _vet_static_asset_or_yaml(path, package, rel_posix, raw_text)


def _load_and_check_entry_text(
    path: Path,
    rel_posix: str,
    cached_texts: dict[Path, str],
) -> tuple[str | None, list[str]]:
    try:
        raw_text = _read_cached_utf8(path, cached_texts)
    except UnicodeDecodeError:
        return None, [f"Binary or non-UTF-8 file in skill package: {rel_posix}"]
    except OSError as exc:
        return None, [f"{rel_posix}: {exc}"]
    if rel_posix == "SKILL.md":
        return raw_text, []
    hygiene = _check_text_hygiene(
        raw_text, rel_posix, check_html_comments=(path.suffix == ".md")
    )
    if "\x00" in raw_text:
        return None, hygiene
    return raw_text, hygiene


def _inspect_package_entry(
    path: Path,
    package: Path,
    *,
    package_resolved: Path,
    references_dir: Path,
    cached_texts: dict[Path, str],
    valid_closed_schemas: dict[Path, dict[str, Any]],
) -> list[str]:
    if path.is_symlink():
        return [
            f"Symlink breaks standalone packaging: {path.relative_to(package).as_posix()}"
        ]
    if not path.is_file():
        return []
    rel_posix = path.relative_to(package).as_posix()
    if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
        return [f"Generated Python cache in package: {rel_posix}"]
    allow_err = _check_package_file_allowlist(rel_posix, path.name, path.suffix)
    if allow_err is not None:
        return [allow_err]
    raw_text, errors = _load_and_check_entry_text(path, rel_posix, cached_texts)
    if raw_text is None:
        return errors
    try:
        errors.extend(
            _inspect_typed_file_content(
                path,
                package,
                rel_posix,
                raw_text,
                package_resolved=package_resolved,
                references_dir=references_dir,
                valid_closed_schemas=valid_closed_schemas,
            )
        )
    except (ValueError, OSError, jsonschema.SchemaError) as exc:
        errors.append(f"{rel_posix}: {exc}")
    return errors


def _scan_package_files(
    package: Path, cached_texts: dict[Path, str]
) -> tuple[list[str], dict[Path, dict[str, Any]]]:
    errors: list[str] = []
    valid_closed_schemas: dict[Path, dict[str, Any]] = {}
    package_resolved = package.resolve()
    references_dir = (package / "references").resolve()
    for path in sorted(package.rglob("*")):
        errors.extend(
            _inspect_package_entry(
                path,
                package,
                package_resolved=package_resolved,
                references_dir=references_dir,
                cached_texts=cached_texts,
                valid_closed_schemas=valid_closed_schemas,
            )
        )
    for chain in find_deep_reference_chains(package, max_hops=1):
        errors.append(
            f"Reference chain exceeds 1-hop depth limit from SKILL.md: {chain}"
        )
    return errors, valid_closed_schemas


def _validate_ui_short_description(short: object) -> tuple[list[str], str | None]:
    if not isinstance(short, str):
        return ["UI short_description must contain 25–64 characters"], None
    normalized = short.strip() or None
    if not 25 <= len(short) <= 64:
        return ["UI short_description must contain 25–64 characters"], normalized
    if REPEATED_WORD_PATTERN.search(short):
        return ["UI short_description contains repeated adjacent words"], normalized
    return [], normalized


def _validate_ui_interface(
    package: Path,
    name: object,
    ui: object,
    cached_texts: dict[Path, str] | None = None,
) -> tuple[list[str], str | None]:
    if not isinstance(ui, dict):
        return ["Invalid optional UI metadata: missing interface"], None
    errors: list[str] = []
    display_name = ui.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        errors.append("Missing display name")
    short_errors, normalized_short = _validate_ui_short_description(
        ui.get("short_description")
    )
    errors.extend(short_errors)
    prompt = ui.get("default_prompt")
    if not isinstance(prompt, str) or f"${name}" not in prompt:
        errors.append("UI default_prompt must name the skill")
    errors.extend(
        _validate_ui_icon(package, "icon_small", ui.get("icon_small"), cached_texts)
    )
    errors.extend(
        _validate_ui_icon(package, "icon_large", ui.get("icon_large"), cached_texts)
    )
    return errors, normalized_short


def _is_nonempty_str_list(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(item, str) and bool(item.strip()) for item in value)


def _validate_ui_policy(policy: object) -> tuple[list[str], bool | None]:
    if not isinstance(policy, dict):
        return [
            "Missing UI policy: policy.allow_implicit_invocation must be a boolean"
        ], None
    errors: list[str] = []
    raw_allow = policy.get("allow_implicit_invocation")
    allow_implicit: bool | None = None
    if isinstance(raw_allow, bool):
        allow_implicit = raw_allow
    else:
        errors.append("UI policy.allow_implicit_invocation must be a boolean")
    if "products" in policy and not _is_nonempty_str_list(policy.get("products")):
        errors.append("UI policy.products must be a nonempty list of strings")
    return errors, allow_implicit


def _validate_invocation_policy_parity(
    package_name: str,
    disable_model_flag: bool | None,
    allow_implicit_flag: bool | None,
) -> list[str]:
    errors: list[str] = []
    if package_name in EXPLICIT_INVOCATION_ONLY_SKILLS and allow_implicit_flag is True:
        errors.append(
            "agents/openai.yaml: mutating, publishing, transcript, or multi-pass "
            "skill must set 'policy.allow_implicit_invocation: false'"
        )
    if disable_model_flag is None or allow_implicit_flag is None:
        return errors
    if disable_model_flag != (not allow_implicit_flag):
        errors.append(
            "Invocation policy mismatch: SKILL.md 'disable-model-invocation' "
            f"({disable_model_flag}) conflicts with agents/openai.yaml "
            f"'policy.allow_implicit_invocation' ({allow_implicit_flag})"
        )
    return errors


def _validate_ui_metadata(
    package: Path,
    name: object,
    cached_texts: dict[Path, str] | None = None,
) -> tuple[list[str], str | None, bool | None]:
    ui_path = package / "agents/openai.yaml"
    if ui_path.is_symlink() or not ui_path.exists():
        return [], None, None
    try:
        raw_ui = _read_cached_utf8(ui_path, cached_texts)
        ui_doc = yaml.safe_load(raw_ui)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        return [f"Invalid optional UI metadata: {exc}"], None, None
    if not isinstance(ui_doc, dict):
        return ["Invalid optional UI metadata: expected mapping"], None, None
    errors, short_description = _validate_ui_interface(
        package, name, ui_doc.get("interface"), cached_texts
    )
    policy_errors, allow_implicit = _validate_ui_policy(ui_doc.get("policy"))
    errors.extend(policy_errors)
    return errors, short_description, allow_implicit


def _inspect_skill_package(
    package: Path,
    scenario_schema: dict | None = None,
    *,
    deferred_jobs: list[_ExampleValidatorJob] | None = None,
) -> _ParsedSkillPackage:
    skill_md_path = package / "SKILL.md"
    raw_skill_errors = _inspect_skill_md_raw_bytes(skill_md_path)
    try:
        metadata, body = read_frontmatter(skill_md_path)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        _, fallback_schemas = _discover_output_schemas(package)
        return _ParsedSkillPackage(
            errors=[*raw_skill_errors, str(exc)],
            output_schemas=[
                p.relative_to(package).as_posix() for p in fallback_schemas
            ],
        )

    cached_texts: dict[Path, str] = {}
    if not skill_md_path.is_symlink():
        cached_texts[skill_md_path] = body

    errors = list(raw_skill_errors)
    fm_errors, disable_model_flag = _validate_frontmatter_metadata(
        package.name, metadata
    )
    errors.extend(fm_errors)
    routing_errors, has_routing_trigger = _validate_description_routing(metadata, body)
    errors.extend(routing_errors)
    body_errors, has_boundary_sections = _validate_skill_body(body)
    errors.extend(body_errors)

    resolved_skill_links = _resolve_skill_links(package, body)
    assets_schemas, output_schemas = _discover_output_schemas(package)
    schema_link_errors = _validate_schema_links(
        package, resolved_skill_links, assets_schemas, output_schemas
    )
    errors.extend(schema_link_errors)
    companion_link_errors = _validate_companion_links(package, resolved_skill_links)
    errors.extend(companion_link_errors)

    prompt_errors, has_prompt_constraints, prompt_body = _validate_prompt_template(
        package, output_schemas, cached_texts
    )
    errors.extend(prompt_errors)

    name = metadata.get("name")
    skill_name = name if isinstance(name, str) else package.name
    errors.extend(
        _validate_evaluation_scenarios(
            package, skill_name, scenario_schema, cached_texts
        )
    )

    ui_errors, short_description, allow_implicit_flag = _validate_ui_metadata(
        package, name, cached_texts
    )
    errors.extend(ui_errors)
    errors.extend(
        _validate_invocation_policy_parity(
            package.name, disable_model_flag, allow_implicit_flag
        )
    )

    scan_errors, valid_closed_schemas = _scan_package_files(package, cached_texts)
    errors.extend(scan_errors)

    script_errors, has_validator_script, runnable_validators = (
        _validate_bundled_scripts(package, cached_texts, scan_errors=scan_errors)
    )
    worked_errors, worked_blocks, has_worked_examples, method_noncode = (
        _validate_worked_examples(
            package,
            resolved_skill_links=resolved_skill_links,
            output_schemas=output_schemas,
            valid_closed_schemas=valid_closed_schemas,
            skill_noncode_set=_skill_noncode_lines(metadata, body),
            cached_texts=cached_texts,
            runnable_validators=runnable_validators,
            deferred_jobs=deferred_jobs,
        )
    )
    errors.extend(worked_errors)
    skill_lines, skill_chars = _estimate_skill_dimensions(metadata, body)
    errors.extend(
        _validate_protocol_depth(package, skill_lines, skill_chars, cached_texts)
    )
    errors.extend(script_errors)
    has_output_schema = (
        bool(output_schemas)
        and not schema_link_errors
        and all(p in valid_closed_schemas for p in output_schemas)
    )
    raw_description = metadata.get("description")
    return _ParsedSkillPackage(
        errors=errors,
        output_schemas=[p.relative_to(package).as_posix() for p in output_schemas],
        has_output_schema=has_output_schema,
        worked_example_blocks=worked_blocks,
        has_worked_examples=has_worked_examples,
        has_validator_script=has_validator_script,
        has_prompt_constraints=has_prompt_constraints,
        has_boundary_sections=has_boundary_sections,
        has_linked_companions=not companion_link_errors,
        has_routing_trigger=has_routing_trigger,
        description=raw_description if isinstance(raw_description, str) else None,
        short_description=short_description,
        prompt_body=prompt_body,
        method_noncode_lines=method_noncode,
    )


def validate_skill(package: Path, scenario_schema: dict | None = None) -> list[str]:
    return _inspect_skill_package(package, scenario_schema=scenario_schema).errors


DEFAULT_ATTEMPT_LEDGER_REL = ".fred-skills/validation-attempts.json"
ZERO_GIT_SHA = "0" * 40


def _git_output(repo: Path, *args: str) -> str | None:
    if not shutil.which("git"):
        return None
    proc = run_local_git(repo, *args, text=False, check=False)
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


def _fallback_parent_commit(root: Path) -> tuple[str | None, str, list[str]]:
    parent_sha = _resolve_commit_sha(root, "HEAD~1")
    if parent_sha is None:
        return None, "initial-commit", []
    return parent_sha, "git-diff", []


def _resolve_explicit_base_commit(
    root: Path, head_sha: str, explicit_ref: str
) -> tuple[str | None, str, list[str]]:
    if not explicit_ref or explicit_ref == ZERO_GIT_SHA:
        return _fallback_parent_commit(root)

    target_sha = _resolve_commit_sha(root, explicit_ref)
    if target_sha is None:
        return _fallback_parent_commit(root)

    merge_base = _git_output(root, "merge-base", target_sha, head_sha)
    if (
        not merge_base
        or not re.fullmatch(r"[0-9a-f]{40,64}", merge_base)
        or merge_base == head_sha
    ):
        return _fallback_parent_commit(root)
    return merge_base, "git-diff", []


def _resolve_default_base_commit(root: Path, head_sha: str) -> str:
    for candidate_branch in ("origin/main", "main"):
        branch_sha = _resolve_commit_sha(root, candidate_branch)
        if branch_sha is None:
            continue
        mb = _git_output(root, "merge-base", branch_sha, head_sha)
        if mb and re.fullmatch(r"[0-9a-f]{40,64}", mb):
            return mb
    return head_sha


def _resolve_diff_base(
    root: Path, explicit_ref: str | None
) -> tuple[str | None, str, list[str]]:
    has_non_zero_ref = bool(explicit_ref and explicit_ref != ZERO_GIT_SHA)
    if not _is_git_worktree_root(root):
        if has_non_zero_ref:
            return (
                None,
                "unversioned",
                [f"Cannot resolve git base ref '{explicit_ref}': not a git repository"],
            )
        return None, "unversioned", []

    head_sha = _resolve_commit_sha(root, "HEAD")
    if head_sha is None:
        if has_non_zero_ref:
            return (
                None,
                "unborn-head",
                [f"Cannot resolve git merge-base for '{explicit_ref}': unborn HEAD"],
            )
        return None, "unborn-head", []

    if explicit_ref is not None:
        return _resolve_explicit_base_commit(root, head_sha, explicit_ref)

    return _resolve_default_base_commit(root, head_sha), "git-diff", []


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
    base_commit, diff_scope, errors = _resolve_diff_base(root, explicit_ref)
    if errors:
        return set(), None, diff_scope, errors
    if base_commit is None:
        return current_set, None, diff_scope, []
    baseline = _skills_at_commit(root, base_commit)
    return current_set - baseline, base_commit, diff_scope, []


def _load_attempt_ledger(ledger_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = read_json_object(
            ledger_path,
            label="Validation attempt ledger",
            hint=f"Fix or delete corrupted '{ledger_path}', or pass --no-ledger.",
        )
    except FredCliError as exc:
        if exc.code == "missing_file":
            return {}
        raise
    skills_obj = payload.get("skills")
    if not isinstance(skills_obj, dict):
        raise FredCliError(
            f"Malformed validation attempt ledger '{ledger_path}': expected 'skills' object",
            code="invalid_ledger",
            hint=f"Ensure '{ledger_path}' contains a JSON object under 'skills', or delete it to reset.",
        )
    result: dict[str, dict[str, Any]] = {}
    for k, v in skills_obj.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            raise FredCliError(
                f"Malformed validation attempt ledger '{ledger_path}': entry '{k}' must be an object",
                code="invalid_ledger",
                hint=f"Fix or delete corrupted '{ledger_path}', or pass --no-ledger.",
            )
        result[k] = dict(v)
    return result


def _save_attempt_ledger(
    ledger_path: Path, entries: Mapping[str, dict[str, Any]]
) -> None:
    payload = {
        "schema_version": "1.0",
        "skills": {k: entries[k] for k in sorted(entries)},
    }
    atomic_json(ledger_path, payload)


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


def _load_validated_scenario_schema(
    root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    schema_path = root / "schemas/evaluation-scenarios.schema.json"
    rel_schema = schema_path.relative_to(root).as_posix()
    if not schema_path.is_file():
        return None, [f"Missing evaluation scenario schema: {rel_schema}"]
    try:
        scenario_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(scenario_schema)
        return scenario_schema, []
    except (ValueError, OSError, jsonschema.SchemaError) as exc:
        return None, [f"{rel_schema}: {exc}"]


def _discover_skill_packages(root: Path) -> tuple[list[Path], list[str]]:
    skills_dir = root / "skills"
    packages = (
        sorted(path for path in skills_dir.iterdir() if path.is_dir())
        if skills_dir.is_dir()
        else []
    )
    if not packages:
        return [], ["No skill packages found"]
    return packages, []


def _record_package_attempt(
    skill_name: str,
    *,
    is_new: bool,
    pkg_valid: bool,
    pkg_errors: Sequence[str],
    record_attempts: bool,
    now_iso: str,
    ledger_entries: dict[str, dict[str, Any]],
) -> tuple[bool | None, int, bool]:
    if not is_new:
        return None, 0, False
    existing_entry = ledger_entries.get(skill_name)
    if existing_entry is None:
        if record_attempts:
            ledger_entries[skill_name] = {
                "first_seen_at": now_iso,
                "last_checked_at": now_iso,
                "attempts": 1,
                "first_pass_valid": pkg_valid,
                "current_valid": pkg_valid,
                "initial_errors": list(pkg_errors),
            }
        return pkg_valid, 1, record_attempts

    first_pass_valid = bool(existing_entry.get("first_pass_valid", pkg_valid))
    prev_attempts = existing_entry.get("attempts", 1)
    attempts_count = (prev_attempts + 1) if isinstance(prev_attempts, int) else 2
    if record_attempts:
        existing_entry["last_checked_at"] = now_iso
        existing_entry["attempts"] = attempts_count
        existing_entry["current_valid"] = pkg_valid
        existing_entry["first_pass_valid"] = first_pass_valid
    return first_pass_valid, attempts_count, record_attempts


def _record_unique_descriptions(
    pkg_name: str,
    parsed: _ParsedSkillPackage,
    descriptions: dict[str, str],
    short_descriptions: dict[str, str],
    descriptions_by_pkg: dict[str, str],
) -> list[str]:
    problems: list[str] = []
    if parsed.description is not None:
        descriptions_by_pkg[pkg_name] = parsed.description
        if parsed.description in descriptions:
            problems.append(
                f"Duplicate activation description: {pkg_name} and {descriptions[parsed.description]}"
            )
        descriptions[parsed.description] = pkg_name

    if parsed.short_description is not None:
        if parsed.short_description in short_descriptions:
            problems.append(
                f"Duplicate UI short_description: {pkg_name} and {short_descriptions[parsed.short_description]}"
            )
        else:
            short_descriptions[parsed.short_description] = pkg_name
    return problems


def _extract_description_bigrams(description: str) -> set[tuple[str, str]]:
    bigrams: set[tuple[str, str]] = set()
    for clause in CLAUSE_SPLIT_PATTERN.split(description):
        words = _trigger_content_words(clause)
        bigrams.update(pairwise(words))
    return bigrams


def _pair_bigram_collision(
    pkg_a: str,
    pkg_b: str,
    suite_a: str | None,
    suite_b: str | None,
    bg_a: set[tuple[str, str]],
    bg_b: set[tuple[str, str]],
) -> str | None:
    if not suite_a or not suite_b or suite_a == suite_b:
        return None
    shared = sorted(f"{w1} {w2}" for w1, w2 in (bg_a & bg_b))
    if not shared:
        return None
    return (
        f"Cross-suite description bigram collision between {pkg_a} "
        f"({suite_a}) and {pkg_b} ({suite_b}): {', '.join(shared)}"
    )


def _check_cross_suite_bigram_collisions(
    root: Path, description_by_pkg: Mapping[str, str]
) -> list[str]:
    skill_suites = skill_to_suite_map(root)
    if not skill_suites:
        return []
    bigrams_by_pkg = [
        (pkg, _extract_description_bigrams(desc))
        for pkg, desc in sorted(description_by_pkg.items())
        if pkg in skill_suites
    ]
    collisions: list[str] = []
    for (pkg_a, bg_a), (pkg_b, bg_b) in combinations(bigrams_by_pkg, 2):
        err = _pair_bigram_collision(
            pkg_a, pkg_b, skill_suites.get(pkg_a), skill_suites.get(pkg_b), bg_a, bg_b
        )
        if err is not None:
            collisions.append(err)
    return collisions


def _record_unique_prompt_body(
    pkg_name: str,
    parsed: _ParsedSkillPackage,
    prompt_bodies: dict[str, str],
) -> list[str]:
    if parsed.prompt_body is None:
        return []
    if parsed.prompt_body in prompt_bodies:
        return [
            f"Duplicate prompt template body: {pkg_name} and {prompt_bodies[parsed.prompt_body]}"
        ]
    prompt_bodies[parsed.prompt_body] = pkg_name
    return []


def _build_package_telemetry_entry(
    pkg_name: str,
    parsed: _ParsedSkillPackage,
    *,
    is_new: bool,
    first_pass_valid: bool | None,
    attempts_count: int,
) -> dict[str, Any]:
    return {
        "name": pkg_name,
        "valid": len(parsed.errors) == 0,
        "is_new": is_new,
        "first_pass_valid": first_pass_valid,
        "validation_attempts": attempts_count,
        "has_output_schema": parsed.has_output_schema,
        "output_schemas": parsed.output_schemas,
        "worked_example_blocks": parsed.worked_example_blocks,
        "has_worked_examples": parsed.has_worked_examples,
        "has_validator_script": parsed.has_validator_script,
        "has_prompt_constraints": parsed.has_prompt_constraints,
        "has_boundary_sections": parsed.has_boundary_sections,
        "deterministic_constraint_coverage": parsed.deterministic_constraint_coverage,
        "errors": parsed.errors,
    }


def _validate_cursor_marketplace_source(
    root: Path, raw_source: object, label: str
) -> tuple[Path | None, list[str]]:
    if not isinstance(raw_source, str) or not _is_safe_cursor_rel_path(raw_source):
        return None, [
            f"{label}: 'source' must be a relative path without '..' or absolute prefixes"
        ]
    candidate = root / raw_source
    if candidate.is_symlink():
        return None, [f"{label}: 'source' path '{raw_source}' must not be a symlink"]
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_dir():
        return None, [
            f"{label}: 'source' path '{raw_source}' does not exist inside repository"
        ]
    return candidate, []


def _validate_cursor_marketplace_entry(
    root: Path, entry: object, idx: int
) -> list[str]:
    label = f".cursor-plugin/marketplace.json plugins[{idx}]"
    if not isinstance(entry, dict):
        return [f"{label}: plugin entry must be a JSON object"]
    plugin_dir, source_errs = _validate_cursor_marketplace_source(
        root, entry.get("source"), label
    )
    without_source = {k: v for k, v in entry.items() if k != "source"}
    expected_name = str(entry.get("name", ""))
    meta_errs = _validate_cursor_metadata_fields(without_source, expected_name, label)
    if plugin_dir is None:
        return [*source_errs, *meta_errs]
    return [
        *source_errs,
        *meta_errs,
        *_validate_cursor_paths_section(plugin_dir, without_source, label),
    ]


def _validate_cursor_marketplace(root: Path) -> list[str]:
    mp_path = root / ".cursor-plugin/marketplace.json"
    if mp_path.is_symlink():
        return ["Root .cursor-plugin/marketplace.json must not be a symlink"]
    if not mp_path.exists():
        return []
    doc, load_errs = _load_optional_json_dict(
        mp_path, ".cursor-plugin/marketplace.json"
    )
    if load_errs or doc is None:
        return load_errs
    plugins = doc.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return [".cursor-plugin/marketplace.json: 'plugins' must be a non-empty array"]
    return [
        err
        for idx, item in enumerate(plugins)
        for err in _validate_cursor_marketplace_entry(root, item, idx)
    ]


def _validate_root_cursor_manifest(root: Path) -> list[str]:
    root_manifest = root / ".cursor-plugin/plugin.json"
    errors: list[str] = []
    if root_manifest.is_symlink():
        errors.append("Root .cursor-plugin/plugin.json must not be a symlink")
    elif root_manifest.exists():
        errors.append(
            "Root .cursor-plugin/plugin.json must be omitted so marketplace pack skill lists are not overridden"
        )
    errors.extend(_validate_cursor_marketplace(root))
    return errors


def _description_content_tokens(description: str) -> frozenset[str]:
    return frozenset(
        tok
        for tok in JACCARD_TOKEN_RE.findall(description.lower())
        if tok not in JACCARD_STOPWORDS
    )


def _resolve_package_suites(root: Path, pkg_names: Sequence[str]) -> dict[str, str]:
    configured = skill_to_suite_map(root)
    known_suites = tuple(dict.fromkeys(configured.values()))
    return {
        pkg: configured.get(pkg) or infer_suite_name(pkg, known_suites)
        for pkg in pkg_names
    }


def _check_cross_suite_pair(
    pkg_a: str,
    suite_a: str,
    tokens_a: frozenset[str],
    pkg_b: str,
    suite_b: str,
    tokens_b: frozenset[str],
) -> str | None:
    if suite_a == suite_b:
        return None
    union = tokens_a | tokens_b
    if not union:
        return None
    jaccard = len(tokens_a & tokens_b) / len(union)
    if jaccard <= MAX_CROSS_SUITE_JACCARD:
        return None
    return (
        f"Cross-suite description overlap ({jaccard:.4f} > "
        f"{MAX_CROSS_SUITE_JACCARD:.2f}): {pkg_a} ({suite_a}) and {pkg_b} ({suite_b})"
    )


def _validate_cross_suite_jaccard(
    root: Path, descriptions_by_pkg: Mapping[str, str]
) -> list[str]:
    ordered = sorted(descriptions_by_pkg)
    if len(ordered) <= 1:
        return []
    suites_by_pkg = _resolve_package_suites(root, ordered)
    tokens_by_pkg = {
        pkg: _description_content_tokens(descriptions_by_pkg[pkg]) for pkg in ordered
    }
    errors: list[str] = []
    for idx, pkg_a in enumerate(ordered):
        for pkg_b in ordered[idx + 1 :]:
            pair_err = _check_cross_suite_pair(
                pkg_a,
                suites_by_pkg[pkg_a],
                tokens_by_pkg[pkg_a],
                pkg_b,
                suites_by_pkg[pkg_b],
                tokens_by_pkg[pkg_b],
            )
            if pair_err is not None:
                errors.append(pair_err)
    return errors


def _check_single_pack_budget(
    pack_name: str, pack_cfg: object, raw_chars_by_pkg: Mapping[str, int]
) -> str | None:
    if pack_name in UMBRELLA_PACK_NAMES or not isinstance(pack_cfg, dict):
        return None
    skills = pack_cfg.get("skills")
    if not isinstance(skills, list):
        return None
    pack_chars = sum(raw_chars_by_pkg.get(s, 0) for s in skills if isinstance(s, str))
    if pack_name == "starter" and pack_chars > MAX_STARTER_PACK_CHARS:
        return (
            f"Pack 'starter' exceeds Stage-1 starter budget "
            f"({pack_chars} > {MAX_STARTER_PACK_CHARS} raw characters)"
        )
    if pack_chars >= MAX_CURATED_PACK_CHARS:
        return (
            f"Pack '{pack_name}' exceeds Stage-1 pack budget "
            f"({pack_chars} >= {MAX_CURATED_PACK_CHARS} raw characters)"
        )
    return None


def _curated_pack_skills(packs: Mapping[str, object]) -> set[str]:
    covered: set[str] = set()
    for pack_name, pack_cfg in packs.items():
        if str(pack_name) in UMBRELLA_PACK_NAMES or not isinstance(pack_cfg, dict):
            continue
        skills = pack_cfg.get("skills")
        if isinstance(skills, list):
            covered.update(s for s in skills if isinstance(s, str))
    return covered


def _validate_curated_pack_coverage(
    root: Path, packs: Mapping[str, object]
) -> list[str]:
    if not packs or not (root / "catalog/suites.json").is_file():
        return []
    try:
        members = sorted(suite_members(root))
    except (OSError, ValueError, KeyError):
        return []
    covered = _curated_pack_skills(packs)
    return [
        f"Skill '{name}' in catalog/suites.json must belong to at least one curated pack in catalog/packs.json"
        for name in members
        if name not in covered
    ]


def _validate_pack_char_budgets(
    root: Path, raw_chars_by_pkg: Mapping[str, int]
) -> list[str]:
    packs_doc, load_errs = _load_optional_json_dict(
        root / "catalog/packs.json", "catalog/packs.json"
    )
    if load_errs or packs_doc is None:
        return load_errs
    packs = packs_doc.get("packs")
    if not isinstance(packs, dict):
        return []
    budget_errs = [
        err
        for pack_name, pack_cfg in packs.items()
        if (
            err := _check_single_pack_budget(str(pack_name), pack_cfg, raw_chars_by_pkg)
        )
        is not None
    ]
    return [*budget_errs, *_validate_curated_pack_coverage(root, packs)]


def _validate_stage1_char_budgets(
    root: Path, descriptions_by_pkg: Mapping[str, str]
) -> list[str]:
    raw_chars_by_pkg = {
        pkg: len(pkg) + len(desc.strip()) for pkg, desc in descriptions_by_pkg.items()
    }
    return _validate_pack_char_budgets(root, raw_chars_by_pkg)


def _validate_routing_and_budget_gates(
    root: Path, descriptions_by_pkg: Mapping[str, str]
) -> list[str]:
    return [
        *_validate_stage1_char_budgets(root, descriptions_by_pkg),
        *_validate_cross_suite_jaccard(root, descriptions_by_pkg),
    ]


def _validate_suite_manifest(root: Path) -> list[str]:
    if not (root / "catalog/suites.json").is_file():
        return []
    problems: list[str] = []
    try:
        for name in suite_members(root):
            skill_dir = root / "skills" / name
            ui_path = skill_dir / "agents/openai.yaml"
            if not (skill_dir / "SKILL.md").is_file():
                problems.append(f"Suite references absent skill: {name}")
            elif ui_path.is_symlink() or not ui_path.is_file():
                problems.append(
                    f"{name}: Suite skill missing required UI metadata: agents/openai.yaml"
                )
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"Invalid suite manifest: {exc}")
    problems.extend(_validate_root_cursor_manifest(root))
    return problems


_HOST_DISPLAY_LABELS: dict[str, str] = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    "antigravity": "Antigravity",
    "gemini-cli": "Gemini CLI",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot",
    "opencode": "OpenCode",
    "windsurf": "Windsurf",
    "kiro": "Kiro",
}
_BUILD_CATALOG_DOC_TOKENS: tuple[str, ...] = (
    "catalog/skills.json",
    "docs/catalog.md",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
    "gemini-extension.json",
)
_ACTION_PINS_DOC_TOKEN = ".github/action-pins.json"
_AUDIT_CORE_SKILLS: frozenset[str] = frozenset(
    {"repo-audit-program", "repo-audit-diagnostics", "repo-audit-synthesis"}
)
_IMPL_STATUS_BANNED_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"world's\s+best\s+skill\s+repository", re.IGNORECASE),
        "prompt-guardrail disclaimer",
    ),
    (
        re.compile(r"\bPrioritize\s+future\s+work\b", re.IGNORECASE),
        "obsolete backlog note",
    ),
    (
        re.compile(
            r"before\s+expanding\s+hosts,\s+scanners,\s+or\s+skill\s+counts",
            re.IGNORECASE,
        ),
        "obsolete backlog note",
    ),
)
_RESEARCH_BANNED_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"exhaustive\s+market\s+census", re.IGNORECASE),
        "prompt-guardrail disclaimer",
    ),
    (
        re.compile(r"proof\s+that\s+no\s+comparable\s+suite\s+exists", re.IGNORECASE),
        "prompt-guardrail disclaimer",
    ),
    (
        re.compile(r"measured\s+superiority", re.IGNORECASE),
        "prompt-guardrail disclaimer",
    ),
    (
        re.compile(r"market\s+uniqueness", re.IGNORECASE),
        "prompt-guardrail disclaimer",
    ),
    (
        re.compile(r"into\s+a\s+read-only\s+audit", re.IGNORECASE),
        "obsolete read-only scope claim",
    ),
)
_README_AUDIT_DOMAINS_RE = re.compile(
    r"\b([A-Za-z0-9]+)\s+audit\s+domains\b", re.IGNORECASE
)
_README_WORKFLOWS_RE = re.compile(
    r"\b([A-Za-z0-9]+)\s+workflows\s+from\s+finding\s+verification\b",
    re.IGNORECASE,
)
_README_ENGINEERING_RE = re.compile(
    r"\b([A-Za-z0-9]+)\s+(?:software\s+)?architecture,\s+code\s+review,\s+automated\s+testing,\s+and\s+spec-driven\s+development\s+skills\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _DocExpectations:
    suites_count: int | None
    total_skills: int | None
    audit_skills: int | None
    audit_domains: int | None
    workflow_skills: int | None
    engineering_skills: int | None
    deliverables: tuple[str, ...]
    hosts_count: int | None
    host_ids: tuple[str, ...]
    packs_count: int | None
    starter_skills: int | None
    shared_dirs: tuple[str, ...]


def _load_optional_json_dict(
    path: Path, rel_label: str
) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"{rel_label}: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{rel_label}: expected JSON object"]
    return data, []


def _suite_skill_names(suite: Mapping[str, Any]) -> list[str]:
    phases = suite.get("phases")
    if not isinstance(phases, list):
        return []
    return [
        skill
        for phase in phases
        if isinstance(phase, dict)
        for skill in phase.get("skills", [])
        if isinstance(skill, str)
    ]


def _suite_deliverable_filename(suite: Mapping[str, Any]) -> str | None:
    artifact = suite.get("artifact")
    if not isinstance(artifact, dict):
        return None
    filename = artifact.get("filename")
    return filename if isinstance(filename, str) else None


def _extract_suite_expectations(
    suites_doc: Mapping[str, Any] | None,
) -> tuple[
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    int | None,
    tuple[str, ...],
]:
    if suites_doc is None:
        return None, None, None, None, None, None, ()
    raw_suites = suites_doc.get("suites")
    if not isinstance(raw_suites, list):
        return None, None, None, None, None, None, ()
    suites = [s for s in raw_suites if isinstance(s, dict)]
    by_name = {str(s.get("name", "")): _suite_skill_names(s) for s in suites}
    total = sum(len(members) for members in by_name.values())
    audit_members = by_name.get("repository-audits", [])
    audit_domains = sum(1 for s in audit_members if s not in _AUDIT_CORE_SKILLS)
    workflows = len(by_name.get("repository-workflows", []))
    engineering = total - len(audit_members) - workflows
    deliverables = sorted(
        {
            fname
            for s in suites
            for fname in (_suite_deliverable_filename(s),)
            if fname is not None
        }
    )
    return (
        len(suites),
        total,
        len(audit_members),
        audit_domains,
        workflows,
        engineering,
        tuple(deliverables),
    )


def _extract_host_expectations(
    hosts_doc: Mapping[str, Any] | None,
) -> tuple[int | None, tuple[str, ...]]:
    if hosts_doc is None:
        return None, ()
    hosts = hosts_doc.get("hosts")
    if not isinstance(hosts, dict):
        return None, ()
    names = tuple(str(k) for k in hosts)
    return len(names), names


def _extract_pack_expectations(
    packs_doc: Mapping[str, Any] | None,
) -> tuple[int | None, int | None]:
    if packs_doc is None:
        return None, None
    packs = packs_doc.get("packs")
    if not isinstance(packs, dict):
        return None, None
    starter = packs.get("starter")
    starter_skills = starter.get("skills") if isinstance(starter, dict) else None
    starter_count = len(starter_skills) if isinstance(starter_skills, list) else None
    return len(packs), starter_count


def _discover_shared_suite_dirs(root: Path) -> tuple[str, ...]:
    shared_root = root / "shared"
    if not shared_root.is_dir():
        return ()
    return tuple(
        f"shared/{manifest.parent.name}/"
        for manifest in sorted(shared_root.glob("*/manifest.json"))
    )


def _load_doc_expectations(root: Path) -> tuple[_DocExpectations, list[str]]:
    suites_doc, s_errs = _load_optional_json_dict(
        root / "catalog/suites.json", "catalog/suites.json"
    )
    hosts_doc, h_errs = _load_optional_json_dict(
        root / "catalog/hosts.json", "catalog/hosts.json"
    )
    packs_doc, p_errs = _load_optional_json_dict(
        root / "catalog/packs.json", "catalog/packs.json"
    )
    (
        suites_count,
        total_skills,
        audit_skills,
        audit_domains,
        workflow_skills,
        engineering_skills,
        deliverables,
    ) = _extract_suite_expectations(suites_doc)
    hosts_count, host_ids = _extract_host_expectations(hosts_doc)
    packs_count, starter_skills = _extract_pack_expectations(packs_doc)
    expectations = _DocExpectations(
        suites_count=suites_count,
        total_skills=total_skills,
        audit_skills=audit_skills,
        audit_domains=audit_domains,
        workflow_skills=workflow_skills,
        engineering_skills=engineering_skills,
        deliverables=deliverables,
        hosts_count=hosts_count,
        host_ids=host_ids,
        packs_count=packs_count,
        starter_skills=starter_skills,
        shared_dirs=_discover_shared_suite_dirs(root),
    )
    return expectations, [*s_errs, *h_errs, *p_errs]


def _build_prose_count_rules(
    exp: _DocExpectations,
) -> tuple[tuple[re.Pattern[str], str, int | None], ...]:
    deliverables_total = len(exp.deliverables) if exp.deliverables else None
    return build_prose_count_rules(
        DocCountExpectations(
            total_skills=exp.total_skills,
            starter_skills=exp.starter_skills,
            suites_count=exp.suites_count,
            packs_count=exp.packs_count,
            hosts_count=exp.hosts_count,
            deliverables_count=deliverables_total,
            audit_skills=exp.audit_skills,
            audit_domains=exp.audit_domains,
            workflow_skills=exp.workflow_skills,
            engineering_skills=exp.engineering_skills,
        )
    )


def _check_prose_counts(
    rel_path: str,
    text: str,
    rules: Sequence[tuple[re.Pattern[str], str, int | None]],
) -> list[str]:
    return [
        f"{rel_path}: stale {m.label} count {m.found} in '{m.snippet}' (expected {m.expected})"
        for m in find_prose_count_mismatches(text, rules)
    ]


def _require_doc_tokens(
    rel_path: str, text: str, tokens: Sequence[str], category: str
) -> list[str]:
    return [
        f"{rel_path}: missing required {category} '{tok}'"
        for tok in tokens
        if tok not in text
    ]


def _forbid_doc_patterns(
    rel_path: str, text: str, rules: Sequence[tuple[re.Pattern[str], str]]
) -> list[str]:
    return [
        f"{rel_path}: disallowed {desc} ('{' '.join(m.group(0).split())}')"
        for pat, desc in rules
        for m in (pat.search(text),)
        if m is not None
    ]


def _extract_matched_prose_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    return parse_prose_int(match.group(1))


def _check_readme_itemized_sum(
    rel_path: str, intro_text: str, exp: _DocExpectations
) -> list[str]:
    if exp.total_skills is None:
        return []
    domains = _extract_matched_prose_int(_README_AUDIT_DOMAINS_RE, intro_text)
    workflows = _extract_matched_prose_int(_README_WORKFLOWS_RE, intro_text)
    engineering = _extract_matched_prose_int(_README_ENGINEERING_RE, intro_text)
    errors = _require_doc_tokens(
        rel_path,
        intro_text,
        ("a coordinator", "repo-audit-diagnostics", "findings synthesis"),
        "itemized skill entry",
    )
    if domains is None or workflows is None or engineering is None:
        errors.append(f"{rel_path}: missing itemized skill breakdown in introduction")
        return errors
    itemized_sum = domains + 3 + workflows + engineering
    if itemized_sum != exp.total_skills:
        errors.append(
            f"{rel_path}: itemized skill sum {itemized_sum} does not match catalog total {exp.total_skills}"
        )
    return errors


def _check_readme_host_names(
    rel_path: str, text: str, host_ids: Sequence[str]
) -> list[str]:
    missing: list[str] = []
    for host_id in host_ids:
        label = _HOST_DISPLAY_LABELS.get(host_id, host_id)
        if label.lower() not in text.lower():
            missing.append(f"{rel_path}: missing cataloged host '{label}' ({host_id})")
    return missing


def _validate_release_notes_doc(
    rel_path: str, text: str, exp: _DocExpectations
) -> list[str]:
    if exp.total_skills is None or exp.hosts_count is None or exp.packs_count is None:
        return []
    required_patterns = (
        (RELEASE_HOST_COUNT_RE, "host installation location count"),
        (RELEASE_SUITE_COUNT_RE, "suite count"),
        (RELEASE_PACK_COUNT_RE, "pack count"),
        (RELEASE_TOTAL_SKILLS_RE, "total skill count"),
    )
    return [
        f"{rel_path}: missing required {label}"
        for pat, label in required_patterns
        if _extract_matched_prose_int(pat, text) is None
    ]


def _validate_readme_doc(rel_path: str, text: str, exp: _DocExpectations) -> list[str]:
    intro = extract_markdown_preamble(text)
    return [
        *_check_readme_itemized_sum(rel_path, intro, exp),
        *_check_readme_host_names(rel_path, text, exp.host_ids),
    ]


def _validate_architecture_doc(
    rel_path: str, text: str, exp: _DocExpectations
) -> list[str]:
    return [
        *_require_doc_tokens(rel_path, text, exp.shared_dirs, "shared suite"),
        *_require_doc_tokens(rel_path, text, exp.deliverables, "JSON deliverable"),
        *_require_doc_tokens(
            rel_path, text, _BUILD_CATALOG_DOC_TOKENS, "build_catalog.py output"
        ),
        *_require_doc_tokens(
            rel_path, text, (_ACTION_PINS_DOC_TOKEN,), "workflow pin lockfile"
        ),
    ]


def _validate_contributing_doc(
    rel_path: str, text: str, exp: _DocExpectations
) -> list[str]:
    return [
        *_require_doc_tokens(rel_path, text, exp.shared_dirs, "shared suite"),
        *_require_doc_tokens(
            rel_path, text, _BUILD_CATALOG_DOC_TOKENS, "build_catalog.py output"
        ),
        *_require_doc_tokens(
            rel_path, text, (_ACTION_PINS_DOC_TOKEN,), "workflow pin lockfile"
        ),
        *_require_doc_tokens(rel_path, text, ("--phase format",), "check phase"),
    ]


def _validate_agents_doc(rel_path: str, text: str, _exp: _DocExpectations) -> list[str]:
    return [
        *_require_doc_tokens(
            rel_path, text, _BUILD_CATALOG_DOC_TOKENS, "build_catalog.py output"
        ),
        *_require_doc_tokens(
            rel_path, text, (_ACTION_PINS_DOC_TOKEN,), "workflow pin lockfile"
        ),
    ]


def _require_host_layouts(rel_path: str, text: str, exp: _DocExpectations) -> list[str]:
    if exp.hosts_count is None:
        return []
    if _extract_matched_prose_int(HOST_LAYOUTS_RE, text) is None:
        return [f"{rel_path}: missing required host layout count"]
    return []


def _validate_impl_status_doc(
    rel_path: str, text: str, exp: _DocExpectations
) -> list[str]:
    return [
        *_forbid_doc_patterns(rel_path, text, _IMPL_STATUS_BANNED_RULES),
        *_require_host_layouts(rel_path, text, exp),
    ]


def _validate_research_doc(
    rel_path: str, text: str, _exp: _DocExpectations
) -> list[str]:
    return _forbid_doc_patterns(rel_path, text, _RESEARCH_BANNED_RULES)


_SPECIFIC_DOC_VALIDATORS = {
    "README.md": _validate_readme_doc,
    "docs/architecture.md": _validate_architecture_doc,
    "CONTRIBUTING.md": _validate_contributing_doc,
    "AGENTS.md": _validate_agents_doc,
    "docs/validation.md": _require_host_layouts,
    "docs/implementation-status.md": _validate_impl_status_doc,
    "docs/research.md": _validate_research_doc,
}


def _validate_specific_doc_invariants(
    rel_path: str, text: str, exp: _DocExpectations
) -> list[str]:
    handler = _SPECIFIC_DOC_VALIDATORS.get(rel_path)
    if handler is None:
        return []
    return handler(rel_path, text, exp)


def _list_v_tag_versions(root: Path) -> set[str]:
    if not (root / ".git").exists() or not shutil.which("git"):
        return set()
    proc = run_local_git(
        root,
        "for-each-ref",
        "--format=%(refname:strip=2)",
        "refs/tags/v*",
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return {
        raw[1:]
        for line in proc.stdout.splitlines()
        if (raw := line.strip()).startswith("v") and len(raw) > 1
    }


def _discover_tagged_release_note_versions(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []
    tagged = _list_v_tag_versions(root)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for note_path in sorted(docs_dir.glob("release-notes-*.md")):
            ver = note_path.name.removeprefix("release-notes-").removesuffix(".md")
            if ver and git_tag_exists(root, f"v{ver}"):
                tagged.add(ver)
    return sorted(tagged)


def _check_single_tagged_release_note(root: Path, tag_ver: str) -> list[str]:
    rel_path = f"docs/release-notes-{tag_ver}.md"
    doc_path = root / rel_path
    blob_bytes = git_blob_bytes(root, f"refs/tags/v{tag_ver}:{rel_path}")
    if blob_bytes is None:
        if doc_path.exists() or doc_path.is_symlink():
            msg = (
                f"{rel_path}: tagged release notes immutability violation; "
                f"file did not exist at refs/tags/v{tag_ver}"
            )
            return [msg]
        return []
    if doc_path.is_symlink() or not doc_path.is_file():
        msg = (
            f"{rel_path}: tagged release notes immutability violation; "
            f"file missing or symlink after refs/tags/v{tag_ver}"
        )
        return [msg]
    if doc_path.read_bytes() != blob_bytes:
        msg = (
            f"{rel_path}: tagged release notes immutability violation; "
            f"content must remain byte-identical to refs/tags/v{tag_ver}:{rel_path}"
        )
        return [msg]
    return []


def _validate_tagged_release_notes_immutability(root: Path) -> list[str]:
    errors: list[str] = []
    for tag_ver in _discover_tagged_release_note_versions(root):
        errors.extend(_check_single_tagged_release_note(root, tag_ver))
    return errors


def _validate_single_doc_file(
    root: Path,
    rel_path: str,
    exp: _DocExpectations,
    count_rules: Sequence[tuple[re.Pattern[str], str, int | None]],
    *,
    is_release_notes: bool = False,
) -> list[str]:
    doc_path = root / rel_path
    if not doc_path.is_file():
        return []
    try:
        text = doc_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{rel_path}: {exc}"]
    errors = _check_prose_counts(rel_path, text, count_rules)
    if is_release_notes:
        errors.extend(_validate_release_notes_doc(rel_path, text, exp))
    else:
        errors.extend(_validate_specific_doc_invariants(rel_path, text, exp))
    return errors


def _validate_documentation_invariants(root: Path) -> list[str]:
    exp, load_errors = _load_doc_expectations(root)
    if load_errors:
        return load_errors
    count_rules = _build_prose_count_rules(exp)
    errors = _validate_tagged_release_notes_immutability(root)
    for rel_path in discover_untagged_release_notes(root):
        errors.extend(
            _validate_single_doc_file(
                root, rel_path, exp, count_rules, is_release_notes=True
            )
        )
    for rel_path in CORE_INVARIANT_DOCS:
        errors.extend(_validate_single_doc_file(root, rel_path, exp, count_rules))
    return errors


def _rate_pair(numerator: int, denominator: int) -> tuple[float, float]:
    raw = (numerator / denominator) if denominator > 0 else 0.0
    return round(raw, 4), round(raw * 100.0, 2)


def _compute_first_pass_rates(
    new_pkgs_telemetry: Sequence[Mapping[str, Any]],
    repo_pass_rate: float,
    repo_pass_rate_pct: float,
) -> tuple[int, int, int, float | None, float | None, float, float]:
    new_skills_count = len(new_pkgs_telemetry)
    new_skills_passed = sum(1 for p in new_pkgs_telemetry if p["valid"])
    new_skills_first_pass_count = sum(
        1 for p in new_pkgs_telemetry if p["first_pass_valid"] is True
    )
    if new_skills_count == 0:
        return (
            0,
            0,
            0,
            None,
            None,
            repo_pass_rate,
            repo_pass_rate_pct,
        )
    new_rate, new_pct = _rate_pair(new_skills_first_pass_count, new_skills_count)
    return (
        new_skills_count,
        new_skills_passed,
        new_skills_first_pass_count,
        new_rate,
        new_pct,
        new_rate,
        new_pct,
    )


def _build_telemetry_summary(
    *,
    now_iso: str,
    diff_scope: str,
    diff_base: str | None,
    package_telemetry: list[dict[str, Any]],
    routing_trigger_count: int = 0,
    method_dup_rate: float = 0.0,
    method_dup_pct: float = 0.0,
    duration: float,
) -> dict[str, Any]:
    total_pkgs = len(package_telemetry)
    passed_pkgs = sum(bool(p["valid"]) for p in package_telemetry)
    covered_pkgs = sum(
        bool(p["deterministic_constraint_coverage"]) for p in package_telemetry
    )
    schema_pkgs = sum(bool(p["has_output_schema"]) for p in package_telemetry)
    example_pkgs = sum(bool(p["has_worked_examples"]) for p in package_telemetry)
    validator_pkgs = sum(bool(p["has_validator_script"]) for p in package_telemetry)

    repo_rate, repo_pct = _rate_pair(passed_pkgs, total_pkgs)
    constraint_rate, constraint_pct = _rate_pair(covered_pkgs, total_pkgs)
    schema_rate, schema_pct = _rate_pair(schema_pkgs, total_pkgs)
    trigger_rate, trigger_pct = _rate_pair(routing_trigger_count, total_pkgs)
    worked_example_rate, worked_example_pct = _rate_pair(example_pkgs, total_pkgs)
    validator_script_rate, validator_script_pct = _rate_pair(validator_pkgs, total_pkgs)

    new_pkgs_telemetry = [p for p in package_telemetry if p["is_new"]]
    (
        new_skills_count,
        new_skills_passed,
        new_skills_first_pass_count,
        new_first_pass_rate,
        new_first_pass_pct,
        effective_first_pass_rate,
        effective_first_pass_pct,
    ) = _compute_first_pass_rates(new_pkgs_telemetry, repo_rate, repo_pct)

    return {
        "timestamp": now_iso,
        "diff_scope": diff_scope,
        "diff_base": diff_base,
        "total_packages": total_pkgs,
        "passed_packages": passed_pkgs,
        "failed_packages": total_pkgs - passed_pkgs,
        "repository_guardrail_pass_rate": repo_rate,
        "repository_guardrail_pass_rate_percentage": repo_pct,
        "new_skills_count": new_skills_count,
        "new_skills_passed": new_skills_passed,
        "new_skills_first_pass_count": new_skills_first_pass_count,
        "new_skills_first_pass_rate": new_first_pass_rate,
        "new_skills_first_pass_percentage": new_first_pass_pct,
        "first_pass_guardrail_pass_rate": effective_first_pass_rate,
        "first_pass_guardrail_pass_rate_percentage": effective_first_pass_pct,
        "deterministic_constraint_coverage": constraint_rate,
        "deterministic_constraint_coverage_percentage": constraint_pct,
        "output_schema_coverage": schema_rate,
        "output_schema_coverage_percentage": schema_pct,
        "routing_trigger_coverage": trigger_rate,
        "routing_trigger_coverage_percentage": trigger_pct,
        "skill_worked_example_coverage": worked_example_rate,
        "skill_worked_example_coverage_percentage": worked_example_pct,
        "validator_script_coverage": validator_script_rate,
        "validator_script_coverage_percentage": validator_script_pct,
        "method_boilerplate_duplication_rate": method_dup_rate,
        "method_boilerplate_duplication_percentage": method_dup_pct,
        "execution_time_seconds": duration,
        "packages": package_telemetry,
    }


def _inspect_packages_batch(
    packages: Sequence[Path], scenario_schema: dict | None
) -> list[tuple[Path, _ParsedSkillPackage]]:
    deferred_jobs: list[_ExampleValidatorJob] = []
    parsed_packages = [
        (
            package,
            _inspect_skill_package(
                package,
                scenario_schema=scenario_schema,
                deferred_jobs=deferred_jobs,
            ),
        )
        for package in packages
    ]
    validator_errors_by_pkg = _execute_validator_jobs(deferred_jobs)
    for package, parsed in parsed_packages:
        extra_errors = validator_errors_by_pkg.get(package.name)
        if extra_errors:
            parsed.errors.extend(extra_errors)
            parsed.has_worked_examples = False
    return parsed_packages


def validate_with_telemetry(
    root: Path = ROOT,
    *,
    base_ref: str | None = None,
    ledger_path: Path | None = None,
    record_attempts: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    start_time = time.perf_counter()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    scenario_schema, problems = _load_validated_scenario_schema(root)
    packages, pkg_problems = _discover_skill_packages(root)
    problems.extend(pkg_problems)

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
    new_skill_names = set(diff_new_skills)
    if base_ref is None and record_attempts:
        new_skill_names |= set(ledger_entries.keys()) & set(pkg_names)

    descriptions: dict[str, str] = {}
    descriptions_by_pkg: dict[str, str] = {}
    short_descriptions: dict[str, str] = {}
    prompt_bodies: dict[str, str] = {}
    method_lines_by_pkg: dict[str, list[str]] = {}
    package_telemetry: list[dict[str, Any]] = []
    routing_trigger_count = 0
    ledger_dirty = False

    for package, parsed in _inspect_packages_batch(packages, scenario_schema):
        if parsed.method_noncode_lines is not None:
            method_lines_by_pkg[package.name] = parsed.method_noncode_lines
        problems.extend(f"{package.name}: {error}" for error in parsed.errors)
        routing_trigger_count += int(parsed.has_routing_trigger)
        is_new = package.name in new_skill_names
        first_pass_valid, attempts_count, updated = _record_package_attempt(
            package.name,
            is_new=is_new,
            pkg_valid=len(parsed.errors) == 0,
            pkg_errors=parsed.errors,
            record_attempts=record_attempts,
            now_iso=now_iso,
            ledger_entries=ledger_entries,
        )
        ledger_dirty = ledger_dirty or updated
        package_telemetry.append(
            _build_package_telemetry_entry(
                package.name,
                parsed,
                is_new=is_new,
                first_pass_valid=first_pass_valid,
                attempts_count=attempts_count,
            )
        )
        problems.extend(
            _record_unique_descriptions(
                package.name,
                parsed,
                descriptions,
                short_descriptions,
                descriptions_by_pkg,
            )
        )
        problems.extend(_record_unique_prompt_body(package.name, parsed, prompt_bodies))

    if record_attempts and ledger_dirty:
        _save_attempt_ledger(effective_ledger_path, ledger_entries)

    problems.extend(_check_cross_suite_bigram_collisions(root, descriptions_by_pkg))
    problems.extend(_validate_suite_manifest(root))
    problems.extend(_validate_routing_and_budget_gates(root, descriptions_by_pkg))
    problems.extend(_validate_documentation_invariants(root))
    method_dup_errors, method_dup_rate, method_dup_pct = (
        _evaluate_method_boilerplate_duplication(method_lines_by_pkg)
    )
    problems.extend(method_dup_errors)
    duration = round(time.perf_counter() - start_time, 4)
    telemetry = _build_telemetry_summary(
        now_iso=now_iso,
        diff_scope=diff_scope,
        diff_base=diff_base,
        package_telemetry=package_telemetry,
        routing_trigger_count=routing_trigger_count,
        method_dup_rate=method_dup_rate,
        method_dup_pct=method_dup_pct,
        duration=duration,
    )
    return problems, telemetry


def validate(root: Path = ROOT) -> list[str]:
    errors, _ = validate_with_telemetry(root)
    return errors


def _build_arg_parser() -> argparse.ArgumentParser:
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
    return parser


def _write_telemetry_outputs(
    args: argparse.Namespace, telemetry: Mapping[str, Any]
) -> None:
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

    if args.step_summary is None:
        return
    summary_target = args.step_summary or os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_target:
        summary_path = Path(summary_target)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(format_telemetry_markdown(telemetry))


def _print_telemetry_report(
    errors: Sequence[str], telemetry: Mapping[str, Any]
) -> None:
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


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        errors, telemetry = validate_with_telemetry(
            root=args.root,
            base_ref=args.base_ref,
            ledger_path=args.ledger,
            record_attempts=not args.no_ledger,
        )
    except FredCliError as exc:
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"Hint: {exc.hint}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as exc:
        print(
            f"Error [subprocess_timeout]: Command timed out after {exc.timeout:g}s",
            file=sys.stderr,
        )
        return 2
    for error in errors:
        print(error)

    _write_telemetry_outputs(args, telemetry)
    _print_telemetry_report(errors, telemetry)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

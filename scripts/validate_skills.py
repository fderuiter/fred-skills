"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
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


_NON_AUDIT_NOTE_SUITES = frozenset(
    {"software-architecture", "code-review", "automated-testing"}
)
_NO_EXPECTED_FINDINGS_SUITES = frozenset(
    {"software-architecture", "automated-testing", "repository-workflows"}
)
_ALLOWED_PACKAGE_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".svg"})
_DISALLOWED_DOC_FILENAMES = frozenset({"readme.md", "changelog.md"})
_ALLOWED_CC_WHITESPACE = frozenset({"\n", "\r", "\t"})
_BANNED_SCRIPT_MODULES = frozenset(
    {
        "aiohttp",
        "builtins",
        "cffi",
        "ctypes",
        "curl",
        "ftplib",
        "grpc",
        "http",
        "httpx",
        "imaplib",
        "importlib",
        "paramiko",
        "poplib",
        "pty",
        "pycurl",
        "requests",
        "runpy",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib",
        "urllib3",
        "webbrowser",
        "websocket",
        "websockets",
        "wsgiref",
        "xmlrpc",
    }
)
_BANNED_SCRIPT_BUILTINS = frozenset(
    {"__builtins__", "__import__", "compile", "eval", "exec"}
)
_BANNED_OS_ATTRS = frozenset(
    {
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "forkpty",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "system",
    }
)


def _find_hidden_unicode(text: str) -> str | None:
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
    return _check_text_hygiene(raw_text, "SKILL.md", check_html_comments=True)


def _is_package_script_rel(rel_posix: str, suffix: str) -> bool:
    parts = rel_posix.split("/")
    return len(parts) == 2 and parts[0] == "scripts" and suffix == ".py"


def _check_package_file_allowlist(rel_posix: str, name: str, suffix: str) -> str | None:
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


def _ast_import_errors(node: ast.AST, rel_posix: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"{rel_posix}: banned import '{alias.name}' (line {node.lineno})"
            for alias in node.names
            if alias.name.split(".")[0] in _BANNED_SCRIPT_MODULES
        ]
    if isinstance(node, ast.ImportFrom) and node.module:
        root_mod = node.module.split(".")[0]
        if root_mod in _BANNED_SCRIPT_MODULES:
            return [
                f"{rel_posix}: banned import from '{node.module}' (line {node.lineno})"
            ]
    return []


def _ast_expr_error(node: ast.AST, rel_posix: str) -> str | None:
    if (
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in _BANNED_SCRIPT_BUILTINS
    ):
        return f"{rel_posix}: banned builtin '{node.id}' (line {node.lineno})"
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in _BANNED_OS_ATTRS
    ):
        return f"{rel_posix}: banned call 'os.{node.attr}' (line {node.lineno})"
    return None


def _inspect_python_script(source: str, rel_posix: str) -> list[str]:
    try:
        tree = ast.parse(source, filename=rel_posix)
    except SyntaxError as exc:
        return [f"{rel_posix}: invalid Python syntax ({exc.msg} at line {exc.lineno})"]
    issues: list[str] = []
    for node in ast.walk(tree):
        issues.extend(_ast_import_errors(node, rel_posix))
        expr_err = _ast_expr_error(node, rel_posix)
        if expr_err is not None:
            issues.append(expr_err)
    return issues


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
        svg_root = ET.fromstring(svg_text)
    except (OSError, UnicodeDecodeError, ET.ParseError) as exc:
        return [f"Invalid SVG in UI {field} ({raw_value}): {exc}"]
    tag = svg_root.tag.rsplit("}", 1)[-1]
    if tag != "svg":
        return [f"Invalid SVG root element in UI {field} ({raw_value}): {tag}"]
    return []


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


def _fallback_suite_name(skill_name: str) -> str:
    if skill_name.startswith("arch-"):
        return "software-architecture"
    if skill_name.startswith("review-"):
        return "code-review"
    if skill_name.startswith("test-"):
        return "automated-testing"
    if skill_name.startswith("repo-") and not skill_name.startswith("repo-audit-"):
        return "repository-workflows"
    return "repository-audits"


def _lookup_catalog_siblings(
    candidate_root: Path, skill_name: str
) -> tuple[str, set[str]] | None:
    try:
        members = suite_members(candidate_root)
        if skill_name not in members:
            return None
        suite_name = str(members[skill_name]["suite"])
        siblings = {
            s
            for s, info in members.items()
            if info.get("suite") == suite_name and s != skill_name
        }
        return suite_name, siblings
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _infer_suite_and_siblings(package: Path, skill_name: str) -> tuple[str, set[str]]:
    candidate_root = package.parent.parent
    if (candidate_root / "catalog/suites.json").is_file():
        catalog_match = _lookup_catalog_siblings(candidate_root, skill_name)
        if catalog_match is not None:
            return catalog_match
    return _fallback_suite_name(skill_name), set()


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
    suite_name, siblings = _infer_suite_and_siblings(package, skill_name)
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


class _ParsedSkillPackage:
    """Single-pass validation errors and parsed metadata for one skill package."""

    __slots__ = (
        "description",
        "deterministic_constraint_coverage",
        "errors",
        "has_boundary_sections",
        "has_output_schema",
        "has_prompt_constraints",
        "output_schemas",
        "prompt_body",
        "short_description",
    )

    def __init__(
        self,
        *,
        errors: list[str],
        output_schemas: list[str] | None = None,
        has_output_schema: bool = False,
        has_prompt_constraints: bool = False,
        has_boundary_sections: bool = False,
        description: str | None = None,
        short_description: str | None = None,
        prompt_body: str | None = None,
    ) -> None:
        self.errors = errors
        self.output_schemas = output_schemas if output_schemas is not None else []
        self.has_output_schema = has_output_schema
        self.has_prompt_constraints = has_prompt_constraints
        self.has_boundary_sections = has_boundary_sections
        self.deterministic_constraint_coverage = (
            has_output_schema and has_prompt_constraints and has_boundary_sections
        )
        self.description = description
        self.short_description = short_description
        self.prompt_body = prompt_body


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


def _validate_frontmatter_metadata(
    package_name: str, metadata: Mapping[str, Any]
) -> list[str]:
    errors = _check_frontmatter_xml_tags(metadata)
    name = metadata.get("name")
    if not _is_valid_skill_name(name, package_name):
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
    return errors


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


def _validate_schema_links(
    package: Path,
    body: str,
    assets_schemas: Sequence[Path],
    output_schemas: Sequence[Path],
) -> list[str]:
    if not output_schemas:
        return [
            f"Missing output schema: {package.name} must define an output schema (*.schema.json in assets/)"
        ]
    resolved_skill_links = {
        resolved
        for target in extract_relative_links(body)
        if (resolved := resolve_relative_link_target(target, package)) is not None
    }
    errors: list[str] = []
    for schema_file in assets_schemas:
        if schema_file.resolve() not in resolved_skill_links:
            errors.append(
                f"SKILL.md: missing markdown link to output schema "
                f"{schema_file.relative_to(package).as_posix()}"
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


def _inspect_json_text(
    path: Path,
    rel_posix: str,
    raw_json: str,
    valid_closed_schemas: set[Path],
) -> list[str]:
    data = json.loads(raw_json)
    if not path.name.endswith(".schema.json"):
        return []
    _check_draft202012_schema(raw_json, data)
    if path.name.startswith("evaluation-scenarios"):
        return []
    closed_errors = _validate_closed_schema(data, rel_posix)
    if not closed_errors:
        valid_closed_schemas.add(path)
    return closed_errors


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
    return errors


def _inspect_typed_file_content(
    path: Path,
    rel_posix: str,
    raw_text: str,
    *,
    package_resolved: Path,
    references_dir: Path,
    valid_closed_schemas: set[Path],
) -> list[str]:
    if path.suffix == ".json":
        return _inspect_json_text(path, rel_posix, raw_text, valid_closed_schemas)
    if path.suffix == ".md":
        return _inspect_markdown_text(
            path,
            rel_posix,
            raw_text,
            package_resolved=package_resolved,
            references_dir=references_dir,
        )
    if path.suffix == ".py":
        return _inspect_python_script(raw_text, rel_posix)
    return []


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
    valid_closed_schemas: set[Path],
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
) -> tuple[list[str], set[Path]]:
    errors: list[str] = []
    valid_closed_schemas: set[Path] = set()
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


def _validate_ui_policy(policy: object) -> list[str]:
    if not isinstance(policy, dict):
        return ["Missing UI policy: policy.allow_implicit_invocation must be a boolean"]
    errors: list[str] = []
    if not isinstance(policy.get("allow_implicit_invocation"), bool):
        errors.append("UI policy.allow_implicit_invocation must be a boolean")
    if "products" in policy and not _is_nonempty_str_list(policy.get("products")):
        errors.append("UI policy.products must be a nonempty list of strings")
    return errors


def _validate_ui_metadata(
    package: Path,
    name: object,
    cached_texts: dict[Path, str] | None = None,
) -> tuple[list[str], str | None]:
    ui_path = package / "agents/openai.yaml"
    if ui_path.is_symlink() or not ui_path.exists():
        return [], None
    try:
        raw_ui = _read_cached_utf8(ui_path, cached_texts)
        ui_doc = yaml.safe_load(raw_ui)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        return [f"Invalid optional UI metadata: {exc}"], None
    if not isinstance(ui_doc, dict):
        return ["Invalid optional UI metadata: expected mapping"], None
    errors, short_description = _validate_ui_interface(
        package, name, ui_doc.get("interface"), cached_texts
    )
    errors.extend(_validate_ui_policy(ui_doc.get("policy")))
    return errors, short_description


def _inspect_skill_package(
    package: Path, scenario_schema: dict | None = None
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
    errors.extend(_validate_frontmatter_metadata(package.name, metadata))
    body_errors, has_boundary_sections = _validate_skill_body(body)
    errors.extend(body_errors)

    assets_schemas, output_schemas = _discover_output_schemas(package)
    schema_link_errors = _validate_schema_links(
        package, body, assets_schemas, output_schemas
    )
    errors.extend(schema_link_errors)

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

    ui_errors, short_description = _validate_ui_metadata(package, name, cached_texts)
    errors.extend(ui_errors)

    scan_errors, valid_closed_schemas = _scan_package_files(package, cached_texts)
    errors.extend(scan_errors)

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
        has_prompt_constraints=has_prompt_constraints,
        has_boundary_sections=has_boundary_sections,
        description=raw_description if isinstance(raw_description, str) else None,
        short_description=short_description,
        prompt_body=prompt_body,
    )


def validate_skill(package: Path, scenario_schema: dict | None = None) -> list[str]:
    return _inspect_skill_package(package, scenario_schema=scenario_schema).errors


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


def _resolve_explicit_base_commit(
    root: Path, head_sha: str, explicit_ref: str
) -> tuple[str | None, str, list[str]]:
    if not explicit_ref or explicit_ref == ZERO_GIT_SHA:
        parent_sha = _resolve_commit_sha(root, "HEAD~1")
        if parent_sha is None:
            return None, "initial-commit", []
        return parent_sha, "git-diff", []

    target_sha = _resolve_commit_sha(root, explicit_ref)
    if target_sha is None:
        return (
            None,
            "git-diff",
            [f"Could not resolve git base ref '{explicit_ref}'"],
        )
    merge_base = _git_output(root, "merge-base", target_sha, head_sha)
    if not merge_base or not re.fullmatch(r"[0-9a-f]{40,64}", merge_base):
        return (
            None,
            "git-diff",
            [f"Could not resolve git merge-base between '{explicit_ref}' and HEAD"],
        )
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
) -> list[str]:
    problems: list[str] = []
    if parsed.description is not None:
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
        "has_prompt_constraints": parsed.has_prompt_constraints,
        "has_boundary_sections": parsed.has_boundary_sections,
        "deterministic_constraint_coverage": parsed.deterministic_constraint_coverage,
        "errors": parsed.errors,
    }


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
    return problems


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
    duration: float,
) -> dict[str, Any]:
    total_pkgs = len(package_telemetry)
    passed_pkgs = sum(1 for p in package_telemetry if p["valid"])
    covered_pkgs = sum(
        1 for p in package_telemetry if p["deterministic_constraint_coverage"]
    )
    schema_pkgs = sum(1 for p in package_telemetry if p["has_output_schema"])

    repo_rate, repo_pct = _rate_pair(passed_pkgs, total_pkgs)
    constraint_rate, constraint_pct = _rate_pair(covered_pkgs, total_pkgs)
    schema_rate, schema_pct = _rate_pair(schema_pkgs, total_pkgs)

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
        "execution_time_seconds": duration,
        "packages": package_telemetry,
    }


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
    short_descriptions: dict[str, str] = {}
    prompt_bodies: dict[str, str] = {}
    package_telemetry: list[dict[str, Any]] = []
    ledger_dirty = False

    for package in packages:
        parsed = _inspect_skill_package(package, scenario_schema=scenario_schema)
        problems.extend(f"{package.name}: {error}" for error in parsed.errors)
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
                package.name, parsed, descriptions, short_descriptions
            )
        )
        problems.extend(_record_unique_prompt_body(package.name, parsed, prompt_bodies))

    if record_attempts and ledger_dirty:
        _save_attempt_ledger(effective_ledger_path, ledger_entries)

    problems.extend(_validate_suite_manifest(root))
    duration = round(time.perf_counter() - start_time, 4)
    telemetry = _build_telemetry_summary(
        now_iso=now_iso,
        diff_scope=diff_scope,
        diff_base=diff_base,
        package_telemetry=package_telemetry,
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
    print(f"  Execution Time: {telemetry['execution_time_seconds']}s")
    print("=" * 60)
    if not errors:
        print("PASS: skill metadata, schemas, and standalone references")


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    errors, telemetry = validate_with_telemetry(
        root=args.root,
        base_ref=args.base_ref,
        ledger_path=args.ledger,
        record_attempts=not args.no_ledger,
    )
    for error in errors:
        print(error)

    _write_telemetry_outputs(args, telemetry)
    _print_telemetry_report(errors, telemetry)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

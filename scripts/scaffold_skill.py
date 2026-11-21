#!/usr/bin/env python3
"""Scaffold a standalone compliant skill package with optional catalog sync."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

sys.dont_write_bytecode = True

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_catalog
import build_experience
import sync_audit_resources
from _doc_invariants import (
    CORE_INVARIANT_DOCS,
    PROSE_NUMBER_WORDS,
    DocCountExpectations,
    build_prose_count_rules,
    discover_untagged_release_notes,
    parse_prose_int,
)
from skill_tools import (
    ROOT,
    FredCliError,
    extract_markdown_preamble,
    infer_suite_name,
    read_frontmatter,
    read_json_object,
    read_utf8_text,
    suite_skills_by_suite,
)
from validate_skills import (
    _AUDIT_CORE_SKILLS,
    _README_AUDIT_DOMAINS_RE,
    _README_ENGINEERING_RE,
    _README_WORKFLOWS_RE,
    EXPLICIT_INVOCATION_ONLY_SKILLS,
    MAX_CURATED_PACK_CHARS,
    MAX_DESCRIPTION_CHARS,
    NAME,
    UMBRELLA_PACK_NAMES,
    _load_doc_expectations,
    _validate_description_contract,
)

VALID_MATURITIES: tuple[str, ...] = ("experimental", "beta", "stable")
RESERVED_SKILL_NAMES: frozenset[str] = frozenset({"fred-skills"})


def _default_title(name: str) -> str:
    """Derive a clean human-readable title from a kebab-case skill name."""
    acronyms = {
        "api": "API",
        "ci": "CI",
        "cd": "CD",
        "ui": "UI",
        "ux": "UX",
        "id": "ID",
    }
    words = [acronyms.get(part, part.capitalize()) for part in name.split("-") if part]
    return " ".join(words) if words else name


_SUITE_DESCRIPTION_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "repository-audits": (
        "Audit {topic} contracts",
        "checking {slug} bugs",
        "code edits",
    ),
    "repository-workflows": (
        "Run {topic} workflow steps",
        "handling {slug} tasks",
        "repo audits",
    ),
    "software-architecture": (
        "Specify {topic} topology",
        "designing {slug} APIs",
        "diff reviews",
    ),
    "code-review": (
        "Review {topic} PR hunks",
        "grading {slug} diffs",
        "system design",
    ),
    "automated-testing": (
        "Check {topic} suite coverage",
        "expanding {slug} tests",
        "issue triage",
    ),
    "spec-driven-development": (
        "Draft {topic} specs pre-code",
        "aligning {slug} scope",
        "live debugging",
    ),
}


def _lower_preserving_acronyms(text: str) -> str:
    return " ".join(
        word if (len(word) >= 2 and word.isupper()) else word.lower()
        for word in text.split()
    )


def _clean_scaffold_topic(title: str) -> str:
    cased = _lower_preserving_acronyms(title)
    for prefix in ("repo audit ", "repo ", "arch ", "review ", "test ", "spec "):
        if cased.lower().startswith(prefix) and len(cased) > len(prefix):
            cased = cased[len(prefix) :]
            break
    return cased[:24].rstrip()


def _default_description(
    name: str, title: str, suite_name: str = "repository-audits"
) -> str:
    """Construct a concise three-part description within 1..140 characters."""
    cap_tpl, use_tpl, dont_str = _SUITE_DESCRIPTION_TEMPLATES.get(
        suite_name, _SUITE_DESCRIPTION_TEMPLATES["repository-audits"]
    )
    topic = _clean_scaffold_topic(title) or name[:20]
    slug = name[:24].rstrip("-") or name[:20]
    desc = (
        f"{cap_tpl.format(topic=topic)}. "
        f"Use when {use_tpl.format(slug=slug)}. "
        f"Do not use for {dont_str}."
    )
    return desc[:MAX_DESCRIPTION_CHARS]


def _default_short_description(title: str, name: str) -> str:
    """Construct a UI short_description strictly within 25..64 characters without adjacent repeats."""
    lower_title = title.lower()
    verb = "Execute" if lower_title.startswith("run") else "Run"
    candidate = f"{verb} {lower_title} checks with evidence guardrails"
    if len(candidate) > 64:
        candidate = f"Execute {name[:36]} workflow with guardrails"
    if len(candidate) > 64:
        candidate = candidate[:64].rstrip()
    if len(candidate) < 25:
        candidate = f"{candidate} for repositories"[:64]
    return candidate


_SYMPTOM_IGNORED_TOKENS: frozenset[str] = frozenset(
    {"repo", "audit", "arch", "review", "test", "spec", "skill", "workflow"}
)

_SUITE_SYMPTOM_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "repository-audits": (
        "unchecked {focus} invariant",
        "{focus} contract regression",
        "missing {focus} boundary guard",
    ),
    "repository-workflows": (
        "unverified {focus} remediation step",
        "{focus} handoff state drift",
        "incomplete {focus} workflow trace",
    ),
    "software-architecture": (
        "leaky {focus} boundary coupling",
        "undocumented {focus} architecture decision",
        "{focus} component ownership drift",
    ),
    "code-review": (
        "unreviewed {focus} diff regression",
        "{focus} invariant broken in PR",
        "missing {focus} review safeguard",
    ),
    "automated-testing": (
        "missing {focus} boundary test",
        "unverified {focus} failure oracle",
        "{focus} regression coverage gap",
    ),
    "spec-driven-development": (
        "ambiguous {focus} acceptance scope",
        "underspecified {focus} edge case",
        "{focus} specification drift",
    ),
}


def _symptom_focus(name: str) -> str:
    tokens = [
        tok for tok in name.split("-") if tok and tok not in _SYMPTOM_IGNORED_TOKENS
    ]
    raw = " ".join(tokens[:3]) if tokens else name.replace("-", " ")
    return raw[:32].strip() or "module"


def _default_symptoms(name: str, suite_name: str = "repository-audits") -> list[str]:
    """Derive multi-word discovery symptom phrases from a skill identifier."""
    focus = _symptom_focus(name)
    templates = _SUITE_SYMPTOM_TEMPLATES.get(
        suite_name, _SUITE_SYMPTOM_TEMPLATES["repository-audits"]
    )
    return [tpl.format(focus=focus) for tpl in templates]


def _normalize_catalog_symptoms(
    symptoms: Sequence[str] | None,
    name: str,
    suite_name: str = "repository-audits",
) -> list[str]:
    defaults = _default_symptoms(name, suite_name)
    cleaned = [item.strip() for item in (symptoms or ()) if item and item.strip()]
    if not cleaned:
        return defaults
    result: list[str] = []
    seen: set[str] = set()
    for candidate in [*cleaned, *defaults]:
        key = candidate.lower()
        if key in seen:
            continue
        if (
            len(result) >= len(cleaned)
            and len(result) >= 3
            and any(" " in s for s in result)
        ):
            break
        seen.add(key)
        result.append(candidate)
    return result


def _resolve_suite_and_sibling(
    root: Path,
    name: str,
    explicit_suite: str | None,
) -> tuple[str, str]:
    """Resolve the target suite name and a valid near-neighbor sibling_skill."""
    suite_skills = suite_skills_by_suite(root) or {}
    available_names = list(suite_skills)

    target_suite = explicit_suite or infer_suite_name(name, available_names)
    for candidate in suite_skills.get(target_suite, ()):
        if candidate != name:
            return target_suite, candidate

    fallback_pairs: dict[str, tuple[str, str]] = {
        "software-architecture": (
            "arch-decision-record",
            "arch-boundary-specification",
        ),
        "code-review": (
            "review-diff-analysis",
            "review-invariant-violations",
        ),
        "automated-testing": (
            "test-gap-analysis",
            "test-case-generation",
        ),
        "spec-driven-development": (
            "spec-interview",
            "spec-authoring",
        ),
        "repository-workflows": (
            "repo-verify-finding",
            "repo-plan-fix",
        ),
        "repository-audits": (
            "repo-audit-concurrency",
            "repo-audit-invariants",
        ),
    }
    primary, secondary = fallback_pairs.get(
        target_suite, ("repo-audit-concurrency", "repo-audit-invariants")
    )
    return target_suite, (secondary if name == primary else primary)


def _safe_excluded_for_skill(manifest: dict[str, object], skill_name: str) -> set[str]:
    try:
        return sync_audit_resources._excluded_for_skill(manifest, skill_name)
    except (TypeError, ValueError):
        return set()


def _is_extra_suite_schema(rel: str, own_schema_filename: str) -> bool:
    return (
        rel.startswith("assets/")
        and rel.endswith(".schema.json")
        and Path(rel).name != own_schema_filename
    )


def _collect_manifest_companions(
    manifest: dict[str, object],
    shared: Path,
    root: Path,
    skill_name: str,
    own_schema_filename: str,
) -> tuple[list[str], list[str]]:
    excluded = _safe_excluded_for_skill(manifest, skill_name)
    file_targets = sync_audit_resources._collect_file_targets(
        manifest, shared, excluded
    )
    script_targets = sync_audit_resources._collect_script_targets(
        manifest.get("scripts", []),
        shared,
        root,
        root / "skills" / skill_name,
        skill_name,
        excluded,
        check=True,
        problems=[],
    )
    schemas: list[str] = []
    companions: list[str] = []
    for rel, _ in file_targets:
        if _is_extra_suite_schema(rel, own_schema_filename):
            schemas.append(rel)
        elif rel.endswith(".md") and rel != "assets/prompt.md":
            companions.append(rel)
    for rel_s, _ in script_targets:
        if rel_s.startswith("scripts/") and rel_s.endswith(".py"):
            companions.append(rel_s)
    return schemas, companions


def _discover_suite_synced_companions(
    root: Path,
    suite_name: str,
    skill_name: str,
    own_schema_filename: str,
) -> tuple[list[str], list[str]]:
    """Return (extra_schemas, extra_companions) synced into skill_name by shared manifests."""
    shared_dir = root / "shared"
    if not shared_dir.is_dir():
        return [], []
    schemas: list[str] = []
    companions: list[str] = []
    for child in sorted(shared_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = read_json_object(
            child / "manifest.json",
            label=f"Shared resource manifest '{child.name}/manifest.json'",
            optional=True,
        )
        if manifest is None or manifest.get("suite", "repository-audits") != suite_name:
            continue
        try:
            extra_s, extra_c = _collect_manifest_companions(
                manifest, child, root, skill_name, own_schema_filename
            )
        except (TypeError, ValueError):
            continue
        schemas.extend(extra_s)
        companions.extend(extra_c)
    return sorted(set(schemas)), sorted(set(companions))


def _yaml_double_quoted(value: str) -> str:
    """Serialize a single-line string safely as a double-quoted YAML scalar."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )
    return f'"{escaped}"'


def _render_skill_markdown(
    name: str,
    title: str,
    description: str,
    primary_schema_rel: str,
    primary_validator_rel: str,
    extra_schema_links: Sequence[str] = (),
    extra_companion_links: Sequence[str] = (),
    disable_model_invocation: bool = False,
) -> str:
    extra_clause = ""
    if extra_schema_links:
        formatted = ", ".join(f"[suite schema]({rel})" for rel in extra_schema_links)
        extra_clause = f" and {formatted}"
    guard_line = "disable-model-invocation: true\n" if disable_model_invocation else ""
    companion_clause = ""
    if extra_companion_links:
        comp_formatted = ", ".join(
            f"[suite resource]({rel})" for rel in extra_companion_links
        )
        companion_clause = f" Consult {comp_formatted} when applicable."
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_double_quoted(description)}\n"
        f"{guard_line}"
        "---\n\n"
        f"# {title}\n\n"
        f"Review the [starting prompt](assets/prompt.md), "
        f"[worked examples](references/examples.md), and "
        f"[evaluation scenarios](references/evaluation-scenarios.json) when validating scope.{companion_clause}\n"
        "Ground every conclusion in observed repository evidence rather than unverified assumptions.\n\n"
        "## Workflow\n\n"
        "1. Establish the target scope, explicit user request, and concrete repository boundaries.\n"
        "2. Inspect relevant source files, interfaces, and configuration contracts directly.\n"
        "3. Separate confirmed findings, static observations, and open validation gaps.\n"
        "4. Verify candidate conclusions against close safe controls before reporting.\n\n"
        "## Guardrails\n\n"
        "- Do not modify application source files or publish external artifacts without explicit user authorization.\n"
        "- Keep unrelated modules, public interfaces, and unverified claims unchanged.\n"
        "- Distinguish observed execution evidence from static inference.\n\n"
        "## Deliver\n\n"
        f"Format structured results conforming to the [output schema]({primary_schema_rel}){extra_clause}, "
        f"and verify deliverables with [output validator]({primary_validator_rel}).\n"
        "Cite exact relative file paths, concrete evidence, and remaining verification limits.\n"
    )


def _render_prompt_markdown(name: str, primary_schema_rel: str) -> str:
    return (
        f"Use {name} to inspect <repository> at <revision> within the requested scope.\n"
        f"Ground {name} claims in concrete file evidence and separate confirmed behavior from inference.\n"
        "Do not modify application code or publish external issues without explicit permission.\n"
        "Keep unrelated boundaries and existing invariants unchanged.\n"
        f"Emit structured output conforming to {primary_schema_rel}.\n"
    )


def _render_workflow_output_schema(name: str, title: str) -> str:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{title} Workflow Schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "skill",
            "summary",
            "task_breakdowns",
            "dependency_order",
            "file_scopes",
            "acceptance_criteria",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": "1.0"},
            "skill": {"type": "string", "const": name},
            "summary": {"type": "string", "minLength": 1},
            "task_breakdowns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "task_id",
                        "title",
                        "file_scopes",
                        "acceptance_criteria",
                    ],
                    "properties": {
                        "task_id": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "file_scopes": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "blocked_by": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "acceptance_criteria": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "dependency_order": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "file_scopes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def _render_output_schema(name: str, title: str) -> str:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{title} Output Schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "skill", "summary", "findings"],
        "properties": {
            "schema_version": {
                "type": "string",
                "const": "1.0",
            },
            "skill": {
                "type": "string",
                "const": name,
            },
            "summary": {
                "type": "string",
                "minLength": 1,
            },
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "title", "evidence"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "severity": {
                            "type": "string",
                            "enum": ["low", "medium", "high", "critical"],
                        },
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "limitations": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


SUITE_ICON_PALETTE: dict[str, tuple[str, str]] = {
    "repository-audits": ("#1E3A8A", "#93C5FD"),
    "repository-workflows": ("#065F46", "#6EE7B7"),
    "software-architecture": ("#4C1D95", "#C4B5FD"),
    "code-review": ("#9A3412", "#FDBA74"),
    "automated-testing": ("#881337", "#FDA4AF"),
    "spec-driven-development": ("#0F766E", "#5EEAD4"),
}


def _render_icon_svg(name: str, suite_name: str) -> str:
    """Render a deterministic 24x24 SVG icon using the suite background palette."""
    bg_hex, accent_hex = SUITE_ICON_PALETTE.get(
        suite_name, SUITE_ICON_PALETTE["repository-audits"]
    )
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    x1 = 6 + (digest[0] % 5)
    y1 = 7 + (digest[1] % 5)
    x2 = 18 - (digest[2] % 5)
    y2 = 17 - (digest[3] % 5)
    cx = 9 + (digest[4] % 7)
    cy = 9 + (digest[5] % 7)
    return (
        '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
        'xmlns="http://www.w3.org/2000/svg">\n'
        f'  <rect x="2" y="2" width="20" height="20" rx="5" fill="{bg_hex}"/>\n'
        '  <rect x="5.5" y="5.5" width="13" height="13" rx="2" stroke="#FFFFFF" stroke-width="1.5"/>\n'
        f'  <path d="M{x1} {y1}L{cx} {cy}L{x2} {y2}" stroke="{accent_hex}" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>\n'
        "</svg>\n"
    )


def _render_openai_yaml(
    name: str,
    title: str,
    short_description: str,
    allow_implicit_invocation: bool = True,
) -> str:
    default_prompt = (
        f"Use ${name} on this repository and report evidence-backed results."
    )
    implicit_str = "true" if allow_implicit_invocation else "false"
    return (
        "interface:\n"
        f"  display_name: {_yaml_double_quoted(title)}\n"
        f"  short_description: {_yaml_double_quoted(short_description)}\n"
        f"  default_prompt: {_yaml_double_quoted(default_prompt)}\n"
        '  icon_small: "assets/icon.svg"\n'
        '  icon_large: "assets/icon.svg"\n'
        "policy:\n"
        "  products:\n"
        "    - chatgpt\n"
        "    - codex\n"
        "    - api\n"
        "    - atlas\n"
        f"  allow_implicit_invocation: {implicit_str}\n"
    )


def _render_evaluation_scenarios(
    name: str,
    title: str,
    suite_name: str,
    sibling_skill: str,
) -> str:
    lower_title = title.lower()
    disallow_audit_word = suite_name in {
        "software-architecture",
        "code-review",
        "automated-testing",
        "spec-driven-development",
    }
    routing_note = (
        "Near-neighbor sibling routing check; verify routing to the designated "
        "neighboring skill rather than out-of-scope execution."
        if disallow_audit_word and "audit" in sibling_skill.lower()
        else (
            f"Near-neighbor sibling routing check; verify routing to {sibling_skill} "
            "rather than out-of-scope execution."
        )
    )
    payload: dict[str, Any] = {
        "topic": name,
        "scenarios": [
            {
                "id": f"{name}-failure",
                "prompt": f"Run {lower_title} analysis on the supplied module and report concrete issues.",
                "fixture_shape": (
                    f"A synthetic module violates {lower_title} boundary assumptions without "
                    "input validation or safeguard checks."
                ),
                "expected_behavior": [
                    "Identify the violated boundary with exact file and line evidence.",
                    "Avoid modifying repository files without explicit authorization.",
                ],
                "evaluation_note": (
                    "Fixture shape is for the author only; execute with raw source and a neutral prompt."
                ),
            },
            {
                "id": f"{name}-safe",
                "prompt": f"Run {lower_title} analysis on the supplied module and report concrete issues.",
                "fixture_shape": (
                    f"A synthetic module enforces {lower_title} invariants with explicit guards "
                    "and deterministic error handling."
                ),
                "expected_behavior": [
                    "Verify the active safeguards and report zero false-positive defects.",
                    "Record any unexecuted runtime boundaries accurately.",
                ],
                "evaluation_note": (
                    "Construct a close safe control; keep expected outcomes out of the execution prompt."
                ),
            },
            {
                "id": f"{name}-routing",
                "prompt": (
                    f"Execute the neighboring {sibling_skill} workflow contract on the supplied "
                    "module boundaries instead of this specialist scope."
                ),
                "sibling_skill": sibling_skill,
                "expected_behavior": [
                    f"Recognize that this request targets {sibling_skill} rather than {name}.",
                    f"Defer or route to {sibling_skill} instead of forcing an out-of-scope pass.",
                ],
                "evaluation_note": routing_note,
            },
        ],
        "execution_state": "specified-not-executed; see docs/validation.md for executed evaluations",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _standalone_example_payload(name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "skill": name,
        "summary": f"Inspected {title.lower()} boundaries across src/service/handler.py.",
        "findings": [
            {
                "id": "F-001",
                "title": f"Unchecked boundary precondition in {name} handler",
                "severity": "medium",
                "evidence": [
                    "src/service/handler.py:2-3 omits boundary precondition guard before state mutation."
                ],
            }
        ],
        "limitations": [
            "Static source inspection only; live integration harness was not executed."
        ],
    }


def _audit_example_payload(name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": f"example-{name[:32]}",
        "repository": {
            "identity": "example-service",
            "revision": "a1b2c3d",
            "dirty": False,
            "snapshot": "src/service/handler.py",
        },
        "scope": ["src/service/handler.py"],
        "coverage": [
            {
                "topic": name,
                "scope": "src/service/handler.py",
                "status": "inspected",
                "evidence_ids": ["EV-001"],
                "notes": f"Inspected {title.lower()} invariants in apply_transition.",
                "next_action": "none",
            }
        ],
        "contracts": [
            {
                "id": "C-001",
                "dimension": name,
                "scope": "src/service/handler.py",
                "intended": "apply_transition validates target_state before mutating record.",
                "observed": "apply_transition assigns target_state directly without guard.",
                "enforcement": "missing-precondition-check",
                "evidence_ids": ["EV-001"],
            }
        ],
        "evidence": [
            {
                "id": "EV-001",
                "path": "src/service/handler.py",
                "line_start": 2,
                "line_end": 3,
                "symbol": "apply_transition",
                "context": "apply_transition mutates record['state'] without checking target_state.",
            }
        ],
        "findings": [
            {
                "id": "F-001",
                "topic": name,
                "tags": [name, "boundary-guard"],
                "title": f"Unchecked target_state transition in {name} handler",
                "status": "supported",
                "severity": "medium",
                "confidence": "high",
                "contract_ids": ["C-001"],
                "evidence_ids": ["EV-001"],
                "preconditions": "Caller passes a payload dictionary missing target_state.",
                "sequence": [
                    "Caller invokes apply_transition(record, payload).",
                    "Handler indexes payload['target_state'] without precondition check.",
                ],
                "expected": "Validate target_state presence before mutating record state.",
                "actual": "Unchecked dictionary lookup raises KeyError or mutates state unsafely.",
                "impact": "Breaks deterministic transition error handling.",
                "root_cause": "unchecked-state-transition",
                "counterevidence": "Inspected src/service/handler.py; no upstream schema guard exists.",
                "remedy": "Check 'target_state' in payload and raise ValueError before mutation.",
                "acceptance": [
                    "Unit test verifies missing target_state raises ValueError cleanly."
                ],
                "validation": {
                    "state": "not-run",
                    "commands": [],
                    "result": "Static source inspection only; runtime harness not executed.",
                },
                "related_findings": [],
            }
        ],
        "issues": [],
        "limitations": [
            "Static source inspection only; live integration harness was not executed."
        ],
        "checks": [],
    }


def _workflow_example_payload(name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "skill": name,
        "summary": f"Planned bounded remediation step for {title.lower()} in src/service/handler.py.",
        "task_breakdowns": [
            {
                "task_id": "TASK-001",
                "title": f"Enforce boundary guard in {name} handler",
                "file_scopes": ["src/service/handler.py"],
                "blocked_by": [],
                "acceptance_criteria": [
                    "Reject payloads missing target_state with ValueError before state mutation."
                ],
            }
        ],
        "dependency_order": ["TASK-001"],
        "file_scopes": ["src/service/handler.py"],
        "acceptance_criteria": [
            "Reject payloads missing target_state with ValueError before state mutation."
        ],
    }


def _architecture_example_payload(name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "title": f"{title} Specification",
        "status": "accepted",
        "boundaries": [
            {
                "name": "service-handler-boundary",
                "type": "module",
                "description": "src/service/handler.py: validate transition payloads before mutating state.",
                "paths": ["src/service/handler.py"],
                "invariants": [
                    "State transitions require a validated target_state key."
                ],
            }
        ],
        "decisions": [
            {
                "id": "ADR-001",
                "title": f"Enforce explicit boundary validation for {name}",
                "context": "src/service/handler.py mutates state from untrusted payload dictionaries.",
                "decision": "Validate required transition keys at module entry before mutating state.",
                "paths": ["src/service/handler.py"],
                "consequences": [
                    "Invalid transition payloads fail fast without partial state mutation."
                ],
            }
        ],
    }


def _review_example_payload(_name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "verdict": "request_changes",
        "summary": f"Identified unguarded state transition in src/service/handler.py during {title.lower()}.",
        "findings": [
            {
                "id": "REV-001",
                "title": "Unchecked target_state lookup mutates handler state",
                "severity": "high",
                "file": "src/service/handler.py",
                "line": 3,
                "description": "apply_transition indexes payload['target_state'] without validating preconditions.",
                "recommendation": "Validate 'target_state' presence and raise ValueError before mutating record.",
            }
        ],
    }


def _testing_example_payload(_name: str, _title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "component": "service.handler",
        "framework": "unittest",
        "test_cases": [
            {
                "id": "TC-001",
                "name": "test_apply_transition_rejects_missing_target_state",
                "file": "tests/test_handler.py",
                "description": "src/service/handler.py: assert apply_transition({}, {}) raises ValueError without mutating state.",
                "oracle_type": "exception",
                "verified": False,
            }
        ],
    }


def _spec_example_payload(_name: str, title: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "title": f"{title} Feature Specification",
        "status": "approved",
        "summary": f"Specification for {title.lower()} safeguards in src/service/handler.py.",
        "interview_decisions": [
            {
                "id": "DEC-001",
                "category": "edge-case",
                "question": "How should apply_transition handle payloads missing target_state?",
                "decision": "Raise ValueError before mutating record['state'].",
                "rationale": "Preserves atomic state invariants on malformed inputs.",
            }
        ],
        "requirements": [
            {
                "id": "REQ-001",
                "description": "Validate target_state presence before mutating record state.",
                "priority": "must",
            }
        ],
        "new_file_scopes": ["src/service/handler.py"],
        "out_of_scope": ["External database schema migrations"],
        "verification_commands": ["python -m unittest discover -s tests"],
    }


_SUITE_EXAMPLE_BUILDERS = {
    "repository-audits": _audit_example_payload,
    "repository-workflows": _workflow_example_payload,
    "software-architecture": _architecture_example_payload,
    "code-review": _review_example_payload,
    "automated-testing": _testing_example_payload,
    "spec-driven-development": _spec_example_payload,
}


def _render_examples_markdown(
    name: str,
    title: str,
    primary_schema_rel: str,
    primary_validator_rel: str,
    *,
    suite_name: str | None = None,
) -> str:
    builder = (
        _SUITE_EXAMPLE_BUILDERS.get(suite_name) if suite_name is not None else None
    )
    sample_output = (
        builder(name, title)
        if builder is not None
        else _standalone_example_payload(name, title)
    )
    rendered_json = json.dumps(sample_output, indent=2, ensure_ascii=False)
    return (
        f"# Worked Examples: {title}\n\n"
        f"Load these examples on demand when formatting deliverables for `{primary_schema_rel}` "
        f"and running `{primary_validator_rel}`.\n\n"
        "## 1. Representative Input Context\n\n"
        "```python\n"
        "# src/service/handler.py\n"
        "def apply_transition(record: dict[str, object], payload: dict[str, object]) -> None:\n"
        '    record["state"] = payload["target_state"]  # mutates state without boundary guard\n'
        "```\n\n"
        f"## 2. Valid Deliverable Output (`{primary_schema_rel}`)\n\n"
        "```json\n"
        f"{rendered_json}\n"
        "```\n"
    )


def _extract_stdlib_schema_evaluator() -> str:
    source = (_SCRIPTS_DIR / "validate_review.py").read_text(encoding="utf-8")
    marker = "def _is_repo_relative("
    idx = source.index(marker)
    header_end = source.index("\n\n", source.index("from typing import Any")) + 2
    return source[header_end:idx]


def _render_validator_script(schema_filename: str) -> str:
    evaluator_body = _extract_stdlib_schema_evaluator()
    schema_default_tail = (
        f'        / "assets"\n        / "{schema_filename}",\n'
        if len(schema_filename) > 67
        else f'        / "assets/{schema_filename}",\n'
    )
    return (
        "#!/usr/bin/env python3\n"
        '"""Validate skill deliverable JSON against the bundled output schema."""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import json\n"
        "import pathlib\n"
        "import re\n"
        "import sys\n"
        "from collections.abc import Callable, Mapping\n"
        "from typing import Any\n\n"
        f"{evaluator_body}"
        "def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:\n"
        "    errors = _schema_errors(data, schema)\n"
        "    if errors or not isinstance(data, Mapping):\n"
        "        return errors\n"
        "    seen_ids: set[str] = set()\n"
        '    for item in data.get("findings", []):\n'
        '        if isinstance(item, Mapping) and isinstance(item.get("id"), str):\n'
        '            fid = item["id"]\n'
        "            if fid in seen_ids:\n"
        '                errors.append(f"Duplicate IDs in findings: {fid}")\n'
        "            seen_ids.add(fid)\n"
        "    return errors\n\n\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser(description=__doc__)\n"
        '    parser.add_argument("artifact", type=pathlib.Path)\n'
        "    parser.add_argument(\n"
        '        "--schema",\n'
        "        type=pathlib.Path,\n"
        "        default=pathlib.Path(__file__).resolve().parents[1]\n"
        f"{schema_default_tail}"
        "    )\n"
        "    args = parser.parse_args()\n"
        "    try:\n"
        "        errors = validate(\n"
        '            json.loads(args.artifact.read_text(encoding="utf-8")),\n'
        '            json.loads(args.schema.read_text(encoding="utf-8")),\n'
        "        )\n"
        "    except (OSError, TypeError, ValueError) as exc:\n"
        '        print(f"Invalid input: {exc}", file=sys.stderr)\n'
        "        return 2\n"
        "    if errors:\n"
        "        for err in errors:\n"
        "            print(err, file=sys.stderr)\n"
        "        return 1\n"
        "    print(\n"
        '        "PASS: deliverable structure and finding IDs; semantic claims require review."\n'
        "    )\n"
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n"
    )


def _validate_skill_identifier(name: str, root: Path) -> None:
    if not NAME.fullmatch(name) or len(name) > 64:
        raise ValueError(
            f"Invalid skill name {name!r}: must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ and be at most 64 characters"
        )
    if name in RESERVED_SKILL_NAMES:
        raise ValueError(f"Skill name {name!r} is reserved")

    packs_doc = read_json_object(
        root / "catalog/packs.json",
        label="Packs catalog 'catalog/packs.json'",
        optional=True,
    )
    if packs_doc is not None:
        packs = packs_doc.get("packs", {})
        if isinstance(packs, Mapping) and name in packs:
            raise ValueError(
                f"Skill name {name!r} collides with existing pack name in catalog/packs.json"
            )


def _read_skill_frontmatter_strict(path: Path) -> tuple[dict[str, Any], str]:
    """Read SKILL.md frontmatter strictly, raising FredCliError on read or parse errors."""
    read_utf8_text(path, label=f"Skill manifest '{path}'")
    try:
        meta, body = read_frontmatter(path)
    except FredCliError:
        raise
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise FredCliError(
            f"Invalid SKILL.md frontmatter in '{path}': {exc}",
            code="invalid_frontmatter",
            hint=f"Ensure '{path}' begins with a valid YAML mapping delimited by '---'.",
        ) from exc
    return meta, body


def _ensure_unique_description(description: str, root: Path, package_dir: Path) -> None:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return
    resolved_package = package_dir.resolve()
    for existing_entry in sorted(skills_root.glob("*/SKILL.md")):
        if existing_entry.parent.resolve() == resolved_package:
            continue
        meta, _ = _read_skill_frontmatter_strict(existing_entry)
        if meta.get("description") == description:
            raise ValueError(
                f"Activation description duplicates existing skill '{existing_entry.parent.name}'"
            )


def _validate_inputs(
    name: str,
    description: str,
    short_description: str,
    maturity: str,
    root: Path,
    package_dir: Path,
    force: bool,
) -> None:
    _validate_skill_identifier(name, root)
    desc_errors = _validate_description_contract(description)
    if desc_errors:
        raise ValueError("; ".join(desc_errors))

    if not 25 <= len(short_description) <= 64:
        raise ValueError(
            f"UI short_description must contain 25-64 characters (got {len(short_description)})"
        )

    if maturity not in VALID_MATURITIES:
        raise ValueError(
            f"Unsupported maturity {maturity!r} (expected one of: {', '.join(VALID_MATURITIES)})"
        )

    if package_dir.exists() and not force:
        raise FileExistsError(
            f"Skill directory already exists: {package_dir} (pass --force to overwrite)"
        )

    _ensure_unique_description(description, root, package_dir)


def _pack_raw_chars(root: Path, skill_names: Sequence[str]) -> int:
    total = 0
    for sk in skill_names:
        skill_md = root / "skills" / sk / "SKILL.md"
        if not skill_md.exists() and not skill_md.is_symlink():
            continue
        meta, _ = _read_skill_frontmatter_strict(skill_md)
        desc = str(meta.get("description", "")).strip()
        total += len(sk) + len(desc)
    return total


_LOCKED_CURATED_PACKS: frozenset[str] = frozenset({"starter", "session-hygiene"})

_SUITE_PREFERRED_PACKS: dict[str, tuple[str, ...]] = {
    "software-architecture": ("software-architecture",),
    "code-review": ("code-review",),
    "automated-testing": ("automated-testing",),
    "spec-driven-development": ("spec-driven-development",),
    "repository-workflows": ("verified-fixes",),
    "repository-audits": (
        "release-readiness",
        "web-review",
        "backend-consistency",
        "async-correctness",
    ),
}


def _is_curated_pack_entry(name: str, pack_obj: object) -> bool:
    return (
        name not in UMBRELLA_PACK_NAMES
        and isinstance(pack_obj, dict)
        and isinstance(pack_obj.get("skills"), list)
    )


def _pick_lowest_cost_pack(
    group: Sequence[str],
    curated: Mapping[str, dict[str, Any]],
    root: Path,
    skill_cost: int,
) -> str | None:
    candidates = [
        (_pack_raw_chars(root, curated[p]["skills"]), idx, p)
        for idx, p in enumerate(group)
        if _pack_raw_chars(root, curated[p]["skills"]) + skill_cost
        < MAX_CURATED_PACK_CHARS
    ]
    return min(candidates)[2] if candidates else None


def _select_curated_pack_for_skill(
    packs: dict[str, Any], root: Path, name: str, suite_name: str
) -> str | None:
    curated = {k: v for k, v in packs.items() if _is_curated_pack_entry(k, v)}
    if not curated or any(name in v["skills"] for v in curated.values()):
        return None
    unlocked = {k: v for k, v in curated.items() if k not in _LOCKED_CURATED_PACKS}
    if not unlocked:
        return None
    skill_cost = _pack_raw_chars(root, [name])
    preferred = [p for p in _SUITE_PREFERRED_PACKS.get(suite_name, ()) if p in unlocked]
    fallback = [p for p in unlocked if p not in preferred]
    return _pick_lowest_cost_pack(
        preferred, unlocked, root, skill_cost
    ) or _pick_lowest_cost_pack(fallback, unlocked, root, skill_cost)


def _append_pack_skill(pack_obj: dict[str, Any], name: str) -> bool:
    skills = pack_obj.get("skills")
    if not isinstance(skills, list) or name in skills:
        return False
    if skills and skills[-1] == "repo-audit-synthesis":
        skills.insert(len(skills) - 1, name)
    else:
        skills.append(name)
    return True


_FULL_AUDIT_DESC_COUNT_RE = re.compile(r"\b(All\s+)\d+(\s+specialist\s+audits)\b")
_COMPLETE_DESC_COUNT_RE = re.compile(r"(\()\d+(\s+skills;)")


def _rewrite_pack_description_count(
    pack_obj: dict[str, Any], pattern: re.Pattern[str], count: int
) -> bool:
    old_desc = pack_obj.get("description")
    if not isinstance(old_desc, str):
        return False
    new_desc = pattern.sub(rf"\g<1>{count}\g<2>", old_desc)
    if new_desc == old_desc:
        return False
    pack_obj["description"] = new_desc
    return True


def _sync_umbrella_pack_descriptions(packs: dict[str, Any]) -> bool:
    changed = False
    full_audit = packs.get("full-audit")
    if isinstance(full_audit, dict) and isinstance(full_audit.get("skills"), list):
        specialists = sum(
            1
            for sk in full_audit["skills"]
            if isinstance(sk, str) and sk not in _AUDIT_CORE_SKILLS
        )
        changed = (
            _rewrite_pack_description_count(
                full_audit, _FULL_AUDIT_DESC_COUNT_RE, specialists
            )
            or changed
        )

    complete = packs.get("complete")
    if isinstance(complete, dict) and isinstance(complete.get("skills"), list):
        total = sum(1 for sk in complete["skills"] if isinstance(sk, str))
        changed = (
            _rewrite_pack_description_count(complete, _COMPLETE_DESC_COUNT_RE, total)
            or changed
        )
    return changed


def _sync_full_audit_pack_membership(
    full_audit: dict[str, Any], name: str, suite_name: str
) -> bool:
    fa_skills = full_audit.get("skills")
    if suite_name == "repository-audits":
        was_sorted = isinstance(fa_skills, list) and fa_skills == sorted(fa_skills)
        if not _append_pack_skill(full_audit, name):
            return False
        if was_sorted and isinstance(fa_skills, list):
            fa_skills.sort()
        return True
    if isinstance(fa_skills, list) and name in fa_skills:
        full_audit["skills"] = [sk for sk in fa_skills if sk != name]
        return True
    return False


def _apply_pack_registrations(
    packs: dict[str, Any], root: Path, name: str, suite_name: str
) -> bool:
    changes: list[bool] = []
    chosen = _select_curated_pack_for_skill(packs, root, name, suite_name)
    if chosen is not None:
        changes.append(_append_pack_skill(packs[chosen], name))
    if isinstance(packs.get("complete"), dict):
        changes.append(_append_pack_skill(packs["complete"], name))
    if isinstance(packs.get("full-audit"), dict):
        changes.append(
            _sync_full_audit_pack_membership(packs["full-audit"], name, suite_name)
        )
    changes.append(_sync_umbrella_pack_descriptions(packs))
    return any(changes)


def _register_skill_in_packs(
    packs_path: Path, root: Path, name: str, suite_name: str
) -> bool:
    packs_doc = read_json_object(
        packs_path,
        label="Packs catalog 'catalog/packs.json'",
        optional=True,
    )
    if packs_doc is None:
        return False
    packs = packs_doc.get("packs")
    if not isinstance(packs, dict) or not packs:
        return False
    changed = _apply_pack_registrations(packs, root, name, suite_name)
    if changed:
        packs_path.write_text(
            json.dumps(packs_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return changed


_INT_TO_PROSE_WORD: dict[int, str] = {v: k for k, v in PROSE_NUMBER_WORDS.items()}
_EXTRA_TOTAL_SKILL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:loads|exposes|installs)\s+all\s+([A-Za-z0-9]+)\s+skills\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfull\s+([A-Za-z0-9]+)-skill\s+(?:repository|bundles?)\b",
        re.IGNORECASE,
    ),
)


def _format_prose_count_token(raw_token: str, expected: int) -> str:
    if raw_token.isalpha() and expected in _INT_TO_PROSE_WORD:
        word = _INT_TO_PROSE_WORD[expected]
        return word.capitalize() if raw_token[0].isupper() else word
    return str(expected)


def _spans_overlap(start: int, end: int, occupied: Sequence[tuple[int, int]]) -> bool:
    return any(not (end <= s or start >= e) for s, e in occupied)


def _collect_prose_count_edits(
    text: str,
    rules: Sequence[tuple[re.Pattern[str], int | None]],
    limit: int,
) -> list[tuple[int, int, str]]:
    edits: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for pattern, expected in rules:
        if expected is None:
            continue
        for match in pattern.finditer(text, 0, limit):
            raw_tok = match.group(1)
            found = parse_prose_int(raw_tok)
            start, end = match.span(1)
            if (
                found is None
                or found == expected
                or _spans_overlap(start, end, occupied)
            ):
                continue
            occupied.append((start, end))
            edits.append((start, end, _format_prose_count_token(raw_tok, expected)))
    return edits


def _rewrite_prose_counts_in_text(
    text: str,
    rules: Sequence[tuple[re.Pattern[str], int | None]],
    *,
    max_end: int | None = None,
) -> str:
    limit = len(text) if max_end is None else min(len(text), max_end)
    edits = _collect_prose_count_edits(text, rules, limit)
    if not edits:
        return text
    updated = text
    for start, end, replacement in sorted(edits, reverse=True):
        updated = f"{updated[:start]}{replacement}{updated[end:]}"
    return updated


def _sync_prose_count_docs(root: Path) -> list[Path]:
    exp, load_errors = _load_doc_expectations(root)
    if load_errors or exp.total_skills is None:
        return []
    deliverables_total = len(exp.deliverables) if exp.deliverables else None
    shared_rules = build_prose_count_rules(
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
    doc_rules: list[tuple[re.Pattern[str], int | None]] = [
        *((pat, expected) for pat, _label, expected in shared_rules),
        *((pat, exp.total_skills) for pat in _EXTRA_TOTAL_SKILL_PATTERNS),
    ]
    readme_intro_rules: tuple[tuple[re.Pattern[str], int | None], ...] = (
        (_README_AUDIT_DOMAINS_RE, exp.audit_domains),
        (_README_WORKFLOWS_RE, exp.workflow_skills),
        (_README_ENGINEERING_RE, exp.engineering_skills),
    )
    candidates = dict.fromkeys(
        [
            *CORE_INVARIANT_DOCS,
            "docs/integration.md",
            *discover_untagged_release_notes(root),
        ]
    )
    updated_paths: list[Path] = []
    for rel_path in candidates:
        doc_path = root / rel_path
        if doc_path.is_symlink() or not doc_path.is_file():
            continue
        original = read_utf8_text(doc_path, label=f"Documentation file '{rel_path}'")
        rewritten = _rewrite_prose_counts_in_text(original, doc_rules)
        if rel_path == "README.md":
            intro = extract_markdown_preamble(rewritten)
            rewritten = _rewrite_prose_counts_in_text(
                rewritten, readme_intro_rules, max_end=len(intro)
            )
        if rewritten != original:
            doc_path.write_text(rewritten, encoding="utf-8", newline="\n")
            updated_paths.append(doc_path)
    return updated_paths


def _load_or_default_suites_doc(
    suites_path: Path, suite_name: str | None
) -> dict[str, Any]:
    loaded = read_json_object(
        suites_path,
        label="Suite catalog 'catalog/suites.json'",
        optional=True,
    )
    if loaded is not None:
        return loaded
    default_suite = suite_name or "repository-workflows"
    return {
        "schema_version": "1.0",
        "suites": [
            {
                "name": default_suite,
                "protocol_version": "1.0",
                "phases": [{"id": 0, "title": "Workflow", "skills": []}],
            }
        ],
    }


def _should_prune_skill_from_phase(
    suite_obj_name: object,
    phase_obj_id: object,
    target_suite_name: str,
    phase_id: int | None,
) -> bool:
    if suite_obj_name != target_suite_name:
        return True
    return phase_id is not None and phase_obj_id != phase_id


def _prune_prior_skill_phases(
    suites_list: Sequence[dict[str, Any]],
    name: str,
    target_suite_name: str,
    phase_id: int | None,
) -> None:
    for s_obj in suites_list:
        for p_obj in s_obj.get("phases", []):
            if name in p_obj.get("skills", []) and _should_prune_skill_from_phase(
                s_obj.get("name"), p_obj.get("id"), target_suite_name, phase_id
            ):
                p_obj["skills"] = [sk for sk in p_obj["skills"] if sk != name]


def _resolve_target_suite_phase(
    target_suite: dict[str, Any], target_suite_name: str, phase_id: int | None
) -> dict[str, Any]:
    phases = target_suite.get("phases", [])
    if not phases:
        raise ValueError(f"Suite {target_suite_name!r} has no phases configured")
    if phase_id is None:
        return phases[-1]
    target_phase = next((p for p in phases if p.get("id") == phase_id), None)
    if target_phase is None:
        valid_ids = [str(p.get("id")) for p in phases]
        raise ValueError(
            f"Unknown phase id {phase_id} in suite {target_suite_name!r} "
            f"(available: {', '.join(valid_ids)})"
        )
    return target_phase


def _register_skill_in_suites_doc(
    suites_path: Path,
    name: str,
    suite_name: str | None,
    phase_id: int | None,
) -> str:
    suites_doc = _load_or_default_suites_doc(suites_path, suite_name)
    suites_list = suites_doc.get("suites", [])
    available_names = [s["name"] for s in suites_list if isinstance(s.get("name"), str)]
    target_suite_name = suite_name or infer_suite_name(name, available_names)

    target_suite = next(
        (s for s in suites_list if s.get("name") == target_suite_name),
        None,
    )
    if target_suite is None:
        raise ValueError(
            f"Unknown suite {target_suite_name!r} in {suites_path.as_posix()} "
            f"(available: {', '.join(available_names)})"
        )

    _prune_prior_skill_phases(suites_list, name, target_suite_name, phase_id)
    target_phase = _resolve_target_suite_phase(
        target_suite, target_suite_name, phase_id
    )
    if name not in target_phase["skills"]:
        target_phase["skills"].append(name)

    suites_path.write_text(
        json.dumps(suites_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target_suite_name


def _update_experience_catalog(
    experience_path: Path,
    name: str,
    title: str,
    maturity: str,
    symptoms: Sequence[str],
) -> None:
    loaded_exp = read_json_object(
        experience_path,
        label="Experience catalog 'catalog/experience.json'",
        optional=True,
    )
    exp_doc: dict[str, Any] = (
        loaded_exp
        if loaded_exp is not None
        else {"schema_version": "1.0", "skills": {}}
    )

    skills_map = dict(exp_doc.get("skills", {}))
    skills_map[name] = {
        "maturity": maturity,
        "symptoms": list(symptoms),
        "expected_inputs": [
            "source snapshot",
            "task scope",
            "existing evidence if available",
        ],
        "outcomes": [f"Evidence-backed {title.lower()} deliverable"],
        "optional_tools": [],
        "behavior_evaluation": "See docs/validation.md; no universal host certification",
    }
    exp_doc["skills"] = {k: skills_map[k] for k in sorted(skills_map)}
    experience_path.write_text(
        json.dumps(exp_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _ensure_auxiliary_catalogs(catalog_dir: Path) -> list[Path]:
    created: list[Path] = []
    for aux_name, top_key in (
        ("recipes.json", "recipes"),
        ("packs.json", "packs"),
        ("hosts.json", "hosts"),
    ):
        aux_path = catalog_dir / aux_name
        if not aux_path.is_file():
            aux_path.write_text(
                json.dumps({"schema_version": "1.0", top_key: {}}, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            created.append(aux_path)
    return created


_SUITE_EVAL_TARGETS: dict[str, tuple[str, str]] = {
    "repository-audits": ("audit", "audit_target.py"),
    "software-architecture": ("architecture", "architecture_target.py"),
    "code-review": ("review", "review_target.py"),
    "automated-testing": ("testing", "testing_target.py"),
    "spec-driven-development": ("spec", "spec_target.py"),
    "repository-workflows": ("workflow", "workflow_target.py"),
}


def _scaffold_fixture_slug(name: str) -> str:
    slug = name.replace("-", "_")
    return (
        f"pkg_{slug}" if slug in {"class", "def", "import", "return", "from"} else slug
    )


def _write_scaffold_eval_fixtures(
    root: Path, name: str, title: str, target_filename: str
) -> list[Path]:
    lower_title = title.lower()
    base = root / "evals/fixtures/scaffolded" / _scaffold_fixture_slug(name)
    fix_a = base / "a" / target_filename
    fix_b = base / "b" / target_filename
    fix_a.parent.mkdir(parents=True, exist_ok=True)
    fix_b.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f'"""Synthetic {name} target ({lower_title})."""\n\n\n'
        "def apply_transition(record: dict[str, object], payload: dict[str, object]) -> None:\n"
    )
    fix_a.write_text(
        f'{header}    record["state"] = payload["target_state"]\n',
        encoding="utf-8",
        newline="\n",
    )
    fix_b.write_text(
        f"{header}"
        '    if "target_state" not in payload:\n'
        '        raise ValueError("missing target_state")\n'
        '    record["state"] = payload["target_state"]\n',
        encoding="utf-8",
        newline="\n",
    )
    return [fix_a, fix_b]


def _suite_eval_expectations(
    contract: str, name: str, target_filename: str
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    if contract == "architecture":
        return (
            [
                {
                    "kind": "boundary",
                    "type": "module",
                    "min_invariants": 1,
                    "paths": [target_filename],
                }
            ],
            {"status": "accepted", "min_invariants": 1},
            {"status": "accepted"},
            {"status": "draft"},
        )
    if contract == "review":
        return (
            [{"file": target_filename, "severity": "high", "line": 5}],
            {
                "verdict": "request_changes",
                "files": [target_filename],
                "require_recommendations": True,
            },
            {"verdict": "approve"},
            {"verdict": "comment"},
        )
    if contract == "testing":
        return (
            [{"oracle_type": "exception", "verified": False, "file": target_filename}],
            {"component": "testing_target", "framework": "unittest", "verified": False},
            {"component": "testing_target", "framework": "unittest"},
            {"component": "repository", "framework": "unittest"},
        )
    if contract == "spec":
        return (
            [{"kind": "interview_decision", "category": "edge-case"}],
            {"status": "interviewing", "new_file_scopes": [target_filename]},
            {"status": "approved", "new_file_scopes": [target_filename]},
            {"status": "draft"},
        )
    if contract == "workflow":
        return (
            [{"kind": "task", "file_scopes": [target_filename], "has_blockers": False}],
            {"acyclic": True},
            {"acyclic": True},
            {"acyclic": True},
        )
    return (
        [
            {
                "topic": name,
                "root_cause": "unchecked-state-transition",
                "locations": [[target_filename, "apply_transition"]],
            }
        ],
        None,
        None,
        None,
    )


def _make_eval_case_entry(
    case_id: str,
    fixture_rel: str,
    name: str,
    contract: str,
    prompt: str,
    anchors: list[dict[str, Any]],
    assertions: dict[str, Any] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": case_id,
        "fixture": fixture_rel,
        "skill": name,
    }
    if contract != "audit":
        entry["contract"] = contract
    if contract == "workflow":
        entry["schema"] = f"skills/{name}/assets/{name}.schema.json"
    entry["prompt"] = prompt
    entry["expected_anchors"] = anchors
    if assertions is not None:
        entry["expected_assertions"] = assertions
    return entry


def _build_starter_eval_cases(
    name: str, title: str, contract: str, target_filename: str, sibling_skill: str
) -> list[dict[str, Any]]:
    in_scope_prompt = f"Inspect the supplied {title.lower()} module and report concrete boundary issues."
    sibling_title = _default_title(sibling_skill).lower()
    routing_prompt = f"Run {sibling_title} checks on the supplied service interfaces."
    anchors_a, assert_a, assert_b, assert_r = _suite_eval_expectations(
        contract, name, target_filename
    )
    slug = _scaffold_fixture_slug(name)
    fix_a = f"fixtures/scaffolded/{slug}/a"
    fix_b = f"fixtures/scaffolded/{slug}/b"
    return [
        _make_eval_case_entry(
            f"{name}-a", fix_a, name, contract, in_scope_prompt, anchors_a, assert_a
        ),
        _make_eval_case_entry(
            f"{name}-b", fix_b, name, contract, in_scope_prompt, [], assert_b
        ),
        _make_eval_case_entry(
            f"{name}-routing", fix_a, name, contract, routing_prompt, [], assert_r
        ),
    ]


def _has_curated_eval_cases(cases: Sequence[Any], name: str) -> bool:
    existing = [c for c in cases if isinstance(c, dict) and c.get("skill") == name]
    if not existing:
        return False
    return any(
        not str(c.get("fixture", "")).startswith("fixtures/scaffolded/")
        for c in existing
    )


def _sync_eval_cases(
    root: Path,
    name: str,
    title: str,
    suite_name: str,
    sibling_skill: str,
) -> list[Path]:
    cases_path = root / "evals/cases.json"
    cases_doc = read_json_object(
        cases_path,
        label="Evaluation manifest 'evals/cases.json'",
        optional=True,
    )
    if cases_doc is None or not isinstance(cases_doc.get("cases"), list):
        return []
    raw_cases: list[Any] = cases_doc["cases"]
    if _has_curated_eval_cases(raw_cases, name):
        return []
    contract, target_filename = _SUITE_EVAL_TARGETS.get(
        suite_name, _SUITE_EVAL_TARGETS["repository-audits"]
    )
    fixture_paths = _write_scaffold_eval_fixtures(root, name, title, target_filename)
    starter_cases = _build_starter_eval_cases(
        name, title, contract, target_filename, sibling_skill
    )
    kept = [
        c for c in raw_cases if not (isinstance(c, dict) and c.get("skill") == name)
    ]
    cases_doc["cases"] = [*kept, *starter_cases]
    cases_path.write_text(
        json.dumps(cases_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return [*fixture_paths, cases_path]


def _sync_catalog_files(
    root: Path,
    name: str,
    title: str,
    suite_name: str | None,
    phase_id: int | None,
    maturity: str,
    symptoms: Sequence[str],
    sibling_skill: str,
) -> list[Path]:
    """Update suites.json, experience.json, shared resources, evals, and generated catalog indexes."""
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    suites_path = catalog_dir / "suites.json"
    target_suite_name = _register_skill_in_suites_doc(
        suites_path, name, suite_name, phase_id
    )
    experience_path = catalog_dir / "experience.json"
    _update_experience_catalog(experience_path, name, title, maturity, symptoms)

    touched: list[Path] = [
        suites_path,
        experience_path,
        *_ensure_auxiliary_catalogs(catalog_dir),
    ]
    packs_path = catalog_dir / "packs.json"
    if _register_skill_in_packs(packs_path, root, name, target_suite_name):
        touched.append(packs_path)

    if (root / "shared").is_dir():
        sync_audit_resources.sync(root=root, check=False)

    build_catalog.build(root=root, check=False)
    touched.extend(
        root / rel_out for rel_out in build_catalog.rendered_catalog(root=root)
    )

    build_experience.build(root=root, check=False)
    touched.extend(root / rel_out for rel_out in build_experience.outputs(root=root))

    touched.extend(
        _sync_eval_cases(root, name, title, target_suite_name, sibling_skill)
    )
    touched.extend(_sync_prose_count_docs(root))
    return touched


def _resolve_skills_parent(resolved_root: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return resolved_root / "skills"
    if output_dir.is_absolute():
        return output_dir.resolve()
    return (resolved_root / output_dir).resolve()


def _resolve_text_or_default(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


def _resolve_package_contract_links(
    own_schema_filename: str,
    synced_schemas: Sequence[str],
    synced_companions: Sequence[str],
) -> tuple[str, list[str], str, list[str], bool, bool]:
    synced_validators = [
        rel
        for rel in synced_companions
        if rel.startswith("scripts/validate_") and rel.endswith(".py")
    ]
    non_validators = [rel for rel in synced_companions if rel not in synced_validators]
    emit_own_schema = not synced_schemas
    primary_schema = (
        f"assets/{own_schema_filename}" if emit_own_schema else synced_schemas[0]
    )
    extra_schemas = [] if emit_own_schema else list(synced_schemas[1:])
    emit_own_validator = not synced_validators
    primary_validator = (
        "scripts/validate_output.py" if emit_own_validator else synced_validators[0]
    )
    extra_companions = [*non_validators, *synced_validators[1:]]
    return (
        primary_schema,
        extra_schemas,
        primary_validator,
        extra_companions,
        emit_own_schema,
        emit_own_validator,
    )


def _render_own_schema_text(
    name: str, title: str, suite_name: str, use_suite_companions: bool
) -> str:
    if use_suite_companions and suite_name == "repository-workflows":
        return _render_workflow_output_schema(name=name, title=title)
    return _render_output_schema(name=name, title=title)


def _build_scaffold_package_files(
    *,
    resolved_root: Path,
    name: str,
    effective_title: str,
    effective_description: str,
    effective_short: str,
    effective_suite: str,
    sibling_skill: str,
    use_suite_companions: bool = True,
) -> dict[str, str]:
    schema_filename = f"{name}.schema.json"
    synced_schemas, synced_companions = (
        _discover_suite_synced_companions(
            resolved_root, effective_suite, name, schema_filename
        )
        if use_suite_companions
        else ([], [])
    )
    (
        primary_schema,
        extra_schema_links,
        primary_validator,
        extra_companion_links,
        emit_own_schema,
        emit_own_validator,
    ) = _resolve_package_contract_links(
        schema_filename, synced_schemas, synced_companions
    )
    explicit_only = name in EXPLICIT_INVOCATION_ONLY_SKILLS
    example_suite = (
        effective_suite if (use_suite_companions and not emit_own_validator) else None
    )
    files: dict[str, str] = {
        "SKILL.md": _render_skill_markdown(
            name=name,
            title=effective_title,
            description=effective_description,
            primary_schema_rel=primary_schema,
            primary_validator_rel=primary_validator,
            extra_schema_links=extra_schema_links,
            extra_companion_links=extra_companion_links,
            disable_model_invocation=explicit_only,
        ),
        "assets/icon.svg": _render_icon_svg(name=name, suite_name=effective_suite),
        "assets/prompt.md": _render_prompt_markdown(
            name=name, primary_schema_rel=primary_schema
        ),
        "agents/openai.yaml": _render_openai_yaml(
            name=name,
            title=effective_title,
            short_description=effective_short,
            allow_implicit_invocation=not explicit_only,
        ),
        "references/evaluation-scenarios.json": _render_evaluation_scenarios(
            name=name,
            title=effective_title,
            suite_name=effective_suite,
            sibling_skill=sibling_skill,
        ),
        "references/examples.md": _render_examples_markdown(
            name=name,
            title=effective_title,
            primary_schema_rel=primary_schema,
            primary_validator_rel=primary_validator,
            suite_name=example_suite,
        ),
    }
    if emit_own_schema:
        files[f"assets/{schema_filename}"] = _render_own_schema_text(
            name, effective_title, effective_suite, use_suite_companions
        )
    if emit_own_validator:
        files["scripts/validate_output.py"] = _render_validator_script(
            schema_filename=schema_filename
        )
    return files


def _write_scaffolded_package_files(
    package_dir: Path, package_files: Mapping[str, str]
) -> None:
    for rel_path, content in package_files.items():
        target = package_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        if rel_path.startswith("scripts/"):
            target.chmod(0o755)


def _resolve_sync_modes(
    resolved_root: Path, skills_parent: Path, sync_catalog: bool | None
) -> tuple[bool, bool]:
    in_repo_skills = skills_parent == (resolved_root / "skills").resolve()
    if sync_catalog is True and not in_repo_skills:
        raise ValueError(
            "--catalog requires scaffolding inside <root>/skills so discovery paths resolve"
        )
    effective_sync = (
        sync_catalog
        if sync_catalog is not None
        else (in_repo_skills and (resolved_root / "catalog/suites.json").is_file())
    )
    use_companions = (
        sync_catalog is not False
        and in_repo_skills
        and (resolved_root / "shared").is_dir()
    )
    return effective_sync, use_companions


def _materialize_scaffold_outputs(
    *,
    resolved_root: Path,
    package_dir: Path,
    package_files: Mapping[str, str],
    effective_sync: bool,
    use_companions: bool,
    name: str,
    effective_title: str,
    explicit_suite: str | None,
    effective_suite: str,
    phase: int | None,
    maturity: str,
    effective_symptoms: Sequence[str],
    sibling_skill: str,
) -> list[Path]:
    _write_scaffolded_package_files(package_dir, package_files)
    if effective_sync:
        return _sync_catalog_files(
            root=resolved_root,
            name=name,
            title=effective_title,
            suite_name=explicit_suite,
            phase_id=phase,
            maturity=maturity,
            symptoms=effective_symptoms,
            sibling_skill=sibling_skill,
        )
    if use_companions:
        sync_audit_resources.sync_skill_package(
            resolved_root, package_dir, effective_suite, name, check=False
        )
    return []


def scaffold_skill(
    name: str,
    *,
    root: Path = ROOT,
    output_dir: Path | None = None,
    title: str | None = None,
    description: str | None = None,
    short_description: str | None = None,
    sync_catalog: bool | None = None,
    suite: str | None = None,
    phase: int | None = None,
    maturity: str = "experimental",
    symptoms: Sequence[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a compliant skill package and synchronize catalog indexes when applicable."""
    resolved_root = root.resolve()
    skills_parent = _resolve_skills_parent(resolved_root, output_dir)
    package_dir = skills_parent / name

    effective_suite, sibling_skill = _resolve_suite_and_sibling(
        root=resolved_root, name=name, explicit_suite=suite
    )
    effective_title = _resolve_text_or_default(title, _default_title(name))
    effective_description = _resolve_text_or_default(
        description, _default_description(name, effective_title, effective_suite)
    )
    effective_short = _resolve_text_or_default(
        short_description, _default_short_description(effective_title, name)
    )
    effective_symptoms = _normalize_catalog_symptoms(symptoms, name, effective_suite)

    _validate_inputs(
        name=name,
        description=effective_description,
        short_description=effective_short,
        maturity=maturity,
        root=resolved_root,
        package_dir=package_dir,
        force=force,
    )
    effective_sync, use_companions = _resolve_sync_modes(
        resolved_root, skills_parent, sync_catalog
    )

    package_files = _build_scaffold_package_files(
        resolved_root=resolved_root,
        name=name,
        effective_title=effective_title,
        effective_description=effective_description,
        effective_short=effective_short,
        effective_suite=effective_suite,
        sibling_skill=sibling_skill,
        use_suite_companions=use_companions,
    )
    planned_paths = [package_dir / rel for rel in sorted(package_files)]
    catalog_paths = (
        []
        if dry_run
        else _materialize_scaffold_outputs(
            resolved_root=resolved_root,
            package_dir=package_dir,
            package_files=package_files,
            effective_sync=effective_sync,
            use_companions=use_companions,
            name=name,
            effective_title=effective_title,
            explicit_suite=suite,
            effective_suite=effective_suite,
            phase=phase,
            maturity=maturity,
            effective_symptoms=effective_symptoms,
            sibling_skill=sibling_skill,
        )
    )

    return {
        "name": name,
        "title": effective_title,
        "package_dir": package_dir,
        "files": planned_paths,
        "catalog_synced": effective_sync,
        "catalog_files": catalog_paths,
        "dry_run": dry_run,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for scripts/scaffold_skill.py."""
    parser = argparse.ArgumentParser(
        description=(
            "Scaffold a standalone compliant skill package with SKILL.md, output schema, "
            "prompt template, OpenAI UI metadata, and evaluation scenarios."
        )
    )
    parser.add_argument(
        "name",
        help="Kebab-case skill identifier (e.g. repo-audit-rate-limits).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Human-readable skill title (defaults to title-cased skill name).",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Activation description for SKILL.md frontmatter (1-1024 chars).",
    )
    parser.add_argument(
        "--short-description",
        default=None,
        help="Short UI description for agents/openai.yaml (25-64 chars).",
    )
    catalog_group = parser.add_mutually_exclusive_group()
    catalog_group.add_argument(
        "--catalog",
        "--sync-catalog",
        dest="sync_catalog",
        action="store_const",
        const=True,
        default=None,
        help="Register the skill in catalog manifests, evaluations, and generated discovery indexes.",
    )
    catalog_group.add_argument(
        "--no-catalog",
        dest="sync_catalog",
        action="store_const",
        const=False,
        help="Skip catalog synchronization and emit an isolated standalone package.",
    )
    parser.add_argument(
        "--suite",
        default=None,
        help="Target suite name in catalog/suites.json when --catalog is enabled.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=None,
        help="Target phase integer ID within the suite when --catalog is enabled.",
    )
    parser.add_argument(
        "--maturity",
        choices=VALID_MATURITIES,
        default="experimental",
        help="Catalog maturity level when --catalog is enabled (default: experimental).",
    )
    parser.add_argument(
        "--symptom",
        dest="symptoms",
        action="append",
        default=None,
        help="Discovery symptom keyword for catalog/experience.json (repeatable).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Parent directory for the generated skill package (default: <root>/skills).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing skill package files if the target directory already exists.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Preview planned skill package paths without writing files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for scripts/scaffold_skill.py."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = scaffold_skill(
            args.name,
            root=args.root,
            output_dir=args.output_dir,
            title=args.title,
            description=args.description,
            short_description=args.short_description,
            sync_catalog=args.sync_catalog,
            suite=args.suite,
            phase=args.phase,
            maturity=args.maturity,
            symptoms=args.symptoms,
            force=args.force,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    pkg_dir: Path = result["package_dir"]
    try:
        display_pkg = pkg_dir.relative_to(args.root.resolve()).as_posix()
    except ValueError:
        display_pkg = pkg_dir.as_posix()

    if result["dry_run"]:
        for planned in result["files"]:
            try:
                rel = planned.relative_to(args.root.resolve()).as_posix()
            except ValueError:
                rel = planned.as_posix()
            print(f"[dry-run] {rel}")
        print(f"DRY-RUN: would scaffold skill '{args.name}' in {display_pkg}")
        return 0

    print(f"PASS: scaffolded skill '{args.name}' in {display_pkg}")
    if result["catalog_synced"]:
        print(
            f"PASS: synchronized suite, experience metadata, and discovery catalogs for '{args.name}'"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def _default_symptoms(name: str) -> list[str]:
    """Derive discovery symptom tags from a skill identifier."""
    ignored = {"repo", "audit", "skill", "workflow"}
    tokens = [tok for tok in name.split("-") if tok and tok not in ignored]
    return tokens[:4] if tokens else [name]


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
        if manifest is None:
            continue
        if manifest.get("suite", "repository-audits") != suite_name:
            continue
        raw_exclude = manifest.get("exclude", {})
        excluded = (
            set(raw_exclude.get(skill_name, []))
            if isinstance(raw_exclude, dict)
            and isinstance(raw_exclude.get(skill_name), list)
            else set()
        )
        for rel in manifest.get("files", []):
            if not isinstance(rel, str) or rel in excluded:
                continue
            if (
                rel.startswith("assets/")
                and rel.endswith(".schema.json")
                and Path(rel).name != own_schema_filename
            ):
                schemas.append(rel)
            elif rel.endswith(".md") and rel != "assets/prompt.md":
                companions.append(rel)
        scripts = manifest.get("scripts", [])
        if isinstance(scripts, Mapping):
            for s_path, s_targets in scripts.items():
                if (
                    isinstance(s_path, str)
                    and isinstance(s_targets, (list, tuple, set))
                    and skill_name in s_targets
                    and s_path not in excluded
                    and s_path.startswith("scripts/")
                    and s_path.endswith(".py")
                ):
                    companions.append(s_path)
        elif isinstance(scripts, list):
            for s_item in scripts:
                if isinstance(s_item, Mapping):
                    rel_s = s_item.get("path") or s_item.get("file")
                    targets = s_item.get("targets") or s_item.get("skills", [])
                    if (
                        isinstance(rel_s, str)
                        and isinstance(targets, (list, tuple, set))
                        and skill_name in targets
                        and rel_s not in excluded
                        and rel_s.startswith("scripts/")
                        and rel_s.endswith(".py")
                    ):
                        companions.append(rel_s)
                    continue
                if not isinstance(s_item, str) or s_item in excluded:
                    continue
                if (
                    s_item == "scripts/audit_to_sarif.py"
                    and skill_name != "repo-audit-synthesis"
                ):
                    continue
                if s_item.startswith("scripts/") and s_item.endswith(".py"):
                    companions.append(s_item)
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
    schema_filename: str,
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
        f"Format structured results conforming to the [output schema](assets/{schema_filename}){extra_clause}, "
        "and verify deliverables with [output validator](scripts/validate_output.py).\n"
        "Cite exact relative file paths, concrete evidence, and remaining verification limits.\n"
    )


def _render_prompt_markdown(name: str, schema_filename: str) -> str:
    return (
        f"Use {name} to inspect <repository> at <revision> within the requested scope.\n"
        "Ground claims in concrete file evidence and separate confirmed behavior from inference.\n"
        "Do not modify application code or publish external issues without explicit permission.\n"
        "Keep unrelated boundaries and existing invariants unchanged.\n"
        f"Emit structured output conforming to assets/{schema_filename}.\n"
    )


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


def _render_examples_markdown(name: str, title: str, schema_filename: str) -> str:
    sample_output = {
        "schema_version": "1.0",
        "skill": name,
        "summary": f"Inspected {title.lower()} boundaries across src/service/handler.py.",
        "findings": [
            {
                "id": "F-001",
                "title": f"Unchecked boundary precondition in {name} handler",
                "severity": "medium",
                "evidence": [
                    "src/service/handler.py:18-24 omits boundary precondition guard before state mutation."
                ],
            }
        ],
        "limitations": [
            "Static source inspection only; live integration harness was not executed."
        ],
    }
    rendered_json = json.dumps(sample_output, indent=2, ensure_ascii=False)
    return (
        f"# Worked Examples: {title}\n\n"
        f"Load these examples on demand when formatting deliverables for `assets/{schema_filename}` "
        "and running `scripts/validate_output.py`.\n\n"
        "## 1. Representative Input Context\n\n"
        "```python\n"
        "# src/service/handler.py\n"
        "def apply_transition(record: dict[str, object], payload: dict[str, object]) -> None:\n"
        '    record["state"] = payload["target_state"]  # mutates state without boundary guard\n'
        "```\n\n"
        f"## 2. Valid Deliverable Output (`assets/{schema_filename}`)\n\n"
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


def _validate_inputs(
    name: str,
    description: str,
    short_description: str,
    maturity: str,
    root: Path,
    package_dir: Path,
    force: bool,
) -> None:
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

    skills_root = root / "skills"
    if skills_root.is_dir():
        for existing_entry in sorted(skills_root.glob("*/SKILL.md")):
            if existing_entry.parent.resolve() == package_dir.resolve():
                continue
            meta, _ = _read_skill_frontmatter_strict(existing_entry)
            if meta.get("description") == description:
                raise ValueError(
                    f"Activation description duplicates existing skill '{existing_entry.parent.name}'"
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


_SUITE_PREFERRED_PACKS: dict[str, tuple[str, ...]] = {
    "software-architecture": ("software-architecture",),
    "code-review": ("code-review",),
    "automated-testing": ("automated-testing",),
    "spec-driven-development": ("spec-driven-development",),
    "repository-workflows": ("verified-fixes", "session-hygiene"),
    "repository-audits": (
        "release-readiness",
        "web-review",
        "backend-consistency",
        "async-correctness",
    ),
}


def _select_curated_pack_for_skill(
    packs: dict[str, Any], root: Path, name: str, suite_name: str
) -> str | None:
    curated = {
        k: v
        for k, v in packs.items()
        if k not in UMBRELLA_PACK_NAMES
        and isinstance(v, dict)
        and isinstance(v.get("skills"), list)
    }
    if not curated or any(name in v["skills"] for v in curated.values()):
        return None
    skill_cost = _pack_raw_chars(root, [name])
    preferred = [p for p in _SUITE_PREFERRED_PACKS.get(suite_name, ()) if p in curated]
    non_starter = [p for p in curated if p != "starter" and p not in preferred]
    ordered = [*preferred, *non_starter]
    for group in (ordered, ["starter"] if "starter" in curated else []):
        candidates = [
            (
                _pack_raw_chars(root, curated[p]["skills"]),
                idx,
                p,
            )
            for idx, p in enumerate(group)
            if _pack_raw_chars(root, curated[p]["skills"]) + skill_cost
            < MAX_CURATED_PACK_CHARS
        ]
        if candidates:
            return min(candidates)[2]
    return None


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


def _sync_umbrella_pack_descriptions(packs: dict[str, Any]) -> bool:
    changed = False
    full_audit = packs.get("full-audit")
    if isinstance(full_audit, dict) and isinstance(full_audit.get("skills"), list):
        specialists = sum(
            1
            for sk in full_audit["skills"]
            if isinstance(sk, str) and sk not in _AUDIT_CORE_SKILLS
        )
        old_desc = full_audit.get("description")
        if isinstance(old_desc, str):
            new_desc = _FULL_AUDIT_DESC_COUNT_RE.sub(
                rf"\g<1>{specialists}\g<2>", old_desc
            )
            if new_desc != old_desc:
                full_audit["description"] = new_desc
                changed = True

    complete = packs.get("complete")
    if isinstance(complete, dict) and isinstance(complete.get("skills"), list):
        total = sum(1 for sk in complete["skills"] if isinstance(sk, str))
        old_desc = complete.get("description")
        if isinstance(old_desc, str):
            new_desc = _COMPLETE_DESC_COUNT_RE.sub(rf"\g<1>{total}\g<2>", old_desc)
            if new_desc != old_desc:
                complete["description"] = new_desc
                changed = True
    return changed


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
    changed = False
    chosen = _select_curated_pack_for_skill(packs, root, name, suite_name)
    if chosen is not None:
        changed = _append_pack_skill(packs[chosen], name) or changed
    if "complete" in packs and isinstance(packs["complete"], dict):
        changed = _append_pack_skill(packs["complete"], name) or changed
    if "full-audit" in packs and isinstance(packs["full-audit"], dict):
        fa_skills = packs["full-audit"].get("skills")
        if suite_name == "repository-audits":
            was_sorted = isinstance(fa_skills, list) and fa_skills == sorted(fa_skills)
            if _append_pack_skill(packs["full-audit"], name):
                if was_sorted and isinstance(fa_skills, list):
                    fa_skills.sort()
                changed = True
        elif isinstance(fa_skills, list) and name in fa_skills:
            packs["full-audit"]["skills"] = [sk for sk in fa_skills if sk != name]
            changed = True
    changed = _sync_umbrella_pack_descriptions(packs) or changed
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


def _rewrite_prose_counts_in_text(
    text: str,
    rules: Sequence[tuple[re.Pattern[str], int | None]],
    *,
    max_end: int | None = None,
) -> str:
    limit = len(text) if max_end is None else min(len(text), max_end)
    edits: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for pattern, expected in rules:
        if expected is None:
            continue
        for match in pattern.finditer(text, 0, limit):
            raw_tok = match.group(1)
            found = parse_prose_int(raw_tok)
            if found is None or found == expected:
                continue
            start, end = match.span(1)
            if any(not (end <= s or start >= e) for s, e in occupied):
                continue
            occupied.append((start, end))
            edits.append((start, end, _format_prose_count_token(raw_tok, expected)))
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


def _sync_catalog_files(
    root: Path,
    name: str,
    title: str,
    suite_name: str | None,
    phase_id: int | None,
    maturity: str,
    symptoms: Sequence[str],
) -> list[Path]:
    """Update suites.json, experience.json, shared resources, and generated catalog indexes."""
    touched: list[Path] = []
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    suites_path = catalog_dir / "suites.json"
    loaded_suites = read_json_object(
        suites_path,
        label="Suite catalog 'catalog/suites.json'",
        optional=True,
    )
    if loaded_suites is not None:
        suites_doc = loaded_suites
    else:
        default_suite = suite_name or "repository-workflows"
        suites_doc = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": default_suite,
                    "protocol_version": "1.0",
                    "phases": [{"id": 0, "title": "Workflow", "skills": []}],
                }
            ],
        }

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

    # Remove from any prior phase if re-running with --force, then add to target phase
    for s_obj in suites_list:
        for p_obj in s_obj.get("phases", []):
            if name in p_obj.get("skills", []) and (
                s_obj.get("name") != target_suite_name
                or (phase_id is not None and p_obj.get("id") != phase_id)
            ):
                p_obj["skills"] = [sk for sk in p_obj["skills"] if sk != name]

    phases = target_suite.get("phases", [])
    if not phases:
        raise ValueError(f"Suite {target_suite_name!r} has no phases configured")

    if phase_id is not None:
        target_phase = next((p for p in phases if p.get("id") == phase_id), None)
        if target_phase is None:
            valid_ids = [str(p.get("id")) for p in phases]
            raise ValueError(
                f"Unknown phase id {phase_id} in suite {target_suite_name!r} "
                f"(available: {', '.join(valid_ids)})"
            )
    else:
        target_phase = phases[-1]

    if name not in target_phase["skills"]:
        target_phase["skills"].append(name)

    suites_path.write_text(
        json.dumps(suites_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    touched.append(suites_path)

    experience_path = catalog_dir / "experience.json"
    loaded_exp = read_json_object(
        experience_path,
        label="Experience catalog 'catalog/experience.json'",
        optional=True,
    )
    if loaded_exp is not None:
        exp_doc = loaded_exp
    else:
        exp_doc = {"schema_version": "1.0", "skills": {}}

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
    touched.append(experience_path)

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
            touched.append(aux_path)

    packs_path = catalog_dir / "packs.json"
    if _register_skill_in_packs(packs_path, root, name, target_suite_name):
        touched.append(packs_path)

    if (root / "shared").is_dir():
        sync_audit_resources.sync(root=root, check=False)

    build_catalog.build(root=root, check=False)
    for rel_out in build_catalog.rendered_catalog(root=root):
        touched.append(root / rel_out)

    build_experience.build(root=root, check=False)
    for rel_out in build_experience.outputs(root=root):
        touched.append(root / rel_out)

    touched.extend(_sync_prose_count_docs(root))

    return touched


def scaffold_skill(
    name: str,
    *,
    root: Path = ROOT,
    output_dir: Path | None = None,
    title: str | None = None,
    description: str | None = None,
    short_description: str | None = None,
    sync_catalog: bool = False,
    suite: str | None = None,
    phase: int | None = None,
    maturity: str = "experimental",
    symptoms: Sequence[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a compliant skill package and optionally synchronize catalog indexes."""
    resolved_root = root.resolve()
    skills_parent = (
        output_dir.resolve()
        if output_dir is not None and output_dir.is_absolute()
        else (resolved_root / output_dir).resolve()
        if output_dir is not None
        else resolved_root / "skills"
    )
    package_dir = skills_parent / name

    effective_suite, sibling_skill = _resolve_suite_and_sibling(
        root=resolved_root,
        name=name,
        explicit_suite=suite,
    )
    effective_title = title.strip() if title and title.strip() else _default_title(name)
    effective_description = (
        description.strip()
        if description and description.strip()
        else _default_description(name, effective_title, effective_suite)
    )
    effective_short = (
        short_description.strip()
        if short_description and short_description.strip()
        else _default_short_description(effective_title, name)
    )
    effective_symptoms = (
        [s.strip() for s in symptoms if s.strip()]
        if symptoms
        else _default_symptoms(name)
    )

    _validate_inputs(
        name=name,
        description=effective_description,
        short_description=effective_short,
        maturity=maturity,
        root=resolved_root,
        package_dir=package_dir,
        force=force,
    )

    if sync_catalog and skills_parent != (resolved_root / "skills"):
        raise ValueError(
            "--catalog requires scaffolding inside <root>/skills so discovery paths resolve"
        )

    schema_filename = f"{name}.schema.json"
    extra_schema_links, extra_companion_links = _discover_suite_synced_companions(
        resolved_root, effective_suite, name, schema_filename
    )
    explicit_only = name in EXPLICIT_INVOCATION_ONLY_SKILLS
    package_files: dict[str, str] = {
        "SKILL.md": _render_skill_markdown(
            name=name,
            title=effective_title,
            description=effective_description,
            schema_filename=schema_filename,
            extra_schema_links=extra_schema_links,
            extra_companion_links=extra_companion_links,
            disable_model_invocation=explicit_only,
        ),
        "assets/icon.svg": _render_icon_svg(
            name=name,
            suite_name=effective_suite,
        ),
        "assets/prompt.md": _render_prompt_markdown(
            name=name,
            schema_filename=schema_filename,
        ),
        f"assets/{schema_filename}": _render_output_schema(
            name=name,
            title=effective_title,
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
            schema_filename=schema_filename,
        ),
        "scripts/validate_output.py": _render_validator_script(
            schema_filename=schema_filename,
        ),
    }

    planned_paths = [package_dir / rel for rel in sorted(package_files)]
    catalog_paths: list[Path] = []

    if not dry_run:
        for rel_path, content in package_files.items():
            target = package_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            if rel_path.startswith("scripts/"):
                target.chmod(0o755)

        if sync_catalog:
            catalog_paths = _sync_catalog_files(
                root=resolved_root,
                name=name,
                title=effective_title,
                suite_name=suite,
                phase_id=phase,
                maturity=maturity,
                symptoms=effective_symptoms,
            )
        elif (resolved_root / "shared").is_dir():
            sync_audit_resources.sync_skill_package(
                resolved_root,
                package_dir,
                effective_suite,
                name,
                check=False,
            )

    return {
        "name": name,
        "title": effective_title,
        "package_dir": package_dir,
        "files": planned_paths,
        "catalog_synced": sync_catalog,
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
    parser.add_argument(
        "--catalog",
        "--sync-catalog",
        dest="sync_catalog",
        action="store_true",
        help="Also register the skill in catalog/suites.json and catalog/experience.json and rebuild discovery indexes.",
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

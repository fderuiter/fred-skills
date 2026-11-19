#!/usr/bin/env python3
"""Scaffold a standalone compliant skill package with optional catalog sync."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_catalog
import build_experience
import sync_audit_resources
from skill_tools import ROOT, read_frontmatter
from validate_skills import NAME

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


def _default_description(name: str, title: str) -> str:
    """Construct a concise, unique activation description within 1..1024 characters."""
    desc = (
        f"Use when executing {title.lower()} workflows or inspecting {name} "
        "contracts and boundaries on explicit request."
    )
    return desc[:1024]


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


def _infer_suite_name(name: str, available_suites: Sequence[str]) -> str:
    """Choose a sensible suite name based on skill prefix and available suites."""
    available = set(available_suites)
    prefix_map = (
        ("repo-audit-", "repository-audits"),
        ("arch-", "software-architecture"),
        ("review-", "code-review"),
        ("test-", "automated-testing"),
        ("repo-", "repository-workflows"),
    )
    for prefix, suite_name in prefix_map:
        if name.startswith(prefix) and (not available or suite_name in available):
            return suite_name
    if "repository-workflows" in available:
        return "repository-workflows"
    if available_suites:
        return available_suites[0]
    return "repository-audits"


def _resolve_suite_and_sibling(
    root: Path,
    name: str,
    explicit_suite: str | None,
) -> tuple[str, str]:
    """Resolve the target suite name and a valid near-neighbor sibling_skill."""
    suites_path = root / "catalog/suites.json"
    available_names: list[str] = []
    suite_skills: dict[str, list[str]] = {}
    if suites_path.is_file():
        try:
            doc = json.loads(suites_path.read_text(encoding="utf-8"))
            for s_obj in doc.get("suites", []):
                s_name = s_obj.get("name")
                if not isinstance(s_name, str):
                    continue
                available_names.append(s_name)
                members: list[str] = []
                for p_obj in s_obj.get("phases", []):
                    for sk in p_obj.get("skills", []):
                        if isinstance(sk, str):
                            members.append(sk)
                suite_skills[s_name] = members
        except (OSError, ValueError, TypeError):
            pass

    target_suite = explicit_suite or _infer_suite_name(name, available_names)
    for candidate in suite_skills.get(target_suite, []):
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


def _discover_suite_synced_schemas(
    root: Path,
    suite_name: str,
    own_schema_filename: str,
) -> list[str]:
    """Return relative assets/*.schema.json paths synced into suite_name by shared manifests."""
    shared_dir = root / "shared"
    if not shared_dir.is_dir():
        return []
    synced: list[str] = []
    for manifest_path in sorted(shared_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("suite", "repository-audits") != suite_name:
            continue
        for rel in manifest.get("files", []):
            if (
                isinstance(rel, str)
                and rel.startswith("assets/")
                and rel.endswith(".schema.json")
                and Path(rel).name != own_schema_filename
            ):
                synced.append(rel)
    return sorted(set(synced))


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
) -> str:
    extra_clause = ""
    if extra_schema_links:
        formatted = ", ".join(f"[suite schema]({rel})" for rel in extra_schema_links)
        extra_clause = f" and {formatted}"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {_yaml_double_quoted(description)}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"Review the [starting prompt](assets/prompt.md) and "
        f"[evaluation scenarios](references/evaluation-scenarios.json) when validating scope.\n"
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
        f"Format structured results conforming to the [output schema](assets/{schema_filename}){extra_clause}.\n"
        "Cite exact relative file paths, Concrete evidence, and remaining verification limits.\n"
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


def _render_openai_yaml(name: str, title: str, short_description: str) -> str:
    default_prompt = (
        f"Use ${name} on this repository and report evidence-backed results."
    )
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
        "  allow_implicit_invocation: true\n"
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

    packs_path = root / "catalog/packs.json"
    if packs_path.is_file():
        packs = json.loads(packs_path.read_text(encoding="utf-8")).get("packs", {})
        if name in packs:
            raise ValueError(
                f"Skill name {name!r} collides with existing pack name in catalog/packs.json"
            )

    if not 1 <= len(description.strip()) <= 1024:
        raise ValueError(
            "Description must be a nonempty string of at most 1024 characters"
        )

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
            try:
                meta, _ = read_frontmatter(existing_entry)
            except (OSError, ValueError, TypeError):
                continue
            if meta.get("description") == description:
                raise ValueError(
                    f"Activation description duplicates existing skill '{existing_entry.parent.name}'"
                )


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
    if suites_path.is_file():
        suites_doc = json.loads(suites_path.read_text(encoding="utf-8"))
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
    target_suite_name = suite_name or _infer_suite_name(name, available_names)

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
    if experience_path.is_file():
        exp_doc = json.loads(experience_path.read_text(encoding="utf-8"))
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

    if (root / "shared").is_dir():
        sync_audit_resources.sync(root=root, check=False)

    build_catalog.build(root=root, check=False)
    for rel_out in build_catalog.rendered_catalog(root=root):
        touched.append(root / rel_out)

    build_experience.build(root=root, check=False)
    for rel_out in build_experience.outputs(root=root):
        touched.append(root / rel_out)

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

    effective_title = title.strip() if title and title.strip() else _default_title(name)
    effective_description = (
        description.strip()
        if description and description.strip()
        else _default_description(name, effective_title)
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
    effective_suite, sibling_skill = _resolve_suite_and_sibling(
        root=resolved_root,
        name=name,
        explicit_suite=suite,
    )
    extra_schema_links = (
        _discover_suite_synced_schemas(resolved_root, effective_suite, schema_filename)
        if sync_catalog
        else []
    )
    package_files: dict[str, str] = {
        "SKILL.md": _render_skill_markdown(
            name=name,
            title=effective_title,
            description=effective_description,
            schema_filename=schema_filename,
            extra_schema_links=extra_schema_links,
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
        ),
        "references/evaluation-scenarios.json": _render_evaluation_scenarios(
            name=name,
            title=effective_title,
            suite_name=effective_suite,
            sibling_skill=sibling_skill,
        ),
    }

    planned_paths = [package_dir / rel for rel in sorted(package_files)]
    catalog_paths: list[Path] = []

    if not dry_run:
        for rel_path, content in package_files.items():
            target = package_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

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

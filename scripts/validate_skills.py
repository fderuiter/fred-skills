"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from skill_tools import (
    ROOT,
    extract_relative_links,
    read_frontmatter,
    resolve_relative_link_target,
    suite_members,
)

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_PROMPT_CHARS = 1200
MAX_PROMPT_LINES = 20
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
    r"\b(do not|never|must not|cannot|should not|prohibit\w*|forbid\w*|avoid\w*|disallow\w*|"
    r"only\s+(?:if|when|after|under|with)|keep\s+[^.\n]+unchanged|preserve\s+[^.\n]+unchanged|"
    r"without\s+[^.\n]+(?:authority|permission|prior|assuming)|honor\s+[^.\n]+scope)\b",
    re.IGNORECASE,
)
BOUNDARY_GUARDRAIL_PATTERN = re.compile(
    r"\b(boundar\w*|guardrail\w*|invariant\w*|constraint\w*|safe control\w*|"
    r"isolation\w*|barrier\w*|safeguard\w*|precondition\w*|mutation scope|blast radius|"
    r"acceptance criteria)\b",
    re.IGNORECASE,
)
BOUNDARY_SECTION_PATTERN = re.compile(
    r"^##+\s+.*(boundar\w*|guardrail\w*|deliver\w*|workflow\w*|inventory\w*|investigat\w*|challenge\w*|triage\w*|establish\w*|coordinate\w*|validate\w*)",
    re.IGNORECASE | re.MULTILINE,
)

_SCENARIO_SCHEMA: dict | None = None


def get_scenario_schema(schema_path: Path | None = None) -> dict | None:
    global _SCENARIO_SCHEMA
    if schema_path is not None:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    if _SCENARIO_SCHEMA is None:
        default_path = ROOT / "schemas/evaluation-scenarios.schema.json"
        if default_path.is_file():
            _SCENARIO_SCHEMA = json.loads(default_path.read_text(encoding="utf-8"))
    return _SCENARIO_SCHEMA


def validate_skill(package: Path, scenario_schema: dict | None = None) -> list[str]:
    errors = []
    try:
        metadata, body = read_frontmatter(package / "SKILL.md")
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
    if not body.strip() or len(body.splitlines()) >= 500:
        errors.append("Skill body must be nonempty and under 500 lines")
    if not BOUNDARY_SECTION_PATTERN.search(body):
        errors.append(
            "SKILL.md: skill markdown lacks boundary sections (e.g. ## Deliver, ## Workflow, ## Boundary, ## Guardrails)"
        )
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
            if not (
                NEGATIVE_CONSTRAINT_PATTERN.search(prompt_content)
                or BOUNDARY_GUARDRAIL_PATTERN.search(prompt_content)
            ):
                errors.append(
                    f"{prompt_rel}: prompt template lacks negative constraints or boundary guardrails"
                )
        except OSError as exc:
            errors.append(f"{prompt_rel}: {exc}")
    output_schemas = [
        p
        for p in package.glob("assets/*.schema.json")
        if not p.name.startswith("evaluation-scenarios")
    ]
    if not output_schemas:
        output_schemas = [
            p
            for p in package.glob("*.schema.json")
            if not p.name.startswith("evaluation-scenarios")
        ]
    if not output_schemas:
        errors.append(
            f"Missing output schema: {package.name} must define an output schema (*.schema.json in assets/)"
        )
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
        except (OSError, ValueError):
            pass  # Malformed JSON or read error is caught during package file traversal below
    for path in package.rglob("*"):
        if path.is_symlink():
            errors.append(
                f"Symlink breaks standalone packaging: {path.relative_to(package).as_posix()}"
            )
            continue
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            errors.append(
                f"Generated Python cache in package: {path.relative_to(package).as_posix()}"
            )
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                if path.name.endswith(".schema.json"):
                    jsonschema.Draft202012Validator.check_schema(data)
            if path.suffix == ".md":
                for target in extract_relative_links(path.read_text(encoding="utf-8")):
                    resolved = resolve_relative_link_target(target, path.parent)
                    if resolved is None:
                        continue
                    if not resolved.is_relative_to(package.resolve()):
                        errors.append(f"Reference escapes standalone package: {target}")
                    elif not resolved.is_file():
                        errors.append(f"Missing relative reference: {target}")
        except (ValueError, OSError, jsonschema.SchemaError) as exc:
            errors.append(f"{path.relative_to(package).as_posix()}: {exc}")
    ui_path = package / "agents/openai.yaml"
    if ui_path.exists():
        try:
            ui = yaml.safe_load(ui_path.read_text(encoding="utf-8"))["interface"]
            if (
                not isinstance(ui.get("display_name"), str)
                or not ui["display_name"].strip()
            ):
                errors.append("Missing display name")
            short = ui.get("short_description")
            if not isinstance(short, str) or not 25 <= len(short) <= 64:
                errors.append("UI short_description must contain 25–64 characters")
            prompt = ui.get("default_prompt")
            if not isinstance(prompt, str) or f"${name}" not in prompt:
                errors.append("UI default_prompt must name the skill")
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

        output_schemas = [
            p.relative_to(package).as_posix()
            for p in package.glob("assets/*.schema.json")
            if not p.name.startswith("evaluation-scenarios")
        ]
        if not output_schemas:
            output_schemas = [
                p.relative_to(package).as_posix()
                for p in package.glob("*.schema.json")
                if not p.name.startswith("evaluation-scenarios")
            ]
        has_schema = len(output_schemas) > 0

        prompt_path = package / "assets/prompt.md"
        prompt_text = (
            prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
        )
        has_prompt_constraints = bool(
            NEGATIVE_CONSTRAINT_PATTERN.search(prompt_text)
            or BOUNDARY_GUARDRAIL_PATTERN.search(prompt_text)
        )

        skill_path = package / "SKILL.md"
        skill_text = (
            skill_path.read_text(encoding="utf-8") if skill_path.is_file() else ""
        )
        has_boundary_sections = bool(BOUNDARY_SECTION_PATTERN.search(skill_text))

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

    if record_attempts and ledger_dirty:
        _save_attempt_ledger(effective_ledger_path, ledger_entries)

    try:
        if (root / "catalog/suites.json").is_file():
            for name in suite_members(root):
                if not (root / "skills" / name / "SKILL.md").is_file():
                    problems.append(f"Suite references absent skill: {name}")
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"Invalid suite manifest: {exc}")

    total_pkgs = len(packages)
    passed_pkgs = sum(1 for p in package_telemetry if p["valid"])
    failed_pkgs = total_pkgs - passed_pkgs
    covered_pkgs = sum(
        1 for p in package_telemetry if p["deterministic_constraint_coverage"]
    )
    schema_pkgs = sum(1 for p in package_telemetry if p["has_output_schema"])

    repo_pass_rate = (passed_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    repo_pass_rate_pct = round(repo_pass_rate * 100.0, 2)
    constraint_cov = (covered_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    constraint_cov_pct = round(constraint_cov * 100.0, 2)
    schema_cov = (schema_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    schema_cov_pct = round(schema_cov * 100.0, 2)

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
    print(f"  Execution Time: {telemetry['execution_time_seconds']}s")
    print("=" * 60)

    if not errors:
        print("PASS: skill metadata, schemas, and standalone references")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

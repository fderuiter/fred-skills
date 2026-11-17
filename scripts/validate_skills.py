"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import jsonschema
import yaml
from skill_tools import ROOT, extract_relative_links, read_frontmatter, suite_members

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
                    parsed = urlsplit(target)
                    raw_path = unquote(parsed.path)
                    clean_path = raw_path.split("#")[0].split("?")[0]
                    if not clean_path:
                        continue
                    resolved = (path.parent / clean_path).resolve()
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


def validate(root: Path = ROOT) -> list[str]:
    problems = []
    descriptions = {}
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
    packages = sorted(path for path in (root / "skills").iterdir() if path.is_dir())
    if not packages:
        problems.append("No skill packages found")
    for package in packages:
        problems.extend(
            f"{package.name}: {error}"
            for error in validate_skill(package, scenario_schema=scenario_schema)
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
    try:
        for name in suite_members(root):
            if not (root / "skills" / name / "SKILL.md").is_file():
                problems.append(f"Suite references absent skill: {name}")
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"Invalid suite manifest: {exc}")
    return problems


if __name__ == "__main__":
    errors = validate()
    for error in errors:
        print(error)
    if not errors:
        print("PASS: skill metadata, schemas, and standalone references")
    raise SystemExit(bool(errors))

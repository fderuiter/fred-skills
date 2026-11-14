"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import jsonschema
import yaml
from skill_tools import ROOT, read_frontmatter, suite_members

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LINK = re.compile(r"\]\(([^)]+)\)")


def validate_skill(package: Path) -> list[str]:
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
    for path in package.rglob("*"):
        if path.is_symlink():
            errors.append(
                f"Symlink breaks standalone packaging: {path.relative_to(package)}"
            )
            continue
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            errors.append(
                f"Generated Python cache in package: {path.relative_to(package)}"
            )
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                if path.name.endswith(".schema.json"):
                    jsonschema.Draft202012Validator.check_schema(data)
            if path.suffix == ".md":
                for target in LINK.findall(path.read_text(encoding="utf-8")):
                    parsed = urlsplit(target)
                    if parsed.scheme or parsed.netloc or not parsed.path:
                        continue
                    resolved = (path.parent / unquote(parsed.path)).resolve()
                    if not resolved.is_relative_to(package.resolve()):
                        errors.append(f"Reference escapes standalone package: {target}")
                    elif not resolved.is_file():
                        errors.append(f"Missing relative reference: {target}")
        except (ValueError, OSError, jsonschema.SchemaError) as exc:
            errors.append(f"{path.relative_to(package)}: {exc}")
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
    packages = sorted(path for path in (root / "skills").iterdir() if path.is_dir())
    if not packages:
        problems.append("No skill packages found")
    for package in packages:
        problems.extend(f"{package.name}: {error}" for error in validate_skill(package))
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

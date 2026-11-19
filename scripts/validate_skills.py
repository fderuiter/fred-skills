"""Validate installable skill structure and package-local resource references."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

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

    assets_schemas, output_schemas = _discover_output_schemas(package)
    if not output_schemas:
        errors.append(
            f"Missing output schema: {package.name} must define an output schema (*.schema.json in assets/)"
        )
    else:
        resolved_skill_links = {
            resolved
            for target in extract_relative_links(body)
            if (resolved := resolve_relative_link_target(target, package)) is not None
        }
        for schema_file in assets_schemas:
            if schema_file.resolve() not in resolved_skill_links:
                errors.append(
                    f"SKILL.md: missing markdown link to output schema "
                    f"{schema_file.relative_to(package).as_posix()}"
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


def validate_with_telemetry(root: Path = ROOT) -> tuple[list[str], dict]:
    start_time = time.perf_counter()
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

    packages = sorted(path for path in (root / "skills").iterdir() if path.is_dir())
    if not packages:
        problems.append("No skill packages found")

    package_telemetry = []
    for package in packages:
        pkg_errors = validate_skill(package, scenario_schema=scenario_schema)
        problems.extend(f"{package.name}: {error}" for error in pkg_errors)

        assets_schema_paths, output_schema_paths = _discover_output_schemas(package)
        output_schemas = [
            p.relative_to(package).as_posix() for p in output_schema_paths
        ]

        skill_body = ""
        skill_path = package / "SKILL.md"
        if skill_path.is_file():
            try:
                _, skill_body = read_frontmatter(skill_path)
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                skill_body = ""

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

        package_telemetry.append(
            {
                "name": package.name,
                "valid": pkg_valid,
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

    try:
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

    pass_rate = (passed_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    pass_rate_pct = round(pass_rate * 100.0, 2)
    constraint_cov = (covered_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    constraint_cov_pct = round(constraint_cov * 100.0, 2)
    schema_cov = (schema_pkgs / total_pkgs) if total_pkgs > 0 else 0.0
    schema_cov_pct = round(schema_cov * 100.0, 2)

    duration = round(time.perf_counter() - start_time, 4)

    telemetry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_packages": total_pkgs,
        "passed_packages": passed_pkgs,
        "failed_packages": failed_pkgs,
        "first_pass_guardrail_pass_rate": round(pass_rate, 4),
        "first_pass_guardrail_pass_rate_percentage": pass_rate_pct,
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
        "--root",
        type=Path,
        default=ROOT,
        help="Root directory of the skill repository",
    )
    args = parser.parse_args(argv)

    errors, telemetry = validate_with_telemetry(root=args.root)
    for error in errors:
        print(error)

    telemetry_path = args.export_telemetry or args.telemetry
    if telemetry_path:
        out_file = Path(telemetry_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(telemetry, indent=2), encoding="utf-8")
        print(f"Exported validation telemetry to {out_file.as_posix()}")

    print("=" * 60)
    print("Validation Telemetry:")
    print(f"  Total Packages: {telemetry['total_packages']}")
    print(f"  Passed Packages: {telemetry['passed_packages']}")
    print(f"  Failed Packages: {telemetry['failed_packages']}")
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

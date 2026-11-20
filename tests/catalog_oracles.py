"""Shared manifest and CLI oracle helpers for test suites."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from skill_tools import (
    read_json_object,
    read_suites_catalog,
    recipe_to_suite_map,
    suite_skills_by_suite,
)

_AUDIT_FRAMEWORK_SKILLS = frozenset(
    {
        "repo-audit-program",
        "repo-audit-diagnostics",
        "repo-audit-synthesis",
    }
)


def suites_catalog(root: Path = ROOT) -> list[dict[str, Any]]:
    """Return all suite entries registered in catalog/suites.json."""
    suites = read_suites_catalog(root)
    return suites if suites is not None else []


def expected_suite_count(root: Path = ROOT) -> int:
    """Return total number of suites registered in catalog/suites.json."""
    return len(suites_catalog(root))


def expected_suite_names(root: Path = ROOT) -> set[str]:
    """Return the set of suite names registered in catalog/suites.json."""
    return {
        suite["name"]
        for suite in suites_catalog(root)
        if isinstance(suite.get("name"), str)
    }


def suite_skill_names(
    root: Path = ROOT,
    *,
    suite_name: str | None = None,
) -> list[str]:
    """Return ordered deduplicated skill names across suites or for a single suite."""
    grouped = suite_skills_by_suite(root) or {}
    if suite_name is not None:
        return list(grouped.get(suite_name, ()))
    ordered: list[str] = []
    seen: set[str] = set()
    for members in grouped.values():
        for skill in members:
            if skill not in seen:
                seen.add(skill)
                ordered.append(skill)
    return ordered


def expected_total_skills(root: Path = ROOT) -> int:
    """Return total unique skills registered across catalog/suites.json."""
    return len(suite_skill_names(root))


def expected_suite_skill_count(suite_name: str, root: Path = ROOT) -> int:
    """Return total unique skills registered under a named suite."""
    return len(suite_skill_names(root, suite_name=suite_name))


def expected_non_audit_skill_count(root: Path = ROOT) -> int:
    """Return count of cataloged skills outside the repository-audits suite."""
    grouped = suite_skills_by_suite(root) or {}
    return sum(
        len(members) for name, members in grouped.items() if name != "repository-audits"
    )


def expected_audit_domain_count(root: Path = ROOT) -> int:
    """Return specialist domain count in repository-audits excluding coordinator/diagnostics/synthesis."""
    audit_members = suite_skill_names(root, suite_name="repository-audits")
    return sum(1 for skill in audit_members if skill not in _AUDIT_FRAMEWORK_SKILLS)


def packs_map(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Return the packs mapping from catalog/packs.json."""
    doc = read_json_object(root / "catalog/packs.json", label="Packs catalog")
    packs = doc.get("packs")
    return packs if isinstance(packs, dict) else {}


def expected_pack_count(root: Path = ROOT) -> int:
    """Return total number of curated and umbrella packs in catalog/packs.json."""
    return len(packs_map(root))


def expected_pack_skill_count(pack_name: str, root: Path = ROOT) -> int:
    """Return number of skills in a named pack from catalog/packs.json."""
    pack = packs_map(root).get(pack_name, {})
    skills = pack.get("skills", [])
    return len(skills) if isinstance(skills, list) else 0


def hosts_map(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Return the hosts mapping from catalog/hosts.json."""
    doc = read_json_object(root / "catalog/hosts.json", label="Hosts catalog")
    hosts = doc.get("hosts")
    return hosts if isinstance(hosts, dict) else {}


def expected_host_count(root: Path = ROOT) -> int:
    """Return total number of host adapters in catalog/hosts.json."""
    return len(hosts_map(root))


def recipes_map(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Return the recipes mapping from catalog/recipes.json."""
    doc = read_json_object(root / "catalog/recipes.json", label="Recipes catalog")
    recipes = doc.get("recipes")
    return recipes if isinstance(recipes, dict) else {}


def expected_recipe_count(root: Path = ROOT) -> int:
    """Return total number of task recipes in catalog/recipes.json."""
    return len(recipes_map(root))


def expected_task_route_count(root: Path = ROOT) -> int:
    """Return total number of task/alias routes mapped to suites."""
    return len(recipe_to_suite_map(root))


def expected_standalone_archive_count(root: Path = ROOT) -> int:
    """Return standalone tarball archive count (individual skills + consolidated suite)."""
    return expected_total_skills(root) + 1


GEMINI_PLATFORM_BUNDLE_COUNT = 3
STANDALONE_CLI_BUNDLE_COUNT = 2


def expected_unified_release_archive_count(root: Path = ROOT) -> int:
    """Return unified release archive count (tar+zip per pack + suite + standalone skills + Gemini + CLI bundles)."""
    return (
        2 * expected_pack_count(root)
        + expected_standalone_archive_count(root)
        + GEMINI_PLATFORM_BUNDLE_COUNT
        + STANDALONE_CLI_BUNDLE_COUNT
    )


def eval_cases(root: Path = ROOT) -> list[dict[str, Any]]:
    """Return evaluation case definitions from evals/cases.json."""
    doc = read_json_object(root / "evals/cases.json", label="Evaluation cases")
    cases = doc.get("cases")
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def expected_eval_case_count(root: Path = ROOT) -> int:
    """Return total evaluation case count from evals/cases.json."""
    return len(eval_cases(root))


def expected_paired_eval_run_count(
    root: Path = ROOT,
    *,
    configurations: int = 2,
) -> int:
    """Return total evaluation runs across paired configurations (without_skill + with_skill)."""
    return expected_eval_case_count(root) * configurations


def expected_non_audit_eval_run_count(
    root: Path = ROOT,
    *,
    configurations: int = 2,
) -> int:
    """Return evaluation run count for cases belonging to non-audit skills."""
    audit_skills = set(suite_skill_names(root, suite_name="repository-audits"))
    non_audit_cases = sum(
        1 for case in eval_cases(root) if case.get("skill") not in audit_skills
    )
    return non_audit_cases * configurations


def expected_routing_eval_case_count(root: Path = ROOT) -> int:
    """Return count of *-routing evaluation cases in evals/cases.json."""
    return sum(
        1 for case in eval_cases(root) if str(case.get("id", "")).endswith("-routing")
    )


def _extract_dict_string_keys(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def registered_fred_commands(root: Path = ROOT) -> set[str]:
    """Extract registered CLI subcommands from COMMAND_REGISTRY in scripts/fred.py via AST."""
    fred_path = root / "scripts/fred.py"
    tree = ast.parse(fred_path.read_text(encoding="utf-8"))
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "COMMAND_REGISTRY"
                for target in stmt.targets
            ):
                return _extract_dict_string_keys(stmt.value)
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "COMMAND_REGISTRY"
            and stmt.value is not None
        ):
            return _extract_dict_string_keys(stmt.value)
    return set()


def expected_fred_subcommand_count(root: Path = ROOT) -> int:
    """Return total number of subcommands in scripts/fred.py COMMAND_REGISTRY."""
    return len(registered_fred_commands(root))

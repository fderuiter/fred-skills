from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


_DEFAULT_SUITE_CONTRACTS: dict[str, dict[str, Any]] = {
    "repository-audits": {
        "filename": "audit.json",
        "schema": "shared/repo-audit/resources/assets/audit.schema.json",
        "starter": {
            "schema_version": "1.0",
            "contracts": [],
            "evidence": [],
            "findings": [],
            "issues": [],
            "limitations": [
                "Initialized workspace only; no application tests or agent audit executed."
            ],
            "checks": [],
        },
    },
    "repository-workflows": {
        "filename": "audit.json",
        "schema": "shared/repo-audit/resources/assets/audit.schema.json",
        "starter": {
            "schema_version": "1.0",
            "contracts": [],
            "evidence": [],
            "findings": [],
            "issues": [],
            "limitations": [
                "Initialized workspace only; no application tests or agent audit executed."
            ],
            "checks": [],
        },
    },
    "software-architecture": {
        "filename": "architecture.json",
        "schema": "skills/arch-decision-record/assets/architecture.schema.json",
        "starter": {
            "schema_version": "1.0",
            "title": "Architecture specification",
            "status": "draft",
            "boundaries": [],
            "decisions": [],
        },
    },
    "code-review": {
        "filename": "review.json",
        "schema": "skills/review-diff-analysis/assets/review.schema.json",
        "starter": {
            "schema_version": "1.0",
            "verdict": "comment",
            "summary": "Initialized workspace only; agent code review has not run.",
            "findings": [],
        },
    },
    "automated-testing": {
        "filename": "testing.json",
        "schema": "skills/test-case-generation/assets/testing.schema.json",
        "starter": {
            "schema_version": "1.0",
            "component": "repository",
            "framework": "unspecified",
            "test_cases": [],
        },
    },
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_suite_contracts(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    contracts = copy.deepcopy(_DEFAULT_SUITE_CONTRACTS)
    suites_path = root / "catalog/suites.json"
    if suites_path.is_file():
        try:
            suites_doc = read_json(suites_path)
            for suite in suites_doc.get("suites", []):
                name = suite.get("name")
                artifact = suite.get("artifact")
                if (
                    isinstance(name, str)
                    and isinstance(artifact, dict)
                    and isinstance(artifact.get("filename"), str)
                    and isinstance(artifact.get("schema"), str)
                    and isinstance(artifact.get("starter"), dict)
                ):
                    contracts[name] = {
                        "filename": artifact["filename"],
                        "schema": artifact["schema"],
                        "starter": copy.deepcopy(artifact["starter"]),
                    }
        except (OSError, ValueError, TypeError):
            pass
    return contracts


_DEFAULT_WORKFLOW_DELIVERABLE_SCHEMAS: dict[str, str] = {
    "finding-verification.json": "skills/repo-verify-finding/assets/finding-verification.schema.json",
    "regression.json": "skills/repo-specify-regression/assets/regression.schema.json",
    "plan.json": "skills/repo-plan-fix/assets/plan.schema.json",
    "fix.json": "skills/repo-apply-fix/assets/fix.schema.json",
    "verification.json": "skills/repo-verify-fix/assets/verification.schema.json",
    "reconciliation.json": "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
    "remediation.json": "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
}

WORKFLOW_SKILL_DELIVERABLES: dict[str, tuple[str, ...]] = {
    "repo-verify-finding": ("finding-verification.json",),
    "repo-specify-regression": ("regression.json",),
    "repo-plan-fix": ("plan.json",),
    "repo-apply-fix": ("fix.json",),
    "repo-verify-fix": ("verification.json",),
    "repo-reconcile-findings": ("reconciliation.json", "remediation.json"),
}


def _load_workflow_companions(root: Path = ROOT) -> dict[str, str]:
    companions = dict(_DEFAULT_WORKFLOW_DELIVERABLE_SCHEMAS)
    suites_path = root / "catalog/suites.json"
    if suites_path.is_file():
        try:
            suites_doc = read_json(suites_path)
            for suite in suites_doc.get("suites", []):
                raw = suite.get("companions")
                if isinstance(raw, dict):
                    for filename, schema_rel in raw.items():
                        if isinstance(filename, str) and isinstance(schema_rel, str):
                            companions[filename] = schema_rel
        except (OSError, ValueError, TypeError):
            pass
    return companions


SUITE_DELIVERABLES: dict[str, tuple[str, str]] = {
    name: (contract["filename"], contract["schema"])
    for name, contract in _load_suite_contracts(ROOT).items()
}

WORKFLOW_DELIVERABLE_SCHEMAS: dict[str, str] = _load_workflow_companions(ROOT)

DOMAIN_DELIVERABLE_SCHEMAS: dict[str, str] = {
    **{
        contract["filename"]: contract["schema"]
        for contract in _load_suite_contracts(ROOT).values()
        if contract["filename"] != "audit.json"
    },
    **WORKFLOW_DELIVERABLE_SCHEMAS,
}


def _suite_for_skills(skills: list[str], root: Path = ROOT) -> str:
    skill_to_suite: dict[str, str] = {}
    suites_path = root / "catalog/suites.json"
    if suites_path.is_file():
        try:
            for suite in read_json(suites_path).get("suites", []):
                suite_name = suite.get("name")
                if isinstance(suite_name, str):
                    for phase in suite.get("phases", []):
                        for member in phase.get("skills", []):
                            if isinstance(member, str):
                                skill_to_suite[member] = suite_name
        except (OSError, ValueError, TypeError):
            skill_to_suite.clear()
    if not skill_to_suite:
        skills_path = root / "catalog/skills.json"
        if skills_path.is_file():
            skill_to_suite = {
                row["name"]: row["suite"]
                for row in read_json(skills_path)["skills"]
                if row.get("suite")
            }
    suites = list(
        dict.fromkeys(skill_to_suite[s] for s in skills if s in skill_to_suite)
    )
    if "repository-audits" in suites:
        return "repository-audits"
    return suites[0] if suites else "repository-audits"


def _suite_for_run(run: dict, root: Path = ROOT) -> str:
    plan_data = run.get("plan", {}) if isinstance(run, dict) else {}
    outputs = plan_data.get("expected_outputs", [])
    contracts = _load_suite_contracts(root)
    if isinstance(outputs, list):
        for suite_name, contract in contracts.items():
            filename = contract["filename"]
            if filename != "audit.json" and filename in outputs:
                return suite_name
    skills = plan_data.get("skills", [])
    if isinstance(skills, list) and skills:
        return _suite_for_skills(skills, root=root)
    task = plan_data.get("task")
    if isinstance(task, str):
        recipes_path = root / "catalog/recipes.json"
        if recipes_path.is_file():
            recipes = read_json(recipes_path).get("recipes", {})
            if task in recipes:
                return _suite_for_skills(recipes[task]["skills"], root=root)
    return "repository-audits"


def _artifact_contract_for_run(run: dict, root: Path = ROOT) -> dict[str, Any]:
    contracts = _load_suite_contracts(root)
    suite_name = _suite_for_run(run, root=root)
    plan_data = run.get("plan", {}) if isinstance(run, dict) else {}
    outputs = plan_data.get("expected_outputs", [])
    if isinstance(outputs, list):
        for candidate in contracts.values():
            fname = candidate["filename"]
            if fname != "audit.json" and fname in outputs:
                return copy.deepcopy(candidate)
        if "audit.json" in outputs and suite_name in contracts:
            if contracts[suite_name]["filename"] == "audit.json":
                return copy.deepcopy(contracts[suite_name])
            return copy.deepcopy(contracts["repository-audits"])
    return copy.deepcopy(contracts.get(suite_name, contracts["repository-audits"]))


def _deliverable_for_run(run: dict, root: Path = ROOT) -> str:
    return str(_artifact_contract_for_run(run, root=root)["filename"])


_DOMAIN_KIND_KEY_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("remediation", frozenset({"records"})),
    (
        "reconciliation",
        frozenset(
            {
                "finding_fingerprint_matches",
                "fingerprint_matches",
                "remediation_state_transitions",
                "state_transitions",
                "unmapped_records",
            }
        ),
    ),
    (
        "finding-verification",
        frozenset(
            {
                "reproduction_status",
                "safe_control_outcomes",
                "confidence_rating",
            }
        ),
    ),
    (
        "regression",
        frozenset(
            {
                "failure_boundaries",
                "failure_boundary",
                "test_command_traces",
                "assertion_expectations",
            }
        ),
    ),
    (
        "plan",
        frozenset(
            {
                "task_breakdowns",
                "dependency_order",
                "file_scopes",
                "plan_id",
            }
        ),
    ),
    (
        "fix",
        frozenset(
            {
                "file_changes",
                "diff_summaries",
                "diff_summary",
                "execution_results",
                "compatibility_limits",
            }
        ),
    ),
    (
        "verification",
        frozenset(
            {
                "original_reproduced",
                "regression_passed",
                "before_revision",
                "after_revision",
                "safe_controls_passed",
            }
        ),
    ),
    ("review", frozenset({"verdict"})),
    ("testing", frozenset({"test_cases"})),
    ("architecture", frozenset({"boundaries", "decisions"})),
    ("run", frozenset({"plan", "snapshot", "authority", "checkpoints"})),
)


def _is_audit_ledger_shape(data: dict[str, Any]) -> bool:
    return "findings" in data and any(
        key in data for key in ("evidence", "coverage", "contracts", "run_id")
    )


def _detect_domain_kind(data: object) -> str | None:
    if not isinstance(data, dict) or _is_audit_ledger_shape(data):
        return None
    keys = set(data)
    for kind, markers in _DOMAIN_KIND_KEY_SETS:
        if keys & markers:
            return kind
    if "summary" in data and "findings" in data:
        return "review"
    if "component" in data and "framework" in data:
        return "testing"
    if "title" in data and "status" in data and "findings" not in data:
        return "architecture"
    if "run_id" in data:
        return "run"
    return None


def resolve_audit_path(path: Path | str | None = None) -> Path:
    p = Path(path) if path is not None else Path(".")
    if p.is_dir():
        run_file = p / "run.json"
        if run_file.is_file():
            try:
                expected = _deliverable_for_run(read_json(run_file))
            except (OSError, ValueError, TypeError):
                expected = "audit.json"
            target = p / expected
            if target.is_file():
                return target
            if expected == "audit.json":
                raise ValueError(
                    f"Audit ledger 'audit.json' not found in directory: {p}"
                )
            raise ValueError(
                f"Suite deliverable '{expected}' not found in directory: {p}"
            )
        audit_file = p / "audit.json"
        if audit_file.is_file():
            return audit_file
        suite_files = [
            p / name
            for name in DOMAIN_DELIVERABLE_SCHEMAS
            if name not in WORKFLOW_DELIVERABLE_SCHEMAS and (p / name).is_file()
        ]
        if len(suite_files) == 1:
            return suite_files[0]
        if len(suite_files) > 1:
            names = ", ".join(f.name for f in suite_files)
            raise ValueError(
                f"Multiple suite deliverables ({names}) found in directory {p}; specify an explicit file path"
            )
        for wf_name in WORKFLOW_DELIVERABLE_SCHEMAS:
            if (p / wf_name).is_file():
                raise ValueError(
                    f"Audit ledger 'audit.json' not found in directory: {p}; workflow artifact '{wf_name}' is a companion deliverable, not a standalone audit ledger"
                )
        raise ValueError(f"Audit ledger 'audit.json' not found in directory: {p}")
    return p


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".fred-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(data) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def relative_path(value: str) -> Path:
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Expected a nonempty repository-relative POSIX path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"..", ".git"} for part in pure.parts)
    ):
        raise ValueError(f"Unsafe relative path: {value!r}")
    return Path(*pure.parts)


def package_files(package: Path) -> dict[str, str]:
    """Identity by bytes and relative names; timestamps are intentionally irrelevant."""
    if package.is_symlink() or not package.is_dir():
        raise ValueError(f"Expected a real package directory: {package}")
    result = {}
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported in packages: {path}")
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        result[path.relative_to(package).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result


def version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def choose_skills(
    root: Path = ROOT, pack: str | None = None, skills: list[str] | None = None
) -> list[str]:
    catalog = {
        item["name"] for item in read_json(root / "catalog/skills.json")["skills"]
    }
    selected = list(skills or [])
    if pack:
        packs = read_json(root / "catalog/packs.json")["packs"]
        if pack not in packs:
            raise ValueError(f"Unknown pack {pack!r}; use fred.py packs")
        selected.extend(packs[pack]["skills"])
    if not selected:
        raise ValueError("Select a pack or at least one --skill")
    selected = list(dict.fromkeys(selected))
    unknown = set(selected) - catalog
    if unknown:
        raise ValueError(f"Unknown skills: {sorted(unknown)}")
    return selected

from __future__ import annotations

import copy
import functools
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Protocol, cast

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _markdown_utils import (
    non_fenced_lines,
    parse_reference_link_definition,
)
from skill_tools import (
    DOMAIN_KIND_KEY_SETS,
    SKILL_SUITE_PREFIXES,
    FredCliError,
    classify_domain_payload,
    detect_artifact_suite,
    detect_domain_artifact,
    infer_suite_name,
    is_audit_ledger_shape,
    read_json_object,
    read_suites_catalog,
    read_utf8_text,
    recipe_to_suite_map,
    skill_to_suite_map,
    suite_for_skills,
    suite_skills_by_suite,
    workflow_phase_deliverables,
    workflow_phase_order_map,
)

_DOMAIN_KIND_KEY_SETS = DOMAIN_KIND_KEY_SETS
_SKILL_SUITE_PREFIXES = SKILL_SUITE_PREFIXES
_non_fenced_lines = non_fenced_lines
_parse_reference_link_definition = parse_reference_link_definition
_read_utf8_text = read_utf8_text

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
        "schema": "shared/software-architecture/resources/assets/architecture.schema.json",
        "starter": {
            "schema_version": "1.0",
            "title": "Architecture specification",
            "status": "draft",
            "coverage": [],
            "boundaries": [],
            "components": [],
            "decisions": [],
        },
    },
    "code-review": {
        "filename": "review.json",
        "schema": "shared/code-review/resources/assets/review.schema.json",
        "starter": {
            "schema_version": "1.0",
            "verdict": "comment",
            "summary": "Initialized workspace only; agent code review has not run.",
            "coverage": [],
            "findings": [],
        },
    },
    "automated-testing": {
        "filename": "testing.json",
        "schema": "shared/automated-testing/resources/assets/testing.schema.json",
        "starter": {
            "schema_version": "1.0",
            "component": "repository",
            "framework": "unspecified",
            "coverage": [],
            "test_cases": [],
        },
    },
    "spec-driven-development": {
        "filename": "spec.json",
        "schema": "shared/spec-driven-development/resources/assets/spec.schema.json",
        "starter": {
            "schema_version": "1.0",
            "title": "Feature specification",
            "status": "draft",
            "interview_decisions": [],
            "new_file_scopes": [],
            "out_of_scope": [],
            "verification_commands": [],
        },
    },
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_suite_contracts(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    contracts = copy.deepcopy(_DEFAULT_SUITE_CONTRACTS)
    suites = read_suites_catalog(root)
    if suites is not None:
        for suite in suites:
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
    return contracts


WORKFLOW_PHASE_SCHEMAS: dict[int, tuple[str, str]] = {
    0: (
        "finding-verification.json",
        "skills/repo-verify-finding/assets/finding-verification.schema.json",
    ),
    1: (
        "regression.json",
        "skills/repo-specify-regression/assets/regression.schema.json",
    ),
    2: (
        "plan.json",
        "skills/repo-plan-fix/assets/plan.schema.json",
    ),
    3: (
        "fix.json",
        "skills/repo-apply-fix/assets/fix.schema.json",
    ),
    4: (
        "verification.json",
        "skills/repo-verify-fix/assets/verification.schema.json",
    ),
    5: (
        "reconciliation.json",
        "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
    ),
}

_DEFAULT_WORKFLOW_DELIVERABLE_SCHEMAS: dict[str, str] = {
    deliverable: schema_rel
    for deliverable, schema_rel in WORKFLOW_PHASE_SCHEMAS.values()
} | {
    "remediation.json": "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
    "observations.json": "schemas/observations.schema.json",
    "scanner-observations.json": "schemas/observations.schema.json",
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
    companions.update(workflow_phase_deliverables(root))
    suites = read_suites_catalog(root)
    if suites is not None:
        for suite in suites:
            raw = suite.get("companions")
            if isinstance(raw, dict):
                for filename, schema_rel in raw.items():
                    if isinstance(filename, str) and isinstance(schema_rel, str):
                        companions[filename] = schema_rel
    return companions


def _workflow_phase_order_map(root: Path = ROOT) -> dict[str, int]:
    return workflow_phase_order_map(root)


SUITE_DELIVERABLES: dict[str, tuple[str, str]] = {
    name: (contract["filename"], contract["schema"])
    for name, contract in _load_suite_contracts(ROOT).items()
}

WORKFLOW_DELIVERABLE_SCHEMAS: dict[str, str] = _load_workflow_companions(ROOT)

DOMAIN_DELIVERABLE_SCHEMAS: dict[str, str] = {
    contract["filename"]: contract["schema"]
    for contract in _load_suite_contracts(ROOT).values()
    if contract["filename"] != "audit.json"
}


def _suite_skills_by_suite(root: Path = ROOT) -> dict[str, tuple[str, ...]] | None:
    return suite_skills_by_suite(root)


def _skill_to_suite_map(root: Path = ROOT) -> dict[str, str]:
    return skill_to_suite_map(root)


def _infer_suite_name(
    skill_name: str, available_suites: Sequence[str] | None = None
) -> str:
    return infer_suite_name(skill_name, available_suites)


def _task_to_suite_map(root: Path = ROOT) -> dict[str, str]:
    return recipe_to_suite_map(root)


def _suite_for_skills(skills: list[str], root: Path = ROOT) -> str:
    return suite_for_skills(skills, root=root)


def _suite_for_run(run: dict, root: Path = ROOT) -> str:
    raw_plan = run.get("plan") if isinstance(run, dict) else None
    plan_data = raw_plan if isinstance(raw_plan, dict) else {}
    outputs = plan_data.get("expected_outputs", [])
    contracts = _load_suite_contracts(root)
    if isinstance(outputs, list):
        for suite_name, contract in contracts.items():
            filename = contract["filename"]
            if filename != "audit.json" and filename in outputs:
                return suite_name
    skills = plan_data.get("skills", [])
    if isinstance(skills, list):
        str_skills = [s for s in skills if isinstance(s, str)]
        if str_skills:
            return _suite_for_skills(str_skills, root=root)
    task = plan_data.get("task")
    if isinstance(task, str):
        task_map = _task_to_suite_map(root)
        if task in task_map:
            return task_map[task]
    return "repository-audits"


def _artifact_contract_for_run(run: dict, root: Path = ROOT) -> dict[str, Any]:
    contracts = _load_suite_contracts(root)
    suite_name = _suite_for_run(run, root=root)
    raw_plan = run.get("plan") if isinstance(run, dict) else None
    plan_data = raw_plan if isinstance(raw_plan, dict) else {}
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


def _is_single_line_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if "\n" in value or "\r" in value or "\x00" in value:
        return False
    return not any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in value)


def _escape_code_span_char(ch: str) -> str:
    if ch == "\n":
        return r"\n"
    if ch == "\r":
        return r"\r"
    if ch == "\t":
        return r"\t"
    if ch == "\x00":
        return r"\x00"
    if unicodedata.category(ch) in {"Cc", "Cf"}:
        return f"\\u{ord(ch):04x}"
    return ch


def _markdown_code_span(value: object) -> str:
    sanitized = "".join(_escape_code_span_char(ch) for ch in str(value))
    if not sanitized:
        return "` `"
    max_ticks = max(
        (len(match.group(0)) for match in re.finditer(r"`+", sanitized)),
        default=0,
    )
    fence = "`" * (max_ticks + 1)
    needs_pad = (
        sanitized.startswith("`")
        or sanitized.endswith("`")
        or (
            sanitized.startswith(" ")
            and sanitized.endswith(" ")
            and bool(sanitized.strip(" "))
        )
    )
    inner = f" {sanitized} " if needs_pad else sanitized
    return f"{fence}{inner}{fence}"


def _suite_shared_assets(suite_name: str, root: Path = ROOT) -> list[tuple[Path, Path]]:
    shared_dir = root / "shared"
    if not shared_dir.is_dir():
        return []
    assets: list[tuple[Path, Path]] = []
    for manifest_path in sorted(shared_dir.glob("*/manifest.json")):
        manifest = read_json_object(
            manifest_path,
            label="Shared resource manifest 'manifest.json'",
            optional=True,
        )
        if manifest is None:
            continue
        if manifest.get("suite", "repository-audits") != suite_name:
            continue
        for raw_entry in manifest.get("files", []):
            if not isinstance(raw_entry, str):
                continue
            rel = relative_path(raw_entry)
            if rel.parts and rel.parts[0] == "assets":
                source = manifest_path.parent / "resources" / rel
                if source.is_file() and not source.is_symlink():
                    assets.append((rel, source))
    return assets


def _is_audit_ledger_shape(data: Mapping[str, Any]) -> bool:
    return is_audit_ledger_shape(data)


def _detect_domain_kind(data: object) -> str | None:
    return classify_domain_payload(data)


def _detect_domain_artifact(data: object) -> str | None:
    return detect_domain_artifact(data)


def _detect_artifact_suite(data: object) -> str | None:
    return detect_artifact_suite(data)


class _ScriptValidator(Protocol):
    def validate(self, data: Any, schema: Mapping[str, Any]) -> list[str]: ...
    def _schema_errors(self, data: Any, schema: Mapping[str, Any]) -> list[str]: ...


_CANONICAL_VALIDATOR_SCRIPTS: dict[str, tuple[str, str]] = {
    "audit": ("fred_audit_validator", "scripts/validate_audit.py"),
    "bundle": ("fred_audit_validator", "scripts/validate_audit.py"),
    "architecture": (
        "fred_architecture_validator",
        "scripts/validate_architecture.py",
    ),
    "review": ("fred_review_validator", "scripts/validate_review.py"),
    "testing": ("fred_testing_validator", "scripts/validate_testing.py"),
    "spec": ("fred_spec_validator", "scripts/validate_spec.py"),
    "workflow": ("fred_workflow_validator", "scripts/validate_workflow.py"),
}

_SCHEMA_NAME_VALIDATOR_KEYS: dict[str, str] = {
    "audit.schema.json": "audit",
    "bundle.schema.json": "bundle",
    "architecture.schema.json": "architecture",
    "review.schema.json": "review",
    "testing.schema.json": "testing",
    "spec.schema.json": "spec",
    "finding-verification.schema.json": "workflow",
    "regression.schema.json": "workflow",
    "plan.schema.json": "workflow",
    "fix.schema.json": "workflow",
    "verification.schema.json": "workflow",
    "reconciliation.schema.json": "workflow",
    "observations.schema.json": "workflow",
    "session-handoff.schema.json": "workflow",
    "handoff.schema.json": "workflow",
    "terse-output-commit.schema.json": "workflow",
    "commit.schema.json": "workflow",
}

_SESSION_HANDOFF_SCHEMA_REL = (
    "skills/session-handoff/assets/session-handoff.schema.json"
)
_TERSE_COMMIT_SCHEMA_REL = (
    "skills/terse-output-commit/assets/terse-output-commit.schema.json"
)

_ARTIFACT_VALIDATOR_TARGETS: dict[str, tuple[str, str]] = {
    "audit.json": ("audit", "shared/repo-audit/resources/assets/audit.schema.json"),
    "bundle.json": (
        "bundle",
        "skills/repo-audit-diagnostics/assets/bundle.schema.json",
    ),
    "architecture.json": (
        "architecture",
        "shared/software-architecture/resources/assets/architecture.schema.json",
    ),
    "review.json": (
        "review",
        "shared/code-review/resources/assets/review.schema.json",
    ),
    "testing.json": (
        "testing",
        "shared/automated-testing/resources/assets/testing.schema.json",
    ),
    "spec.json": (
        "spec",
        "shared/spec-driven-development/resources/assets/spec.schema.json",
    ),
    "session-handoff.json": ("workflow", _SESSION_HANDOFF_SCHEMA_REL),
    "handoff.json": ("workflow", _SESSION_HANDOFF_SCHEMA_REL),
    "terse-output-commit.json": ("workflow", _TERSE_COMMIT_SCHEMA_REL),
    "commit.json": ("workflow", _TERSE_COMMIT_SCHEMA_REL),
} | {
    wf_name: ("workflow", wf_schema)
    for wf_name, wf_schema in _DEFAULT_WORKFLOW_DELIVERABLE_SCHEMAS.items()
}

_BUNDLE_PAYLOAD_KEYS = frozenset(
    {
        "failure_classification",
        "recovery",
        "redaction",
        "scrubbed_transcript",
        "session_id",
        "transcript_discovery",
    }
)
_HANDOFF_PAYLOAD_KEYS = frozenset(
    {"dead_ends", "next_action", "untouched_scope", "verified_facts"}
)
_COMMIT_PAYLOAD_KEYS = frozenset(
    {"commit_body_bullets", "commit_subject", "compression_level", "terse_response"}
)


def _load_canonical_validator(
    validator_key: str,
    root: Path = ROOT,
    *,
    spec_factory: Any = None,
) -> _ScriptValidator:
    if validator_key not in _CANONICAL_VALIDATOR_SCRIPTS:
        raise ValueError(f"Unknown canonical validator key: {validator_key!r}")
    module_name, script_rel = _CANONICAL_VALIDATOR_SCRIPTS[validator_key]
    script = (root / script_rel).resolve()
    cached = sys.modules.get(module_name)
    cached_file = getattr(cached, "__file__", None)
    if (
        isinstance(cached, ModuleType)
        and isinstance(cached_file, str)
        and Path(cached_file).resolve() == script
        and callable(getattr(cached, "validate", None))
    ):
        return cast(_ScriptValidator, cached)
    loader_fn = spec_factory or importlib.util.spec_from_file_location
    spec = loader_fn(module_name, script)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to load validator from {script}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.modules[spec.name] = module
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    finally:
        sys.dont_write_bytecode = previous
    return cast(_ScriptValidator, module)


def _resolve_payload_shape_target(
    payload: Mapping[Any, Any], schema_rel: str | None
) -> tuple[str, str] | None:
    if "findings" not in payload:
        payload_keys = set(payload.keys())
        if _BUNDLE_PAYLOAD_KEYS & payload_keys:
            return (
                "bundle",
                schema_rel or "skills/repo-audit-diagnostics/assets/bundle.schema.json",
            )
        if _HANDOFF_PAYLOAD_KEYS <= payload_keys:
            return ("workflow", schema_rel or _SESSION_HANDOFF_SCHEMA_REL)
        if _COMMIT_PAYLOAD_KEYS <= payload_keys:
            return ("workflow", schema_rel or _TERSE_COMMIT_SCHEMA_REL)
    if is_audit_ledger_shape(payload):
        return (
            "audit",
            schema_rel or "shared/repo-audit/resources/assets/audit.schema.json",
        )
    return None


def _resolve_validator_target(
    artifact_name: str | None,
    schema_rel: str | None,
    payload: object,
    root: Path = ROOT,
) -> tuple[str, str]:
    if artifact_name is not None:
        norm_name = (
            Path(artifact_name).name
            if artifact_name.endswith(".json")
            else f"{artifact_name}.json"
        )
        if norm_name in _ARTIFACT_VALIDATOR_TARGETS:
            v_key, default_schema = _ARTIFACT_VALIDATOR_TARGETS[norm_name]
            return v_key, (schema_rel or default_schema)
        companions = _load_workflow_companions(root)
        if norm_name in companions:
            return "workflow", (schema_rel or companions[norm_name])
    if schema_rel is not None:
        schema_name = PurePosixPath(schema_rel).name
        if schema_name in _SCHEMA_NAME_VALIDATOR_KEYS:
            return _SCHEMA_NAME_VALIDATOR_KEYS[schema_name], schema_rel
    detected = detect_domain_artifact(payload)
    if detected is not None and detected in _ARTIFACT_VALIDATOR_TARGETS:
        v_key, default_schema = _ARTIFACT_VALIDATOR_TARGETS[detected]
        return v_key, (schema_rel or default_schema)
    if isinstance(payload, Mapping):
        matched = _resolve_payload_shape_target(payload, schema_rel)
        if matched is not None:
            return matched
    raise ValueError(
        f"Unsupported canonical validator target: artifact={artifact_name!r}, schema={schema_rel!r}"
    )


@functools.lru_cache(maxsize=32)
def _load_cached_schema_object(schema_path: Path) -> dict[str, Any]:
    return read_json_object(schema_path, label=f"Schema '{schema_path.name}'")


def _validate_canonical_artifact(
    payload: object,
    *,
    artifact_name: str | None = None,
    schema_rel: str | None = None,
    root: Path = ROOT,
    spec_factory: Any = None,
    schema_obj: Mapping[str, Any] | None = None,
) -> list[str]:
    validator_key, resolved_schema_rel = _resolve_validator_target(
        artifact_name, schema_rel, payload, root=root
    )
    validator = _load_canonical_validator(
        validator_key, root=root, spec_factory=spec_factory
    )
    if schema_obj is None:
        schema_path = (root / resolved_schema_rel).resolve()
        schema_obj = (
            _load_cached_schema_object(schema_path)
            if root == ROOT
            else read_json_object(schema_path, label=f"Schema '{resolved_schema_rel}'")
        )
    return validator.validate(payload, schema_obj)


def resolve_audit_path(path: Path | str | None = None) -> Path:
    p = Path(path) if path is not None else Path(".")
    if p.is_dir():
        run_file = p / "run.json"
        if run_file.is_file():
            expected = _deliverable_for_run(
                read_json_object(run_file, label="Run metadata 'run.json'")
            )
            target = p / expected
            if target.is_file():
                return target
            if expected == "audit.json":
                raise FredCliError(
                    f"Audit ledger 'audit.json' not found in directory: {p}",
                    code="missing_audit_ledger",
                    hint="Pass an audit run directory containing 'audit.json' or an explicit 'audit.json' file path.",
                )
            raise FredCliError(
                f"Suite deliverable '{expected}' not found in directory: {p}",
                code="missing_deliverable",
                hint=f"Ensure '{expected}' exists in '{p}' or re-initialize the run workspace.",
            )
        audit_file = p / "audit.json"
        if audit_file.is_file():
            return audit_file
        suite_files = [
            p / name for name in DOMAIN_DELIVERABLE_SCHEMAS if (p / name).is_file()
        ]
        if len(suite_files) == 1:
            return suite_files[0]
        if len(suite_files) > 1:
            names = ", ".join(f.name for f in suite_files)
            raise FredCliError(
                f"Multiple suite deliverables ({names}) found in directory {p}; specify an explicit file path",
                code="ambiguous_deliverable",
                hint="Pass an explicit deliverable JSON file path instead of the directory.",
            )
        for wf_name in WORKFLOW_DELIVERABLE_SCHEMAS:
            if (p / wf_name).is_file():
                raise FredCliError(
                    f"Audit ledger 'audit.json' not found in directory: {p}; workflow artifact '{wf_name}' is a companion deliverable, not a standalone audit ledger",
                    code="unsupported_domain_artifact",
                    hint="Pass an audit run directory containing 'audit.json' or an explicit 'audit.json' file path.",
                )
        raise FredCliError(
            f"Audit ledger 'audit.json' not found in directory: {p}",
            code="missing_audit_ledger",
            hint="Pass an audit run directory containing 'audit.json' or an explicit 'audit.json' file path.",
        )
    return p


def resolve_manifest_path(path: Path | str) -> Path:
    p = Path(path)
    if not p.exists():
        raise FredCliError(
            f"Release manifest not found: {p}",
            code="missing_manifest",
            hint="Pass a path to 'manifest.json' or a release directory produced by 'python scripts/fred.py release'.",
        )
    if p.is_dir():
        candidate = p / "manifest.json"
        if candidate.is_file():
            return candidate
        raise FredCliError(
            f"Release manifest 'manifest.json' not found in directory: {p}",
            code="missing_manifest",
            hint="Run 'python scripts/fred.py release --output <dir>' first, or pass an explicit 'manifest.json' path.",
        )
    if not p.is_file():
        raise FredCliError(
            f"Release manifest path is not a regular file: {p}",
            code="invalid_manifest_path",
            hint="Pass a path to 'manifest.json' or a release directory containing 'manifest.json'.",
        )
    return p


def resolve_run_directory(path: Path | str, *, require_run_json: bool = True) -> Path:
    p = Path(path)
    if not p.exists():
        raise FredCliError(
            f"Run directory not found: {p}",
            code="missing_run_directory",
            hint="Initialize a run workspace with 'python scripts/fred.py init <repo> --task <task> --output <dir>' or check the directory path.",
        )
    if not p.is_dir():
        raise FredCliError(
            f"Expected a run directory, got file: {p}",
            code="invalid_run_directory",
            hint="Pass the audit run directory containing 'run.json' rather than an individual file.",
        )
    if require_run_json and not (p / "run.json").is_file():
        raise FredCliError(
            f"Run metadata 'run.json' not found in directory: {p}",
            code="missing_run_json",
            hint="Ensure the directory was initialized with 'python scripts/fred.py init' and contains 'run.json'.",
        )
    return p


def _target_file_mode(path: Path) -> int:
    try:
        st = path.lstat()
        if not stat.S_ISLNK(st.st_mode):
            return stat.S_IMODE(st.st_mode)
    except OSError:
        pass
    umask = os.umask(0o077)
    os.umask(umask)
    return 0o666 & ~umask


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_file_mode(path)
    fd, name = tempfile.mkstemp(prefix=".fred-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            if os.name == "nt":
                os.chmod(name, stat.S_IWRITE)
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
        raise FredCliError(
            "Expected a nonempty repository-relative POSIX path",
            code="invalid_relative_path",
            hint="Use forward slashes ('/') and a relative path inside the directory.",
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"..", ".git"} for part in pure.parts)
    ):
        raise FredCliError(
            f"Unsafe relative path: {value!r}",
            code="unsafe_relative_path",
            hint="Remove leading '/', drive letters, '..', and '.git' path segments.",
        )
    return Path(*pure.parts)


_IGNORED_SKILL_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".github",
        ".venv",
        "venv",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
    }
)
_IGNORED_SKILL_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
_IGNORED_SKILL_FILE_NAMES = frozenset({".DS_Store", ".gitignore", ".gitattributes"})
_ALLOWED_SKILL_TEXT_SUFFIXES = frozenset({".md", ".json", ".yaml", ".svg"})
_DISALLOWED_SKILL_DOC_NAMES = frozenset({"readme.md", "changelog.md"})


def _is_ignored_skill_entry(rel_parts: tuple[str, ...], name: str, suffix: str) -> bool:
    return (
        any(part in _IGNORED_SKILL_DIR_NAMES for part in rel_parts)
        or suffix in _IGNORED_SKILL_FILE_SUFFIXES
        or name in _IGNORED_SKILL_FILE_NAMES
    )


def _is_skill_script_rel(rel_posix: str) -> bool:
    parts = PurePosixPath(rel_posix).parts
    return (
        len(parts) == 2
        and parts[0] == "scripts"
        and parts[1].endswith(".py")
        and parts[1] != ".py"
    )


def _is_executable_skill_bytes(path: Path, data: bytes) -> bool:
    return (os.name != "nt" and bool(path.stat().st_mode & 0o111)) or data.startswith(
        b"#!"
    )


def _validate_allowed_skill_file(rel_posix: str, data: bytes) -> None:
    pure = PurePosixPath(rel_posix)
    if pure.name.lower() in _DISALLOWED_SKILL_DOC_NAMES:
        raise FredCliError(
            f"Disallowed documentation clutter in skill package: {rel_posix}",
            code="disallowed_package_file",
            hint="Keep README.md and CHANGELOG.md outside installable skills/ packages.",
        )
    if not (
        _is_skill_script_rel(rel_posix) or pure.suffix in _ALLOWED_SKILL_TEXT_SUFFIXES
    ):
        raise FredCliError(
            f"Disallowed file in skill package: {rel_posix}",
            code="disallowed_package_file",
            hint="Skill packages allow only UTF-8 .md, .json, .yaml, .svg, and scripts/*.py files.",
        )
    if b"\x00" in data:
        raise FredCliError(
            f"Binary file (NUL byte) not allowed in skill package: {rel_posix}",
            code="binary_package_file",
            hint="Remove binary assets from skill packages.",
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FredCliError(
            f"Binary or non-UTF-8 file not allowed in skill package: {rel_posix}",
            code="binary_package_file",
            hint="Encode all skill package files as valid UTF-8 text.",
        ) from exc


def _scan_validated_skill_files(
    package_dir: Path, root: Path | None = None
) -> tuple[list[tuple[str, bytes]], set[str]]:
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise FredCliError(
            f"Expected a real package directory: {package_dir}",
            code="invalid_package_directory",
            hint="Ensure the skill package path exists as a regular directory and is not a symlink.",
        )
    resolved_root = root.resolve() if root is not None else None
    if resolved_root is not None and not package_dir.resolve().is_relative_to(
        resolved_root
    ):
        raise ValueError(f"Package directory escapes repository root: {package_dir}")
    files: list[tuple[str, bytes]] = []
    executable_paths: set[str] = set()
    for path in sorted(
        package_dir.rglob("*"),
        key=lambda p: p.relative_to(package_dir).as_posix(),
    ):
        if path.is_symlink():
            raise FredCliError(
                f"Symlinks are not supported in packages: {path}",
                code="unsupported_symlink",
                hint="Replace symlinks inside skill packages with regular files.",
            )
        if resolved_root is not None and not path.resolve().is_relative_to(
            resolved_root
        ):
            raise ValueError(f"File escapes repository root: {path}")
        if not path.is_file():
            continue
        rel_path = path.relative_to(package_dir)
        if _is_ignored_skill_entry(rel_path.parts, path.name, path.suffix):
            continue
        rel = rel_path.as_posix()
        data = path.read_bytes()
        _validate_allowed_skill_file(rel, data)
        files.append((rel, data))
        if _is_skill_script_rel(rel) and _is_executable_skill_bytes(path, data):
            executable_paths.add(rel)
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    return files, executable_paths


def _copy_allowed_skill_package(
    source: Path, destination: Path, root: Path | None = None
) -> None:
    files, executable_paths = _scan_validated_skill_files(source, root=root)
    destination.mkdir(parents=True, exist_ok=True)
    for rel, data in files:
        out_path = destination / relative_path(rel)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        if os.name != "nt":
            out_path.chmod(0o755 if rel in executable_paths else 0o644)


def package_files(package: Path) -> dict[str, str]:
    """Identity by bytes and relative names; timestamps are intentionally irrelevant."""
    if package.is_symlink() or not package.is_dir():
        raise FredCliError(
            f"Expected a real package directory: {package}",
            code="invalid_package_directory",
            hint="Ensure the skill package path exists as a regular directory and is not a symlink.",
        )
    result = {}
    for path in sorted(
        package.rglob("*"),
        key=lambda p: p.relative_to(package).as_posix(),
    ):
        if path.is_symlink():
            raise FredCliError(
                f"Symlinks are not supported in packages: {path}",
                code="unsupported_symlink",
                hint="Replace symlinks inside skill packages with regular files.",
            )
        rel = path.relative_to(package)
        if (
            not path.is_file()
            or "__pycache__" in rel.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        result[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def choose_skills(
    root: Path = ROOT, pack: str | None = None, skills: list[str] | None = None
) -> list[str]:
    skills_doc = read_json_object(
        root / "catalog/skills.json", label="Skills catalog 'catalog/skills.json'"
    )
    raw_skills = skills_doc.get("skills")
    if not isinstance(raw_skills, list):
        raise FredCliError(
            f"Malformed skills catalog '{root / 'catalog/skills.json'}': expected 'skills' array",
            code="invalid_catalog",
            hint="Ensure 'catalog/skills.json' contains a JSON array under 'skills'.",
        )
    catalog = {
        item["name"]
        for item in raw_skills
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    selected = list(skills or [])
    if pack:
        packs_doc = read_json_object(
            root / "catalog/packs.json", label="Packs catalog 'catalog/packs.json'"
        )
        packs = packs_doc.get("packs")
        if not isinstance(packs, dict):
            raise FredCliError(
                f"Malformed packs catalog '{root / 'catalog/packs.json'}': expected 'packs' object",
                code="invalid_catalog",
                hint="Ensure 'catalog/packs.json' contains a JSON object under 'packs'.",
            )
        if pack not in packs:
            raise FredCliError(
                f"Unknown pack {pack!r}; use fred.py packs",
                code="unknown_pack",
                hint=f"Available packs: {', '.join(sorted(packs))}.",
            )
        pack_entry = packs[pack]
        if not isinstance(pack_entry, dict) or not isinstance(
            pack_entry.get("skills"), list
        ):
            raise FredCliError(
                f"Malformed pack '{pack}' in '{root / 'catalog/packs.json'}': expected 'skills' array",
                code="invalid_catalog",
                hint="Ensure each pack in 'catalog/packs.json' defines a 'skills' array.",
            )
        selected.extend(pack_entry["skills"])
    if not selected:
        raise FredCliError(
            "Select a pack or at least one --skill",
            code="missing_skill_selection",
            hint="Pass --pack <pack> or one or more --skill <name> flags (see 'python scripts/fred.py packs').",
        )
    selected = list(dict.fromkeys(selected))
    unknown = set(selected) - catalog
    if unknown:
        raise FredCliError(
            f"Unknown skills: {sorted(unknown)}",
            code="unknown_skill",
            hint="Run 'python scripts/fred.py search \"\"' to inspect valid skill names.",
        )
    return selected

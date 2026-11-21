"""Tests for standalone skill scaffolding, diff-scoped telemetry, attempt ledger, and guardrails."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import build_catalog
import build_experience
import run_checks
import scaffold_skill
import sync_audit_resources
import validate_skills
import verify_doc_snippets
import verify_workflow_actions

from tests.catalog_oracles import assert_module_ast_bounds, expected_total_skills

_PASSTHROUGH_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
)


def _git_env(tmp_dir: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV_KEYS if k in os.environ}
    home_dir = tmp_dir / ".git-home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test Author",
            "-c",
            "user.email=author@example.com",
            "-c",
            "core.autocrlf=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_synthetic_repo(root: Path) -> None:
    """Copy minimal schemas and a baseline compliant skill into a temporary repo."""
    (root / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "schemas/evaluation-scenarios.schema.json").write_bytes(
        (ROOT / "schemas/evaluation-scenarios.schema.json").read_bytes()
    )
    scaffold_skill.scaffold_skill("baseline-audit", root=root)


class EvaluationScenarioSchemaTests(unittest.TestCase):
    """Verify tightened evaluation-scenarios.schema.json suffix requirements."""

    def setUp(self) -> None:
        self.schema = json.loads(
            (ROOT / "schemas/evaluation-scenarios.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def _valid_payload(self) -> dict[str, Any]:
        return {
            "topic": "sample-check",
            "scenarios": [
                {
                    "id": "sample-check-failure",
                    "prompt": "Inspect sample module boundaries and report issues.",
                    "expected_behavior": ["Report defect with evidence."],
                    "evaluation_note": "Author fixture note; keep expected outcomes out of the prompt.",
                },
                {
                    "id": "sample-check-safe",
                    "prompt": "Inspect sample module boundaries and report issues.",
                    "expected_behavior": ["Confirm safeguards hold."],
                    "evaluation_note": "Safe control note; keep expected outcomes out of the prompt.",
                },
                {
                    "id": "sample-check-routing",
                    "prompt": "Inspect neighboring boundary contracts across module interfaces.",
                    "sibling_skill": "repo-audit-invariants",
                    "expected_behavior": [
                        "Route to repo-audit-invariants instead of sample-check."
                    ],
                    "evaluation_note": "Verify sibling routing to repo-audit-invariants.",
                },
            ],
        }

    def test_valid_three_suffix_scenarios_pass(self) -> None:
        payload = self._valid_payload()
        errors = list(self.validator.iter_errors(payload))
        self.assertEqual([], errors)

    def test_omitting_failure_safe_or_routing_suffix_is_rejected(self) -> None:
        for omitted_idx, replacement_id in (
            (0, "sample-check-safe"),
            (1, "sample-check-routing"),
            (2, "sample-check-failure"),
        ):
            with self.subTest(omitted_idx=omitted_idx, replacement_id=replacement_id):
                payload = self._valid_payload()
                scenarios = list(payload["scenarios"])
                updated = {
                    **scenarios[omitted_idx],
                    "id": replacement_id,
                }
                if replacement_id.endswith("-routing"):
                    updated["sibling_skill"] = "repo-audit-invariants"
                scenarios[omitted_idx] = updated
                payload["scenarios"] = scenarios
                errors = list(self.validator.iter_errors(payload))
                self.assertTrue(
                    len(errors) > 0,
                    f"Expected schema rejection when scenario {omitted_idx} became {replacement_id}",
                )

    def test_unrecognized_or_bare_suffix_is_rejected(self) -> None:
        for bad_id in ("sample-check-other", "-failure", "FAILURE"):
            with self.subTest(bad_id=bad_id):
                payload = self._valid_payload()
                scenarios = list(payload["scenarios"])
                scenarios[0] = {**scenarios[0], "id": bad_id}
                payload["scenarios"] = scenarios
                errors = list(self.validator.iter_errors(payload))
                self.assertTrue(len(errors) > 0)


class ScaffolderTests(unittest.TestCase):
    """Verify standalone and catalog-synced skill scaffolding."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "schemas").mkdir(parents=True)
        (self.root / "schemas/evaluation-scenarios.schema.json").write_bytes(
            (ROOT / "schemas/evaluation-scenarios.schema.json").read_bytes()
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_standalone_scaffolder_passes_validate_skill_and_cli_immediately(
        self,
    ) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/scaffold_skill.py"),
                "custom-boundary-check",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("PASS: scaffolded skill 'custom-boundary-check'", proc.stdout)

        pkg_dir = self.root / "skills/custom-boundary-check"
        self.assertTrue((pkg_dir / "SKILL.md").is_file())
        self.assertTrue((pkg_dir / "assets/prompt.md").is_file())
        self.assertTrue((pkg_dir / "assets/icon.svg").is_file())
        self.assertTrue(
            (pkg_dir / "assets/custom-boundary-check.schema.json").is_file()
        )
        self.assertTrue((pkg_dir / "agents/openai.yaml").is_file())
        self.assertTrue((pkg_dir / "references/evaluation-scenarios.json").is_file())

        self.assertEqual([], validate_skills.validate_skill(pkg_dir))
        errors, telemetry = validate_skills.validate_with_telemetry(self.root)
        self.assertEqual([], errors)
        self.assertEqual(1, telemetry["new_skills_count"])
        self.assertEqual(1, telemetry["new_skills_first_pass_count"])
        self.assertEqual(100.0, telemetry["new_skills_first_pass_percentage"])

    def test_scaffolder_enforces_ui_short_description_bounds_for_short_and_long_names(
        self,
    ) -> None:
        short_name = "ab"
        long_name = "a" * 64
        for skill_name in (short_name, long_name):
            scaffold_skill.scaffold_skill(skill_name, root=self.root)
            pkg_dir = self.root / "skills" / skill_name
            ui = yaml.safe_load(
                (pkg_dir / "agents/openai.yaml").read_text(encoding="utf-8")
            )["interface"]
            self.assertGreaterEqual(len(ui["short_description"]), 25)
            self.assertLessEqual(len(ui["short_description"]), 64)
            self.assertIn(f"${skill_name}", ui["default_prompt"])
            self.assertEqual([], validate_skills.validate_skill(pkg_dir))

    def test_scaffolder_dry_run_writes_nothing_and_refuses_overwrite_without_force(
        self,
    ) -> None:
        res = scaffold_skill.scaffold_skill(
            "preview-only-skill", root=self.root, dry_run=True
        )
        self.assertTrue(res["dry_run"])
        self.assertFalse((self.root / "skills/preview-only-skill").exists())

        scaffold_skill.scaffold_skill("existing-skill", root=self.root)
        with self.assertRaises(FileExistsError):
            scaffold_skill.scaffold_skill("existing-skill", root=self.root)

        overwritten = scaffold_skill.scaffold_skill(
            "existing-skill", root=self.root, force=True
        )
        self.assertTrue(overwritten["package_dir"].is_dir())

    def _populate_full_isolated_root(self) -> None:
        ignore_bytecode = shutil.ignore_patterns("__pycache__", "*.pyc")
        for rel_file in (
            "VERSION",
            ".pre-commit-config.yaml",
            "Makefile",
            "pyproject.toml",
            "requirements-dev.txt",
        ):
            src = ROOT / rel_file
            if src.is_file():
                (self.root / rel_file).write_bytes(src.read_bytes())
        for root_md in sorted(ROOT.glob("*.md")):
            if root_md.is_file():
                (self.root / root_md.name).write_bytes(root_md.read_bytes())
        for rel_dir in (
            "catalog",
            "docs",
            "evals",
            "examples",
            "schemas",
            "scripts",
            "shared",
            "skills",
            ".github",
        ):
            src_dir = ROOT / rel_dir
            if src_dir.is_dir():
                shutil.copytree(
                    src_dir,
                    self.root / rel_dir,
                    dirs_exist_ok=True,
                    ignore=ignore_bytecode,
                )

    def test_standalone_scaffolder_syncs_suite_companions_and_formats_cleanly(
        self,
    ) -> None:
        ignore_bytecode = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "shared", self.root / "shared", ignore=ignore_bytecode)
        shutil.copytree(ROOT / "scripts", self.root / "scripts", ignore=ignore_bytecode)
        shutil.copytree(
            ROOT / "schemas",
            self.root / "schemas",
            dirs_exist_ok=True,
            ignore=ignore_bytecode,
        )
        for rel_file in ("VERSION", "pyproject.toml"):
            dst = self.root / rel_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((ROOT / rel_file).read_bytes())

        dry_res = scaffold_skill.scaffold_skill(
            "repo-audit-dry-preview",
            root=self.root,
            suite="repository-audits",
            dry_run=True,
        )
        self.assertTrue(dry_res["dry_run"])
        self.assertFalse((self.root / "skills").exists())

        suite_specs: tuple[tuple[str, str | None, str, str], ...] = (
            (
                "repo-audit-rate-limits",
                "repository-audits",
                "assets/audit.schema.json",
                "scripts/validate_audit.py",
            ),
            (
                "repo-triage-regressions",
                "repository-workflows",
                "references/workflow-protocol.md",
                "scripts/validate_workflow.py",
            ),
            (
                "arch-service-contracts",
                "software-architecture",
                "assets/architecture.schema.json",
                "scripts/validate_architecture.py",
            ),
            (
                "review-api-changes",
                "code-review",
                "assets/review.schema.json",
                "scripts/validate_review.py",
            ),
            (
                "test-mutation-bounds",
                "automated-testing",
                "assets/testing.schema.json",
                "scripts/validate_testing.py",
            ),
            (
                "spec-acceptance-matrix",
                "spec-driven-development",
                "assets/spec.schema.json",
                "scripts/validate_spec.py",
            ),
            (
                "a" * 64,
                None,
                "assets/audit.schema.json",
                "scripts/validate_audit.py",
            ),
        )
        format_targets: list[str] = []
        for skill_name, suite_name, suite_resource, suite_validator in suite_specs:
            scaffold_skill.scaffold_skill(
                skill_name,
                root=self.root,
                suite=suite_name,
            )
            pkg_dir = self.root / "skills" / skill_name
            skill_md = (pkg_dir / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"]({suite_resource})", skill_md)
            self.assertIn(f"]({suite_validator})", skill_md)
            self.assertTrue((pkg_dir / suite_resource).is_file())
            self.assertTrue((pkg_dir / suite_validator).is_file())
            format_targets.extend(
                [
                    str(pkg_dir / "scripts/validate_output.py"),
                    str(pkg_dir / "SKILL.md"),
                    str(pkg_dir / "references/examples.md"),
                ]
            )

        ruff_bin = shutil.which("ruff")
        self.assertIsNotNone(ruff_bin, "ruff binary required in dev test environment")
        assert ruff_bin is not None
        check_proc = subprocess.run(
            [
                ruff_bin,
                "check",
                "--config",
                str(self.root / "pyproject.toml"),
                str(self.root / "skills"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0, check_proc.returncode, check_proc.stdout + check_proc.stderr
        )

        fmt_proc = subprocess.run(
            [
                ruff_bin,
                "format",
                "--check",
                "--config",
                str(self.root / "pyproject.toml"),
                *format_targets,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, fmt_proc.returncode, fmt_proc.stdout + fmt_proc.stderr)

        val_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(self.root),
                "--no-ledger",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, val_proc.returncode, val_proc.stdout + val_proc.stderr)

    def test_scaffolder_with_catalog_sync_passes_catalog_and_experience_checks(
        self,
    ) -> None:
        self._populate_full_isolated_root()
        version = (self.root / "VERSION").read_text(encoding="utf-8").strip()
        historical_notes = self.root / "docs/release-notes-0.2.0.md"
        historical_before = historical_notes.read_bytes()
        active_notes = self.root / f"docs/release-notes-{version}.md"
        self.assertTrue(active_notes.is_file())

        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts/scaffold_skill.py"),
                "repo-audit-rate-limits",
                "--catalog",
                "--suite",
                "repository-audits",
                "--phase",
                "2",
                "--symptom",
                "throttling",
                "--symptom",
                "quotas",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("synchronized suite, experience metadata", proc.stdout)

        self.assertEqual([], sync_audit_resources.sync(self.root, check=True))
        self.assertEqual([], build_catalog.build(self.root, check=True))
        self.assertEqual([], build_experience.build(self.root, check=True))
        baseline_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(baseline_skills, 1)
        errors, telemetry = validate_skills.validate_with_telemetry(
            self.root, record_attempts=False
        )
        self.assertEqual([], errors)
        self.assertEqual(baseline_skills + 1, telemetry["total_packages"])

        extra_specs = (
            ("arch-service-contracts", "software-architecture"),
            ("review-api-changes", "code-review"),
            ("test-mutation-bounds", "automated-testing"),
            ("spec-acceptance-matrix", "spec-driven-development"),
            ("repo-triage-regressions", "repository-workflows"),
        )
        for extra_name, extra_suite in extra_specs:
            scaffold_skill.scaffold_skill(
                extra_name,
                root=self.root,
                sync_catalog=True,
                suite=extra_suite,
            )

        expected_total = baseline_skills + 1 + len(extra_specs)
        self.assertEqual([], sync_audit_resources.sync(self.root, check=True))
        self.assertEqual([], build_catalog.build(self.root, check=True))
        self.assertEqual([], build_experience.build(self.root, check=True))

        packs = json.loads(
            (self.root / "catalog/packs.json").read_text(encoding="utf-8")
        )["packs"]
        all_new = ["repo-audit-rate-limits", *(n for n, _ in extra_specs)]
        for sk_name in all_new:
            self.assertIn(sk_name, packs["complete"]["skills"])
        self.assertIn(f"({expected_total} skills;", packs["complete"]["description"])
        self.assertIn("repo-audit-rate-limits", packs["full-audit"]["skills"])
        for non_audit_name, _ in extra_specs:
            self.assertNotIn(non_audit_name, packs["full-audit"]["skills"])

        self.assertIn(
            f"Total: {expected_total} skills.",
            active_notes.read_text(encoding="utf-8"),
        )
        self.assertIn(
            f"**{expected_total} standalone skills**",
            (self.root / "README.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f"exposes all {expected_total} skills",
            (self.root / "docs/integration.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(historical_before, historical_notes.read_bytes())

        ruff_bin = shutil.which("ruff")
        self.assertIsNotNone(ruff_bin, "ruff binary required in dev test environment")
        assert ruff_bin is not None
        new_pkg_targets = [
            str(p)
            for sk_name in all_new
            for p in (
                self.root / "skills" / sk_name / "scripts/validate_output.py",
                self.root / "skills" / sk_name / "SKILL.md",
                self.root / "skills" / sk_name / "references/examples.md",
            )
        ]
        ruff_check_proc = subprocess.run(
            [
                ruff_bin,
                "check",
                "--config",
                str(self.root / "pyproject.toml"),
                *(str(self.root / "skills" / sk_name) for sk_name in all_new),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            ruff_check_proc.returncode,
            ruff_check_proc.stdout + ruff_check_proc.stderr,
        )
        ruff_fmt_proc = subprocess.run(
            [
                ruff_bin,
                "format",
                "--check",
                "--config",
                str(self.root / "pyproject.toml"),
                *new_pkg_targets,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0, ruff_fmt_proc.returncode, ruff_fmt_proc.stdout + ruff_fmt_proc.stderr
        )

        val_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts/validate_skills.py"),
                "--root",
                str(self.root),
                "--no-ledger",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, val_proc.returncode, val_proc.stdout + val_proc.stderr)

        doc_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts/verify_doc_snippets.py"),
                "--root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, doc_proc.returncode, doc_proc.stdout + doc_proc.stderr)

        all_errors, all_telemetry = validate_skills.validate_with_telemetry(
            self.root, record_attempts=False
        )
        self.assertEqual([], all_errors)
        self.assertEqual(expected_total, all_telemetry["total_packages"])

    def test_scaffolder_catalog_sync_in_minimal_root(self) -> None:
        scaffold_skill.scaffold_skill(
            "minimal-workflow-skill",
            root=self.root,
            sync_catalog=True,
        )
        self.assertEqual([], build_catalog.build(self.root, check=True))
        self.assertEqual([], build_experience.build(self.root, check=True))
        errors, telemetry = validate_skills.validate_with_telemetry(self.root)
        self.assertEqual([], errors)
        self.assertEqual(1, telemetry["total_packages"])

    def test_scaffold_skill_cyclomatic_complexity_and_nesting_bounds(self) -> None:
        assert_module_ast_bounds(
            self,
            ROOT / "scripts/scaffold_skill.py",
            max_complexity=10,
            max_nesting=4,
        )


class DiffTelemetryAndLedgerTests(unittest.TestCase):
    """Verify git-diff scoping, attempt ledger history, and dedicated telemetry metrics."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir(parents=True)
        self.env = _git_env(Path(self.temp.name))
        _init_synthetic_repo(self.repo)
        _git(self.repo, self.env, "init")
        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Initial baseline skill")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_invalid_new_skill_reports_zero_first_pass_and_preserves_ledger_after_fix(
        self,
    ) -> None:
        # Scaffold a valid second skill so baseline has 2 valid committed skills
        scaffold_skill.scaffold_skill("second-baseline", root=self.repo)
        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Add second baseline skill")

        # Author creates an invalid uncommitted new skill (missing boundary section & output schema)
        bad_dir = self.repo / "skills/broken-new-skill"
        bad_dir.mkdir(parents=True)
        (bad_dir / "SKILL.md").write_text(
            '---\nname: broken-new-skill\ndescription: "Broken skill fixture."\n---\n\n# Broken\n\nNo boundary headings.\n',
            encoding="utf-8",
        )

        # Run 1: invalid new skill -> 0.0% new-skill first-pass rate while whole-repo is 2/3 (66.67%)
        errors_1, telemetry_1 = validate_skills.validate_with_telemetry(self.repo)
        self.assertTrue(len(errors_1) > 0)
        self.assertEqual(3, telemetry_1["total_packages"])
        self.assertEqual(2, telemetry_1["passed_packages"])
        self.assertEqual(1, telemetry_1["failed_packages"])
        self.assertEqual(
            66.67, telemetry_1["repository_guardrail_pass_rate_percentage"]
        )
        self.assertEqual(1, telemetry_1["new_skills_count"])
        self.assertEqual(0, telemetry_1["new_skills_passed"])
        self.assertEqual(0, telemetry_1["new_skills_first_pass_count"])
        self.assertEqual(0.0, telemetry_1["new_skills_first_pass_rate"])
        self.assertEqual(0.0, telemetry_1["new_skills_first_pass_percentage"])
        self.assertEqual(0.0, telemetry_1["first_pass_guardrail_pass_rate_percentage"])

        ledger_file = self.repo / ".fred-skills/validation-attempts.json"
        self.assertTrue(ledger_file.is_file())
        ledger_data_1 = json.loads(ledger_file.read_text(encoding="utf-8"))
        self.assertEqual(list(ledger_data_1["skills"].keys()), ["broken-new-skill"])
        self.assertIs(
            ledger_data_1["skills"]["broken-new-skill"]["first_pass_valid"], False
        )
        self.assertEqual(ledger_data_1["skills"]["broken-new-skill"]["attempts"], 1)

        # Fix the skill using the scaffolder (--force) and re-run validation
        scaffold_skill.scaffold_skill("broken-new-skill", root=self.repo, force=True)
        errors_2, telemetry_2 = validate_skills.validate_with_telemetry(self.repo)
        self.assertEqual([], errors_2)
        self.assertEqual(3, telemetry_2["total_packages"])
        self.assertEqual(3, telemetry_2["passed_packages"])
        self.assertEqual(0, telemetry_2["failed_packages"])
        self.assertEqual(
            100.0, telemetry_2["repository_guardrail_pass_rate_percentage"]
        )
        self.assertEqual(1, telemetry_2["new_skills_count"])
        self.assertEqual(1, telemetry_2["new_skills_passed"])
        self.assertEqual(0, telemetry_2["new_skills_first_pass_count"])
        self.assertEqual(0.0, telemetry_2["new_skills_first_pass_rate"])
        self.assertEqual(0.0, telemetry_2["new_skills_first_pass_percentage"])
        self.assertEqual(0.0, telemetry_2["first_pass_guardrail_pass_rate_percentage"])

        pkg_map = {p["name"]: p for p in telemetry_2["packages"]}
        self.assertIs(pkg_map["broken-new-skill"]["valid"], True)
        self.assertIs(pkg_map["broken-new-skill"]["is_new"], True)
        self.assertIs(pkg_map["broken-new-skill"]["first_pass_valid"], False)
        self.assertEqual(pkg_map["broken-new-skill"]["validation_attempts"], 2)
        self.assertIs(pkg_map["baseline-audit"]["is_new"], False)
        self.assertIsNone(pkg_map["baseline-audit"]["first_pass_valid"])

    def test_branch_diff_scoping_and_cli_step_summary_export(self) -> None:
        _git(self.repo, self.env, "checkout", "-b", "feat/new-skills")
        scaffold_skill.scaffold_skill("branch-valid-skill", root=self.repo)

        bad_branch_skill = self.repo / "skills/branch-invalid-skill"
        scaffold_skill.scaffold_skill("branch-invalid-skill", root=self.repo)
        # Break prompt budget on branch-invalid-skill
        (bad_branch_skill / "assets/prompt.md").write_text("X" * 1300, encoding="utf-8")

        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Add one valid and one invalid skill")

        telemetry_out = Path(self.temp.name) / "ci-telemetry.json"
        summary_out = Path(self.temp.name) / "step-summary.md"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(self.repo),
                "--base-ref",
                "main",
                "--no-ledger",
                "--telemetry",
                str(telemetry_out),
                "--step-summary",
                str(summary_out),
            ],
            env={**self.env, "GITHUB_STEP_SUMMARY": ""},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, proc.returncode)
        self.assertIn("New Skills: 2", proc.stdout)
        self.assertIn("New-skill First-pass Count: 1", proc.stdout)
        self.assertIn("New-skill First-pass Rate: 50.0%", proc.stdout)

        exported = json.loads(telemetry_out.read_text(encoding="utf-8"))
        self.assertEqual(3, exported["total_packages"])
        self.assertEqual(2, exported["new_skills_count"])
        self.assertEqual(1, exported["new_skills_first_pass_count"])
        self.assertEqual(50.0, exported["new_skills_first_pass_percentage"])

        summary_md = summary_out.read_text(encoding="utf-8")
        self.assertIn("### Skill Validation Telemetry", summary_md)
        self.assertIn("| Newly Authored Skills | 2 |", summary_md)
        self.assertIn("| New-Skill First-Pass Rate | **50.0%** |", summary_md)
        self.assertIn("`branch-valid-skill`", summary_md)
        self.assertIn("`branch-invalid-skill`", summary_md)

    def test_zero_sha_push_base_ref_falls_back_to_parent_commit(self) -> None:
        scaffold_skill.scaffold_skill("pushed-main-skill", root=self.repo)
        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Push new skill to main")

        errors, telemetry = validate_skills.validate_with_telemetry(
            self.repo,
            base_ref="0" * 40,
            record_attempts=False,
        )
        self.assertEqual([], errors)
        self.assertEqual(1, telemetry["new_skills_count"])
        self.assertEqual(1, telemetry["new_skills_first_pass_count"])
        self.assertEqual(100.0, telemetry["new_skills_first_pass_percentage"])

    def test_explicit_base_ref_fallback_on_root_unfetched_and_descendant_commits(
        self,
    ) -> None:
        # 1. Root commit: HEAD~1, <root_sha>~1, and <root_sha> fall back to initial-commit cleanly.
        root_sha = _git(self.repo, self.env, "rev-parse", "HEAD")
        for ref in ("HEAD~1", f"{root_sha}~1", root_sha, "deadbeef" * 5):
            with self.subTest(root_case=ref):
                errs, tel = validate_skills.validate_with_telemetry(
                    self.repo,
                    base_ref=ref,
                    record_attempts=False,
                )
                self.assertEqual([], errs)
                self.assertEqual("initial-commit", tel["diff_scope"])
                self.assertIsNone(tel["diff_base"])
                self.assertEqual(1, tel["new_skills_count"])

        # 2. Commit C2 adds target-tag-skill; commit C3 advances main with later-main-skill.
        scaffold_skill.scaffold_skill("target-tag-skill", root=self.repo)
        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Add target-tag-skill at C2")
        c2_sha = _git(self.repo, self.env, "rev-parse", "HEAD")

        scaffold_skill.scaffold_skill("later-main-skill", root=self.repo)
        _git(self.repo, self.env, "add", ".")
        _git(self.repo, self.env, "commit", "-m", "Add later-main-skill at C3")
        c3_sha = _git(self.repo, self.env, "rev-parse", "HEAD")

        # Force-updated v* tag with unfetched github.event.before SHA exits 0 and diffs against HEAD~1.
        unfetched_sha = "f00dbabe" * 5
        cli_unfetched = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(self.repo),
                "--base-ref",
                unfetched_sha,
                "--no-ledger",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, cli_unfetched.returncode, cli_unfetched.stderr)
        self.assertIn("New Skills: 1", cli_unfetched.stdout)

        # Older target_sha checkout (HEAD == C2) diffs C2 against C2~1 (root_sha) for both C2~1 and descendant C3.
        _git(self.repo, self.env, "checkout", "--detach", c2_sha)
        for older_ref in (f"{c2_sha}~1", c3_sha, "main"):
            with self.subTest(older_ref=older_ref):
                errs_c2, tel_c2 = validate_skills.validate_with_telemetry(
                    self.repo,
                    base_ref=older_ref,
                    record_attempts=False,
                )
                self.assertEqual([], errs_c2)
                self.assertEqual("git-diff", tel_c2["diff_scope"])
                self.assertEqual(root_sha, tel_c2["diff_base"])
                self.assertEqual(1, tel_c2["new_skills_count"])
                new_names = {p["name"] for p in tel_c2["packages"] if p["is_new"]}
                self.assertEqual({"target-tag-skill"}, new_names)

    def test_single_pass_inspection_eliminates_rereads_and_preserves_telemetry_parity(
        self,
    ) -> None:
        from collections import Counter
        from unittest.mock import patch

        scaffold_skill.scaffold_skill("fresh-valid-skill", root=self.repo)

        partial_dir = self.repo / "skills/partial-invalid-skill"
        scaffold_skill.scaffold_skill("partial-invalid-skill", root=self.repo)
        (partial_dir / "assets/prompt.md").write_text(
            "Use partial-invalid-skill on <repository> at <revision> conforming to "
            "assets/partial-invalid-skill.schema.json.\n",
            encoding="utf-8",
        )

        broken_fm_dir = self.repo / "skills/broken-frontmatter-skill"
        broken_fm_dir.mkdir(parents=True)
        (broken_fm_dir / "SKILL.md").write_text(
            "unterminated frontmatter\n", encoding="utf-8"
        )

        total_read_counts: Counter[Path] = Counter()
        validator_read_counts: Counter[Path] = Counter()
        frontmatter_counts: Counter[Path] = Counter()
        orig_read_text = Path.read_text
        orig_read_frontmatter = validate_skills.read_frontmatter
        validator_filename = Path(validate_skills.__file__).resolve()

        def tracked_read_text(path_obj: Path, *args: Any, **kwargs: Any) -> str:
            resolved = path_obj.resolve()
            total_read_counts[resolved] += 1
            caller = Path(sys._getframe(1).f_code.co_filename).resolve()
            if caller == validator_filename:
                validator_read_counts[resolved] += 1
            return orig_read_text(path_obj, *args, **kwargs)

        def tracked_read_frontmatter(path_obj: Path) -> tuple[dict[str, Any], str]:
            frontmatter_counts[path_obj.resolve()] += 1
            return orig_read_frontmatter(path_obj)

        with (
            patch.object(Path, "read_text", new=tracked_read_text),
            patch.object(
                validate_skills,
                "read_frontmatter",
                side_effect=tracked_read_frontmatter,
            ),
        ):
            errors, telemetry = validate_skills.validate_with_telemetry(self.repo)

        for pkg_name in (
            "baseline-audit",
            "fresh-valid-skill",
            "partial-invalid-skill",
            "broken-frontmatter-skill",
        ):
            pkg_dir = (self.repo / "skills" / pkg_name).resolve()
            self.assertEqual(
                1,
                frontmatter_counts[pkg_dir / "SKILL.md"],
                f"SKILL.md frontmatter parsed multiple times for {pkg_name}",
            )
            self.assertEqual(
                0,
                validator_read_counts[pkg_dir / "SKILL.md"],
                f"validate_skills.py re-read SKILL.md directly for {pkg_name}",
            )
            prompt_file = pkg_dir / "assets/prompt.md"
            if prompt_file.is_file():
                self.assertEqual(
                    1,
                    validator_read_counts[prompt_file.resolve()],
                    f"validate_skills.py read assets/prompt.md multiple times for {pkg_name}",
                )
            for rel_asset in (
                f"assets/{pkg_name}.schema.json",
                "references/evaluation-scenarios.json",
                "agents/openai.yaml",
            ):
                asset_path = pkg_dir / rel_asset
                if asset_path.is_file():
                    self.assertEqual(
                        1,
                        total_read_counts[asset_path.resolve()],
                        f"Duplicate file read detected for {pkg_name}/{rel_asset}",
                    )

        expected_top_keys = {
            "timestamp",
            "diff_scope",
            "diff_base",
            "total_packages",
            "passed_packages",
            "failed_packages",
            "repository_guardrail_pass_rate",
            "repository_guardrail_pass_rate_percentage",
            "new_skills_count",
            "new_skills_passed",
            "new_skills_first_pass_count",
            "new_skills_first_pass_rate",
            "new_skills_first_pass_percentage",
            "first_pass_guardrail_pass_rate",
            "first_pass_guardrail_pass_rate_percentage",
            "deterministic_constraint_coverage",
            "deterministic_constraint_coverage_percentage",
            "output_schema_coverage",
            "output_schema_coverage_percentage",
            "routing_trigger_coverage",
            "routing_trigger_coverage_percentage",
            "skill_worked_example_coverage",
            "skill_worked_example_coverage_percentage",
            "validator_script_coverage",
            "validator_script_coverage_percentage",
            "method_boilerplate_duplication_rate",
            "method_boilerplate_duplication_percentage",
            "execution_time_seconds",
            "packages",
        }
        self.assertEqual(expected_top_keys, set(telemetry.keys()))
        self.assertEqual("git-diff", telemetry["diff_scope"])
        self.assertEqual(4, telemetry["total_packages"])
        self.assertEqual(2, telemetry["passed_packages"])
        self.assertEqual(2, telemetry["failed_packages"])
        self.assertEqual(0.5, telemetry["repository_guardrail_pass_rate"])
        self.assertEqual(50.0, telemetry["repository_guardrail_pass_rate_percentage"])
        self.assertEqual(3, telemetry["new_skills_count"])
        self.assertEqual(1, telemetry["new_skills_passed"])
        self.assertEqual(1, telemetry["new_skills_first_pass_count"])
        self.assertEqual(0.3333, telemetry["new_skills_first_pass_rate"])
        self.assertEqual(33.33, telemetry["new_skills_first_pass_percentage"])
        self.assertEqual(0.3333, telemetry["first_pass_guardrail_pass_rate"])
        self.assertEqual(33.33, telemetry["first_pass_guardrail_pass_rate_percentage"])
        self.assertEqual(0.5, telemetry["deterministic_constraint_coverage"])
        self.assertEqual(
            50.0, telemetry["deterministic_constraint_coverage_percentage"]
        )
        self.assertEqual(0.75, telemetry["output_schema_coverage"])
        self.assertEqual(75.0, telemetry["output_schema_coverage_percentage"])
        self.assertEqual(0.75, telemetry["skill_worked_example_coverage"])
        self.assertEqual(75.0, telemetry["skill_worked_example_coverage_percentage"])
        self.assertEqual(0.75, telemetry["validator_script_coverage"])
        self.assertEqual(75.0, telemetry["validator_script_coverage_percentage"])
        self.assertEqual(0.0, telemetry["method_boilerplate_duplication_rate"])
        self.assertEqual(0.0, telemetry["method_boilerplate_duplication_percentage"])

        pkg_by_name = {pkg["name"]: pkg for pkg in telemetry["packages"]}
        for pkg_name, pkg_entry in pkg_by_name.items():
            direct_errors = validate_skills.validate_skill(
                self.repo / "skills" / pkg_name
            )
            self.assertEqual(direct_errors, pkg_entry["errors"])
            for err in direct_errors:
                self.assertIn(f"{pkg_name}: {err}", errors)

        self.assertEqual(
            {
                "name": "partial-invalid-skill",
                "valid": False,
                "is_new": True,
                "first_pass_valid": False,
                "validation_attempts": 1,
                "has_output_schema": True,
                "output_schemas": ["assets/partial-invalid-skill.schema.json"],
                "worked_example_blocks": 2,
                "has_worked_examples": True,
                "has_validator_script": True,
                "has_prompt_constraints": False,
                "has_boundary_sections": True,
                "deterministic_constraint_coverage": False,
                "errors": pkg_by_name["partial-invalid-skill"]["errors"],
            },
            pkg_by_name["partial-invalid-skill"],
        )
        self.assertEqual(
            {
                "name": "broken-frontmatter-skill",
                "valid": False,
                "is_new": True,
                "first_pass_valid": False,
                "validation_attempts": 1,
                "has_output_schema": False,
                "output_schemas": [],
                "worked_example_blocks": 0,
                "has_worked_examples": False,
                "has_validator_script": False,
                "has_prompt_constraints": False,
                "has_boundary_sections": False,
                "deterministic_constraint_coverage": False,
                "errors": pkg_by_name["broken-frontmatter-skill"]["errors"],
            },
            pkg_by_name["broken-frontmatter-skill"],
        )

        md = validate_skills.format_telemetry_markdown(telemetry)
        self.assertIn("| New-Skill First-Pass Rate | **33.33%** |", md)
        self.assertIn("| Output Schema Coverage | 75.0% |", md)

    def test_attempt_ledger_surfaces_corrupted_json_and_writes_atomically(self) -> None:
        from unittest.mock import patch

        from fredlib.common import FredCliError, atomic_json

        missing_ledger = self.repo / ".fred-skills/validation-attempts.json"
        self.assertEqual({}, validate_skills._load_attempt_ledger(missing_ledger))

        # Write atomically via atomic_json with zero leftover temp files
        with patch.object(
            validate_skills, "atomic_json", wraps=atomic_json
        ) as spy_atomic:
            validate_skills._save_attempt_ledger(
                missing_ledger,
                {"sample-new": {"first_pass_valid": True, "attempts": 1}},
            )
            spy_atomic.assert_called_once()
        self.assertTrue(missing_ledger.is_file())
        self.assertEqual(
            [],
            list(missing_ledger.parent.glob(".fred-*")),
        )

        # Truncated JSON in validation-attempts.json raises FredCliError without wiping file
        truncated_raw = '{\n  "schema_version": "1.0",\n  "skills": {\n'
        missing_ledger.write_text(truncated_raw, encoding="utf-8")
        with self.assertRaises(FredCliError) as json_ctx:
            validate_skills._load_attempt_ledger(missing_ledger)
        self.assertEqual("invalid_json", json_ctx.exception.code)
        self.assertIn("validation-attempts.json", json_ctx.exception.message)

        with self.assertRaises(FredCliError) as val_ctx:
            validate_skills.validate_with_telemetry(self.repo)
        self.assertEqual("invalid_json", val_ctx.exception.code)
        self.assertEqual(truncated_raw, missing_ledger.read_text(encoding="utf-8"))

        # CLI surfaces structured error and exits 2, while --no-ledger bypasses ledger read
        bad_cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(self.repo),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, bad_cli.returncode)
        self.assertIn("[invalid_json]", bad_cli.stderr)
        self.assertEqual(truncated_raw, missing_ledger.read_text(encoding="utf-8"))

        no_ledger_cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(self.repo),
                "--no-ledger",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, no_ledger_cli.returncode, no_ledger_cli.stderr)

        # Non-object root and missing 'skills' map also raise FredCliError
        missing_ledger.write_text("[]\n", encoding="utf-8")
        with self.assertRaises(FredCliError) as root_ctx:
            validate_skills._load_attempt_ledger(missing_ledger)
        self.assertEqual("invalid_json_root", root_ctx.exception.code)

        missing_ledger.write_text('{"schema_version": "1.0"}\n', encoding="utf-8")
        with self.assertRaises(FredCliError) as schema_ctx:
            validate_skills._load_attempt_ledger(missing_ledger)
        self.assertEqual("invalid_ledger", schema_ctx.exception.code)


class WorkflowAndContributingGuardrailTests(unittest.TestCase):
    """Verify CI workflow telemetry setup, action pins, and CONTRIBUTING.md guardrails."""

    def test_validate_workflow_checkout_depth_telemetry_and_artifact_upload(
        self,
    ) -> None:
        wf_path = ROOT / ".github/workflows/validate.yml"
        wf_data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        steps = wf_data["jobs"]["validate"]["steps"]

        checkout_step = steps[0]
        self.assertTrue(checkout_step["uses"].startswith("actions/checkout@"))
        self.assertEqual("${{ inputs.ref }}", checkout_step["with"]["ref"])
        self.assertEqual(0, checkout_step["with"]["fetch-depth"])

        pkg_step = next(
            s
            for s in steps
            if "scripts/run_checks.py --phase packages" in s.get("run", "")
        )
        self.assertEqual("bash", pkg_step.get("shell"))
        self.assertIn('--base-ref "$BASE_REF"', pkg_step["run"])
        self.assertNotIn("${{", pkg_step["run"])
        base_ref_expr = pkg_step.get("env", {}).get("BASE_REF", "")
        self.assertIn("inputs.ref != ''", base_ref_expr)
        self.assertIn("format('{0}~1', inputs.ref)", base_ref_expr)
        self.assertIn("github.event_name == 'pull_request'", base_ref_expr)
        self.assertIn("'HEAD~1'", base_ref_expr)
        self.assertIn("github.event.before", base_ref_expr)
        self.assertIn("--telemetry validation-telemetry.json", pkg_step["run"])
        self.assertIn("--step-summary", pkg_step["run"])

        upload_steps = [
            s
            for s in steps
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        self.assertEqual(1, len(upload_steps))
        self.assertEqual("always()", upload_steps[0].get("if"))
        self.assertEqual(
            "validation-telemetry.json",
            upload_steps[0]["with"]["path"],
        )

        # Offline SHA pin and lockfile verification must pass
        self.assertEqual([], verify_workflow_actions.verify_workflows(root=ROOT))

    def test_run_checks_forwards_optional_base_ref_and_step_summary_without_default_writes(
        self,
    ) -> None:
        default_cmds = run_checks.build_phase_commands("packages", root=ROOT)
        self.assertNotIn("--telemetry", default_cmds[-1])
        self.assertNotIn("--base-ref", default_cmds[-1])
        self.assertNotIn("--step-summary", default_cmds[-1])

        ci_cmds = run_checks.build_phase_commands(
            "packages",
            root=ROOT,
            telemetry_path="/tmp/out.json",
            base_ref="origin/main",
            step_summary="",
        )
        self.assertEqual(
            [
                "--base-ref",
                "origin/main",
                "--telemetry",
                "/tmp/out.json",
                "--step-summary",
            ],
            ci_cmds[-1][-5:],
        )

    def test_contributing_documents_all_seven_guardrails_and_passes_snippet_dry_runs(
        self,
    ) -> None:
        contributing_text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        required_markers = (
            "1200",
            "20",
            "<repository>",
            "<revision>",
            "Negative constraints",
            "Boundary headings",
            "assets/*.schema.json",
            "25–64",
            "-failure",
            "-safe",
            "-routing",
            "scripts/scaffold_skill.py",
        )
        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, contributing_text)

        snippet_errors = verify_doc_snippets.verify_all_snippets(
            root=ROOT, files=["CONTRIBUTING.md"]
        )
        self.assertEqual([], snippet_errors)


class FailClosedValidationAndScaffolderTests(unittest.TestCase):
    """Verify validate_skills and scaffold_skill fail closed on unreadable or malformed files."""

    def test_validate_skills_fails_closed_and_deduplicates_errors(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_synthetic_repo(repo)
            pkg = repo / "skills/baseline-audit"
            self.assertEqual([], validate_skills.validate_skill(pkg))

            # Absent catalog/suites.json is cleanly skipped
            self.assertFalse((repo / "catalog/suites.json").exists())
            self.assertEqual(
                [],
                validate_skills._validate_curated_pack_coverage(
                    repo, {"starter": {"skills": ["baseline-audit"]}}
                ),
            )

            # Corrupt catalog/suites.json reports errors in _validate_curated_pack_coverage,
            # _validate_evaluation_scenarios, and validate_skill without crashing
            suites_path = repo / "catalog/suites.json"
            suites_path.parent.mkdir(parents=True, exist_ok=True)
            for payload in (b"\xff\xfe", b"{bad json"):
                suites_path.write_bytes(payload)
                cov_errs = validate_skills._validate_curated_pack_coverage(
                    repo, {"starter": {"skills": ["baseline-audit"]}}
                )
                self.assertTrue(cov_errs)
                self.assertTrue(any("Invalid suite manifest" in e for e in cov_errs))

                scen_errs = validate_skills._validate_evaluation_scenarios(
                    pkg, "baseline-audit", None, {}
                )
                self.assertTrue(scen_errs)
                self.assertTrue(any("Invalid suite manifest" in e for e in scen_errs))

                pkg_errs = validate_skills.validate_skill(pkg)
                self.assertTrue(pkg_errs)
                self.assertEqual(len(pkg_errs), len(set(pkg_errs)))
            suites_path.unlink()

            # Corrupt references/evaluation-scenarios.json (invalid UTF-8, malformed JSON, PermissionError)
            scen_path = pkg / "references/evaluation-scenarios.json"
            orig_scen = scen_path.read_bytes()
            scen_path.write_bytes(b"\xff\xfe\x00")
            self.assertTrue(
                validate_skills._validate_evaluation_scenarios(
                    pkg, "baseline-audit", None, {}
                )
            )
            utf8_pkg_errs = validate_skills.validate_skill(pkg)
            self.assertTrue(utf8_pkg_errs)
            self.assertEqual(len(utf8_pkg_errs), len(set(utf8_pkg_errs)))

            scen_path.write_text("{bad json", encoding="utf-8")
            self.assertTrue(
                validate_skills._validate_evaluation_scenarios(
                    pkg, "baseline-audit", None, {}
                )
            )
            json_pkg_errs = validate_skills.validate_skill(pkg)
            self.assertTrue(json_pkg_errs)
            self.assertEqual(len(json_pkg_errs), len(set(json_pkg_errs)))
            scen_path.write_bytes(orig_scen)

            scen_resolved = scen_path.resolve()
            orig_read_text = Path.read_text

            def deny_scen(self_path: Path, *args: object, **kwargs: object) -> str:
                if self_path.resolve() == scen_resolved:
                    raise PermissionError("scenarios permission denied")
                return orig_read_text(self_path, *args, **kwargs)  # type: ignore[arg-type]

            with patch.object(Path, "read_text", autospec=True, side_effect=deny_scen):
                perm_scen_errs = validate_skills._validate_evaluation_scenarios(
                    pkg, "baseline-audit", None, {}
                )
                perm_pkg_errs = validate_skills.validate_skill(pkg)
            self.assertTrue(perm_scen_errs)
            self.assertTrue(perm_pkg_errs)
            self.assertEqual(len(perm_pkg_errs), len(set(perm_pkg_errs)))

            # Unreadable SKILL.md reports deduplicated error without crashing
            skill_md_resolved = (pkg / "SKILL.md").resolve()
            orig_read_bytes = Path.read_bytes

            def deny_skill_bytes(self_path: Path) -> bytes:
                if self_path.resolve() == skill_md_resolved:
                    raise PermissionError("SKILL.md permission denied")
                return orig_read_bytes(self_path)

            def deny_skill_text(
                self_path: Path, *args: object, **kwargs: object
            ) -> str:
                if self_path.resolve() == skill_md_resolved:
                    raise PermissionError("SKILL.md permission denied")
                return orig_read_text(self_path, *args, **kwargs)  # type: ignore[arg-type]

            with (
                patch.object(
                    Path, "read_bytes", autospec=True, side_effect=deny_skill_bytes
                ),
                patch.object(
                    Path, "read_text", autospec=True, side_effect=deny_skill_text
                ),
            ):
                skill_perm_errs = validate_skills.validate_skill(pkg)
            self.assertEqual(1, len(skill_perm_errs))
            self.assertIn("SKILL.md", skill_perm_errs[0])

    def test_scaffold_skill_raises_fred_cli_error_on_corrupt_manifest_or_sibling_and_preserves_force(
        self,
    ) -> None:
        from unittest.mock import patch

        from skill_tools import FredCliError

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_synthetic_repo(repo)

            # Absent shared/*/manifest.json is a clean no-op
            (repo / "shared/empty-bundle").mkdir(parents=True)
            scaffold_skill.scaffold_skill("second-audit", root=repo)

            # Corrupt shared/*/manifest.json raises FredCliError (ValueError)
            manifest_path = repo / "shared/empty-bundle/manifest.json"
            for bad_bytes, expected_code in (
                (b"\xff\xfe", "invalid_encoding"),
                (b"{bad", "invalid_json"),
                (b"[]", "invalid_json_root"),
            ):
                manifest_path.write_bytes(bad_bytes)
                with self.assertRaises(FredCliError) as man_ctx:
                    scaffold_skill.scaffold_skill("third-audit", root=repo)
                self.assertIsInstance(man_ctx.exception, ValueError)
                self.assertEqual(expected_code, man_ctx.exception.code)
            manifest_path.unlink()

            # Broken sibling SKILL.md (including yaml.YAMLError, invalid UTF-8, PermissionError)
            sibling_md = repo / "skills/second-audit/SKILL.md"
            sibling_md.write_text(
                "---\nname: [unclosed\n---\n# Body\n", encoding="utf-8"
            )
            with self.assertRaises(FredCliError) as yaml_ctx:
                scaffold_skill.scaffold_skill("third-audit", root=repo)
            self.assertIsInstance(yaml_ctx.exception, ValueError)
            self.assertEqual("invalid_frontmatter", yaml_ctx.exception.code)

            # CLI exits cleanly with status 1 instead of uncaught yaml.YAMLError
            rc = scaffold_skill.main(["third-audit", "--root", str(repo)])
            self.assertEqual(1, rc)

            sibling_md.write_bytes(b"\xff\xfe\x00")
            with self.assertRaises(FredCliError) as enc_ctx:
                scaffold_skill.scaffold_skill("third-audit", root=repo)
            self.assertEqual("invalid_encoding", enc_ctx.exception.code)

            # --force skips target directory itself during sibling inspection and overwrites broken SKILL.md
            scaffold_skill.scaffold_skill("second-audit", root=repo, force=True)
            self.assertEqual(
                [], validate_skills.validate_skill(repo / "skills/second-audit")
            )

            sibling_resolved = sibling_md.resolve()
            orig_read_text = Path.read_text

            def deny_sibling(self_path: Path, *args: object, **kwargs: object) -> str:
                if self_path.resolve() == sibling_resolved:
                    raise PermissionError("sibling SKILL.md permission denied")
                return orig_read_text(self_path, *args, **kwargs)  # type: ignore[arg-type]

            with (
                patch.object(
                    Path, "read_text", autospec=True, side_effect=deny_sibling
                ),
                self.assertRaises(FredCliError) as io_ctx,
            ):
                scaffold_skill.scaffold_skill("third-audit", root=repo)
            self.assertEqual("io_error", io_ctx.exception.code)


if __name__ == "__main__":
    unittest.main()

"""Tests for standalone skill scaffolding, diff-scoped telemetry, attempt ledger, and guardrails."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
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

    def test_scaffolder_with_catalog_sync_passes_catalog_and_experience_checks(
        self,
    ) -> None:
        # Copy real catalog and shared manifests into isolated root to test full drift-free sync
        for rel_file in (
            "VERSION",
            "scripts/audit_to_sarif.py",
            "catalog/suites.json",
            "catalog/experience.json",
            "catalog/recipes.json",
            "catalog/packs.json",
            "catalog/hosts.json",
        ):
            src = ROOT / rel_file
            dst = self.root / rel_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())

        import shutil

        shutil.copytree(ROOT / "shared", self.root / "shared")
        shutil.copytree(ROOT / "skills", self.root / "skills")

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/scaffold_skill.py"),
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
        errors, telemetry = validate_skills.validate_with_telemetry(self.root)
        self.assertEqual([], errors)
        self.assertEqual(52, telemetry["total_packages"])

        for extra_name, extra_suite in (
            ("arch-service-contracts", "software-architecture"),
            ("review-api-changes", "code-review"),
            ("test-mutation-bounds", "automated-testing"),
            ("repo-triage-regressions", "repository-workflows"),
        ):
            scaffold_skill.scaffold_skill(
                extra_name,
                root=self.root,
                sync_catalog=True,
                suite=extra_suite,
            )
        self.assertEqual([], sync_audit_resources.sync(self.root, check=True))
        self.assertEqual([], build_catalog.build(self.root, check=True))
        self.assertEqual([], build_experience.build(self.root, check=True))
        all_errors, all_telemetry = validate_skills.validate_with_telemetry(self.root)
        self.assertEqual([], all_errors)
        self.assertEqual(56, all_telemetry["total_packages"])

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
        self.assertIn("--base-ref", pkg_step["run"])
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


if __name__ == "__main__":
    unittest.main()

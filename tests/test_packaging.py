"""Negative package fixtures and generated-artifact drift checks."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_catalog import build
from package_skills import get_suite_skills
from skill_tools import build_outputs
from sync_audit_resources import sync
from validate_skills import validate_skill, validate_with_telemetry


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/sample-audit"
        self.skill.mkdir(parents=True)
        self.entry = self.skill / "SKILL.md"
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n# Sample\n\nInspect the supplied contract.\n\n## Deliver\n\nDeliver the audit output.\n'
        )
        assets_dir = self.skill / "assets"
        assets_dir.mkdir(parents=True)
        self.output_schema = assets_dir / "audit.schema.json"
        self.output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                }
            ),
            encoding="utf-8",
        )
        self.prompt = assets_dir / "prompt.md"
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision>. Do not modify application code without authorization.\n",
            encoding="utf-8",
        )
        self.scenarios_file = self.skill / "references/evaluation-scenarios.json"
        self.scenarios_file.parent.mkdir(parents=True, exist_ok=True)
        self.scenarios_file.write_text(
            json.dumps(
                {
                    "topic": "sample-audit",
                    "scenarios": [
                        {
                            "id": "sample-audit-failure",
                            "prompt": "Audit synthetic contracts when explicitly requested.",
                            "expected_behavior": ["Report finding with evidence."],
                        },
                        {
                            "id": "sample-audit-safe",
                            "prompt": "Audit synthetic contracts when explicitly requested.",
                            "expected_behavior": ["Safeguards verified, no defect."],
                        },
                        {
                            "id": "sample-audit-routing",
                            "prompt": "Fix a spelling mistake in the project description.",
                            "expected_behavior": [
                                "Do not activate this specialist solely because the task concerns a repository."
                            ],
                        },
                    ],
                }
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self):
        (self.root / "catalog").mkdir(exist_ok=True)
        document = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": "repository-audits",
                    "phases": [
                        {"id": 1, "title": "Discovery", "skills": ["sample-audit"]}
                    ],
                }
            ],
        }
        (self.root / "catalog/suites.json").write_text(json.dumps(document))

    def test_minimal_portable_package_is_valid(self):
        self.assertEqual([], validate_skill(self.skill))

    def test_missing_evaluation_scenarios_is_rejected(self):
        self.scenarios_file.unlink()
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("references/evaluation-scenarios.json" in error for error in errors)
        )

    def test_malformed_evaluation_scenarios_is_rejected(self):
        self.scenarios_file.write_text('{"topic": "sample-audit"}')
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("references/evaluation-scenarios.json" in error for error in errors)
        )

    def test_name_mismatch_is_rejected(self):
        self.entry.write_text(
            self.entry.read_text().replace("name: sample-audit", "name: different-name")
        )
        self.assertTrue(
            any("mismatch" in error for error in validate_skill(self.skill))
        )

    def test_missing_reference_and_package_escape_are_rejected(self):
        self.entry.write_text(
            self.entry.read_text()
            + "\n[Missing](references/absent.md)\n[Outside](../../outside.md)\n"
        )
        (self.root / "outside.md").write_text("Outside package")
        errors = validate_skill(self.skill)
        self.assertTrue(any("Missing relative" in error for error in errors))
        self.assertTrue(any("escapes standalone" in error for error in errors))

    def test_frontmatter_and_schema_failures_are_reported(self):
        self.entry.write_text("# No frontmatter\n")
        self.assertTrue(validate_skill(self.skill))

    def test_invalid_json_schema_is_rejected(self):
        (self.skill / "bad.schema.json").write_text('{"type": "invented-type"}')
        self.assertTrue(validate_skill(self.skill))

    def test_external_links_and_local_fragments_are_allowed(self):
        self.entry.write_text(
            self.entry.read_text()
            + "\n[External](https://example.invalid/docs)\n[Local](#sample)\n"
        )
        self.assertEqual([], validate_skill(self.skill))

    def test_skill_markdown_supports_html_images_and_reference_links(self):
        references_dir = self.skill / "references"
        references_dir.mkdir(parents=True, exist_ok=True)
        (references_dir / "valid.md").write_text("# Valid\n", encoding="utf-8")
        self.entry.write_text(
            self.entry.read_text()
            + "\n![Diagram](references/valid.md)\n"
            + '<a href="references/valid.md#anchor?key=1">Valid</a>\n'
            + "[ref]: references/valid.md\n"
        )
        self.assertEqual([], validate_skill(self.skill))

    def test_skill_markdown_detects_broken_html_and_reference_links(self):
        self.entry.write_text(
            self.entry.read_text()
            + '\n<a href="references/missing.md">Missing</a>\n'
            + "[ref]: references/missing_ref.md\n"
            + "![Image](references/missing.png)\n"
        )
        errors = validate_skill(self.skill)
        self.assertTrue(any("references/missing.md" in error for error in errors))
        self.assertTrue(any("references/missing_ref.md" in error for error in errors))
        self.assertTrue(any("references/missing.png" in error for error in errors))

    def test_symlinks_are_not_standalone_packages(self):
        try:
            (self.skill / "linked.md").symlink_to(self.entry)
        except OSError:
            self.skipTest("Symlinks unavailable on this host")
        self.assertTrue(any("Symlink" in error for error in validate_skill(self.skill)))

    def test_catalog_is_deterministic_and_detects_drift(self):
        self.manifest()
        self.assertEqual([], build(self.root))
        first = (self.root / "catalog/skills.json").read_bytes()
        first_marketplace = (self.root / ".claude-plugin/marketplace.json").read_bytes()
        self.assertEqual([], build(self.root, check=True))
        build(self.root)
        self.assertEqual(first, (self.root / "catalog/skills.json").read_bytes())
        self.assertEqual(
            first_marketplace,
            (self.root / ".claude-plugin/marketplace.json").read_bytes(),
        )
        self.entry.write_text(
            self.entry.read_text().replace("synthetic contracts", "revised contracts")
        )
        problems = build(self.root, check=True)
        self.assertTrue(problems)
        self.assertTrue(
            any(".claude-plugin/marketplace.json" in problem for problem in problems)
        )

    def test_marketplace_drift_is_detected(self):
        self.manifest()
        self.assertEqual([], build(self.root))
        marketplace_path = self.root / ".claude-plugin/marketplace.json"
        self.assertTrue(marketplace_path.is_file())
        marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        problems = build(self.root, check=True)
        self.assertTrue(
            any(".claude-plugin/marketplace.json" in problem for problem in problems)
        )

    def test_build_outputs_helper_creates_files_and_detects_drift(self):
        target_file = "generated/sample.txt"
        content = "hello world\nline 2\n"
        out = {target_file: content}
        drift = build_outputs(out, root=self.root, check=True)
        self.assertEqual([f"Stale generated file: {target_file}"], drift)
        self.assertEqual([], build_outputs(out, root=self.root, check=False))
        created_path = self.root / target_file
        self.assertTrue(created_path.is_file())
        self.assertEqual(content.encode("utf-8"), created_path.read_bytes())
        self.assertEqual([], build_outputs(out, root=self.root, check=True))
        created_path.write_text("tampered\n", encoding="utf-8")
        drift = build_outputs(out, root=self.root, check=True)
        self.assertEqual([f"Stale generated file: {target_file}"], drift)

    def test_build_outputs_exported_from_skill_tools(self):
        import skill_tools

        self.assertIn("build_outputs", skill_tools.__all__)
        self.assertIs(build_outputs, skill_tools.build_outputs)

    def test_marketplace_manifest_includes_all_suites(self):
        workflow_skill = self.root / "skills/sample-workflow"
        workflow_skill.mkdir(parents=True)
        (workflow_skill / "SKILL.md").write_text(
            '---\nname: sample-workflow\ndescription: "Workflow package."\n---\n\n# Workflow\n'
        )
        (self.root / "catalog").mkdir(exist_ok=True)
        document = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": "repository-audits",
                    "phases": [
                        {"id": 1, "title": "Discovery", "skills": ["sample-audit"]}
                    ],
                },
                {
                    "name": "repository-workflows",
                    "phases": [
                        {"id": 0, "title": "Workflow", "skills": ["sample-workflow"]}
                    ],
                },
            ],
        }
        (self.root / "catalog/suites.json").write_text(json.dumps(document))
        self.assertEqual([], build(self.root))
        manifest = json.loads(
            (self.root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        names = [plugin["name"] for plugin in manifest["plugins"]]
        self.assertEqual(names, ["sample-audit", "sample-workflow"])

    def test_listed_skill_without_valid_entry_point_is_rejected(self):
        self.manifest()
        self.entry.unlink()
        with self.assertRaises(ValueError) as ctx:
            build(self.root)
        self.assertIn("missing entry point file", str(ctx.exception))

    def test_committed_marketplace_manifest_is_valid_and_current(self):
        self.assertEqual([], build(ROOT, check=True))
        manifest_path = ROOT / ".claude-plugin/marketplace.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "fred-skills")
        self.assertEqual(data["owner"]["name"], "fderuiter")
        plugins = data["plugins"]
        self.assertEqual(len(plugins), len(get_suite_skills(ROOT)))
        plugin_names = {plugin["name"] for plugin in plugins}
        for workflow_skill in get_suite_skills(ROOT, suite_name="repository-workflows"):
            self.assertIn(workflow_skill, plugin_names)
        for plugin in plugins:
            self.assertIn("name", plugin)
            self.assertIn("description", plugin)
            self.assertIn("source", plugin)
            self.assertTrue(plugin["source"].startswith("./skills/"))
            entry_file = ROOT / plugin["source"] / "SKILL.md"
            self.assertTrue(entry_file.is_file())

    def test_shared_resource_check_detects_and_repairs_drift(self):
        self.manifest()
        shared = self.root / "shared/repo-audit"
        source = shared / "resources/references/protocol.md"
        source.parent.mkdir(parents=True)
        source.write_text("Canonical protocol\n")
        (shared / "manifest.json").write_text(
            json.dumps({"files": ["references/protocol.md"]})
        )
        self.assertTrue(sync(self.root, check=True))
        self.assertEqual([], sync(self.root))
        self.assertEqual([], sync(self.root, check=True))
        target = self.skill / "references/protocol.md"
        target.write_text("Divergent copy\n")
        self.assertTrue(sync(self.root, check=True))
        self.assertEqual([], sync(self.root))
        self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_pre_commit_configuration_and_requirements(self):
        config_path = ROOT / ".pre-commit-config.yaml"
        self.assertTrue(config_path.is_file())
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertIn("repos", config)
        local_repos = [r for r in config["repos"] if r.get("repo") == "local"]
        self.assertTrue(local_repos)
        hooks = {h["id"]: h for h in local_repos[0].get("hooks", [])}
        for hook_id in [
            "sync-audit-resources",
            "build-catalog",
            "build-experience",
            "validate-skills",
            "unit-tests",
        ]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
            self.assertFalse(hooks[hook_id].get("pass_filenames", True))
            self.assertTrue(hooks[hook_id].get("always_run", False))
        for hook_id in ["ruff-lint", "ruff-format"]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
        self.assertIn("markdown", hooks["ruff-format"].get("types_or", []))
        self.assertEqual(hooks["ruff-format"].get("types_or"), ["python", "markdown"])
        for hook_id in ["check-doc-links", "verify-doc-snippets"]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
            self.assertEqual(hooks[hook_id].get("files"), r"\.md$")
            self.assertFalse(hooks[hook_id].get("pass_filenames", True))

        reqs_path = ROOT / "requirements-dev.txt"
        self.assertTrue(reqs_path.is_file())
        reqs = reqs_path.read_text(encoding="utf-8")
        self.assertIn("pre-commit", reqs)
        self.assertIn("ruff", reqs)

        validate_wf_path = ROOT / ".github/workflows/validate.yml"
        self.assertTrue(validate_wf_path.is_file())
        with validate_wf_path.open("r", encoding="utf-8") as f:
            validate_wf = yaml.safe_load(f)
        validate_steps = (
            validate_wf.get("jobs", {}).get("validate", {}).get("steps", [])
        )
        self.assertTrue(
            any(
                "pre-commit run --all-files" in s.get("run", "") for s in validate_steps
            ),
            "validate.yml must execute pre-commit run --all-files",
        )

    def test_makefile_target_definitions(self):
        makefile_path = ROOT / "Makefile"
        self.assertTrue(makefile_path.is_file(), "Root Makefile must exist")
        content = makefile_path.read_text(encoding="utf-8")
        self.assertIn(".PHONY:", content)
        for target in ["check", "fix", "test"]:
            self.assertIn(target, content.split(".PHONY:")[1].splitlines()[0])
            self.assertTrue(
                any(line.startswith(f"{target}:") for line in content.splitlines()),
                f"Target {target} must be defined",
            )
        self.assertIn("RUFF ?= ruff", content)
        self.assertIn("scripts/sync_audit_resources.py", content)
        self.assertIn("scripts/build_catalog.py", content)
        self.assertIn("scripts/validate_skills.py", content)
        self.assertIn("scripts/check_doc_links.py", content)
        self.assertIn("scripts/verify_doc_snippets.py", content)
        self.assertIn("unittest", content)

    def test_makefile_dry_run_execution(self):
        if not shutil.which("make"):
            self.skipTest("make is not available on this host")
        for target in ["check", "fix", "test"]:
            result = subprocess.run(
                ["make", "-n", target],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"make -n {target} failed with error: {result.stderr}",
            )
            output = result.stdout
            if target == "check":
                self.assertIn("ruff check .", output)
                self.assertIn("ruff format --check .", output)
                self.assertIn("scripts/sync_audit_resources.py", output)
                self.assertIn("--check", output)
                self.assertIn("scripts/build_catalog.py", output)
                self.assertIn("scripts/validate_skills.py", output)
                self.assertIn("scripts/check_doc_links.py", output)
                self.assertIn("scripts/verify_doc_snippets.py", output)
                self.assertIn("unittest", output)
            elif target == "fix":
                self.assertIn("ruff format .", output)
                self.assertIn("ruff check --fix .", output)
                self.assertIn("scripts/sync_audit_resources.py", output)
                self.assertIn("scripts/build_catalog.py", output)
            elif target == "test":
                self.assertIn("unittest", output)

        default_result = subprocess.run(
            ["make", "-n"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(default_result.returncode, 0)
        self.assertIn("ruff check .", default_result.stdout)
        self.assertIn("ruff format --check .", default_result.stdout)
        self.assertIn("scripts/validate_skills.py", default_result.stdout)

    def test_documentation_files_state_accurate_package_count(self):
        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        suite_skills = [
            skill
            for suite in suites_doc["suites"]
            for phase in suite["phases"]
            for skill in phase["skills"]
        ]
        self.assertEqual(51, len(suite_skills))

        arch_text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
        self.assertIn("51 total packages", arch_text)

        val_text = (ROOT / "docs/validation.md").read_text(encoding="utf-8")
        self.assertIn("51 total packages", val_text)
        self.assertIn(
            "Each of the 51 skills in the suite includes three scenario specifications",
            val_text,
        )
        self.assertIn(
            "These 153 specifications are not 153 executed evaluations",
            val_text,
        )
        self.assertNotIn("42 skills in the suite", val_text)
        self.assertNotIn("126 specifications", val_text)
        self.assertNotIn("35 skills in the suite", val_text)
        self.assertNotIn("105 specifications", val_text)

        impl_text = (ROOT / "docs/implementation-status.md").read_text(encoding="utf-8")
        self.assertIn("51 total packages", impl_text)

        usage_text = (ROOT / "docs/usage.md").read_text(encoding="utf-8")
        self.assertIn("complete 51-package pack", usage_text)
        self.assertIn("fred-skills-0.2.0-complete.tar", usage_text)
        self.assertIn(
            "Install all 51 packages directly via plugin marketplaces", usage_text
        )

        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Install all 51 packages with a single marketplace command:", readme_text
        )

        for doc_name in [
            "docs/architecture.md",
            "docs/validation.md",
            "docs/implementation-status.md",
            "docs/release-notes-0.2.0.md",
        ]:
            doc_content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertNotIn("41 packages", doc_content)
            self.assertNotIn("41 skills", doc_content)

        exp_text = (ROOT / "docs/experience.md").read_text(encoding="utf-8")
        self.assertIn(
            "| `full-audit` | All 33 specialist audits plus coordinator and synthesis. | 35 |",
            exp_text,
        )
        self.assertIn(
            "| `complete` | Every audit and engineering workflow. | 51 |",
            exp_text,
        )

    def test_prompt_template_present_and_within_budget_is_valid(self):
        self.assertEqual([], validate_skill(self.skill))

    def test_missing_prompt_template_is_rejected(self):
        self.prompt.unlink()
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "Missing prompt template: assets/prompt.md" in error for error in errors
            )
        )

    def test_missing_prompt_template_uses_posix_separators_on_all_platforms(self):
        self.prompt.unlink()
        orig_rel = Path.relative_to

        def mock_relative_to(path_obj, other):
            rel = orig_rel(path_obj, other)
            return PureWindowsPath(rel)

        with patch.object(Path, "relative_to", mock_relative_to):
            errors = validate_skill(self.skill)
            self.assertTrue(
                any(
                    "Missing prompt template: assets/prompt.md" in error
                    for error in errors
                )
            )

    def test_prompt_template_exceeding_character_budget_is_rejected(self):
        self.prompt.write_text("A" * 1201, encoding="utf-8")
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "prompt template exceeds budget" in error and "1200 characters" in error
                for error in errors
            )
        )

    def test_prompt_template_exceeding_line_budget_is_rejected(self):
        self.prompt.write_text("\n".join(["line"] * 21), encoding="utf-8")
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "prompt template exceeds line budget" in error and "20 lines" in error
                for error in errors
            )
        )

    def test_prompt_template_with_unauthorized_placeholder_is_rejected(self):
        self.prompt.write_text(
            "Use sample-audit on <unauthorized_token> at <revision>.\n",
            encoding="utf-8",
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "unauthorized placeholder '<unauthorized_token>'" in error
                and "<repository>" in error
                for error in errors
            )
        )

    def test_all_repository_prompt_templates_comply(self):
        skills_dir = ROOT / "skills"
        packages = sorted(p for p in skills_dir.iterdir() if p.is_dir())
        expected_package_count = len(
            [
                p
                for p in skills_dir.iterdir()
                if p.is_dir() and (p / "SKILL.md").is_file()
            ]
        )
        self.assertGreater(len(packages), 0)
        self.assertEqual(expected_package_count, len(packages))
        for package in packages:
            prompt_file = package / "assets/prompt.md"
            self.assertTrue(prompt_file.is_file(), f"{package.name} missing prompt.md")
            content = prompt_file.read_text(encoding="utf-8")
            self.assertLessEqual(
                len(content),
                1200,
                f"{package.name} prompt exceeds 1200 characters ({len(content)})",
            )
            self.assertLessEqual(
                len(content.splitlines()),
                20,
                f"{package.name} prompt exceeds 20 lines ({len(content.splitlines())})",
            )
            errors = validate_skill(package)
            self.assertEqual([], errors, f"{package.name} failed validation: {errors}")

    def test_missing_output_schema_is_rejected(self):
        self.output_schema.unlink()
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("Missing output schema" in error for error in errors),
            f"Expected missing output schema error, got: {errors}",
        )

    def test_prompt_lacking_negative_constraints_or_guardrails_is_rejected(self):
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision>.\n", encoding="utf-8"
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "prompt template lacks negative constraints or boundary guardrails"
                in error
                for error in errors
            ),
            f"Expected prompt constraint error, got: {errors}",
        )

    def test_skill_markdown_lacking_boundary_sections_is_rejected(self):
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n# Sample\n\nInspect the supplied contract without standard headers.\n'
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("lacks boundary sections" in error for error in errors),
            f"Expected missing boundary section error, got: {errors}",
        )

    def test_validation_telemetry_metrics_and_export(self):
        errors, telemetry = validate_with_telemetry(ROOT)
        self.assertEqual([], errors)
        self.assertEqual(51, telemetry["total_packages"])
        self.assertEqual(51, telemetry["passed_packages"])
        self.assertEqual(0, telemetry["failed_packages"])
        self.assertEqual(1.0, telemetry["first_pass_guardrail_pass_rate"])
        self.assertEqual(100.0, telemetry["first_pass_guardrail_pass_rate_percentage"])
        self.assertEqual(1.0, telemetry["deterministic_constraint_coverage"])
        self.assertEqual(
            100.0, telemetry["deterministic_constraint_coverage_percentage"]
        )
        self.assertEqual(1.0, telemetry["output_schema_coverage"])
        self.assertEqual(100.0, telemetry["output_schema_coverage_percentage"])
        self.assertLess(telemetry["execution_time_seconds"], 5.0)

        telemetry_file = self.root / "telemetry.json"
        cmd = [
            sys.executable,
            str(ROOT / "scripts/validate_skills.py"),
            "--telemetry",
            str(telemetry_file),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertIn("Guardrail Pass Rate: 100.0%", res.stdout)
        self.assertIn("Deterministic Constraint Coverage: 100.0%", res.stdout)
        self.assertTrue(telemetry_file.is_file())
        exported = json.loads(telemetry_file.read_text(encoding="utf-8"))
        self.assertEqual(51, exported["total_packages"])
        self.assertEqual(1.0, exported["first_pass_guardrail_pass_rate"])

    def test_all_skills_have_output_format_schemas(self):
        skills_dir = ROOT / "skills"
        packages = sorted(
            p for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        )
        self.assertEqual(len(packages), 51)
        for package in packages:
            schemas = sorted(package.glob("assets/*.schema.json"))
            self.assertGreater(
                len(schemas),
                0,
                f"{package.name} missing output format schema in assets/",
            )
            for schema_path in schemas:
                schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    schema_data.get("$schema"),
                    "https://json-schema.org/draft/2020-12/schema",
                    f"{schema_path} does not use draft 2020-12",
                )

    def test_mypy_static_type_enforcement_contracts(self) -> None:
        import re
        import tomllib

        pyproject_path = ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "Root pyproject.toml must exist")
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        mypy_cfg = pyproject.get("tool", {}).get("mypy", {})
        self.assertEqual(mypy_cfg.get("python_version"), "3.12")
        self.assertEqual(mypy_cfg.get("mypy_path"), [".", "scripts"])
        self.assertEqual(
            mypy_cfg.get("files"),
            ["scripts", "tests", "evals", "examples", "skills"],
        )
        self.assertTrue(mypy_cfg.get("explicit_package_bases"))
        self.assertTrue(mypy_cfg.get("namespace_packages"))
        self.assertTrue(mypy_cfg.get("show_error_codes"))
        excludes = mypy_cfg.get("exclude", [])
        for candidate_path in (
            "skills/repo-audit-synthesis/scripts/audit_to_sarif.py",
            r"skills\repo-audit-synthesis\scripts\audit_to_sarif.py",
        ):
            self.assertTrue(
                any(re.search(pattern, candidate_path) for pattern in excludes),
                f"Synced audit_to_sarif.py copy ({candidate_path}) must be excluded from batch mypy discovery",
            )
        overrides = mypy_cfg.get("overrides", [])
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0].get("module"), ["playwright", "playwright.*"])
        self.assertTrue(overrides[0].get("ignore_missing_imports"))

        reqs = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        self.assertIn("mypy==2.4.0", reqs)
        self.assertIn("types-PyYAML==6.0.12.20261113", reqs)
        self.assertIn("types-jsonschema==4.26.0.20261006", reqs)

        pre_commit_cfg = yaml.safe_load(
            (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        )
        hooks = {h["id"]: h for h in pre_commit_cfg["repos"][0]["hooks"]}
        self.assertEqual(len(hooks), 10)
        self.assertIn("mypy", hooks)
        self.assertEqual(hooks["mypy"].get("entry"), "python -m mypy")
        self.assertEqual(hooks["mypy"].get("language"), "system")
        self.assertFalse(hooks["mypy"].get("pass_filenames", True))
        self.assertTrue(hooks["mypy"].get("always_run", False))

        makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("$(PYTHON) -m mypy", makefile_text)

        validate_wf = yaml.safe_load(
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        )
        steps = validate_wf["jobs"]["validate"]["steps"]
        self.assertTrue(
            any(
                s.get("run", "").strip() == "python -m mypy" and "if" not in s
                for s in steps
            ),
            "validate.yml must run python -m mypy across matrix OS jobs",
        )
        win_steps = [s for s in steps if s.get("if") == "runner.os == 'Windows'"]
        self.assertTrue(
            any("python -m mypy" in s.get("run", "") for s in win_steps),
            "validate.yml Windows fallback must include python -m mypy",
        )

        alt_platform = "linux" if sys.platform == "win32" else "win32"
        cross_check = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--platform",
                alt_platform,
                "--no-incremental",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            cross_check.returncode,
            f"mypy failed for --platform {alt_platform}:\n{cross_check.stdout}\n{cross_check.stderr}",
        )

    def test_mypy_cache_excluded_from_gitignore_and_repository_snapshot(self) -> None:
        from fredlib import repository

        gitignore_lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any(line.strip().rstrip("/") == ".mypy_cache" for line in gitignore_lines),
            ".gitignore must ignore .mypy_cache",
        )
        self.assertIn(".mypy_cache", repository.IGNORED)

        repo_dir = self.root / "unversioned-repo"
        (repo_dir / ".mypy_cache").mkdir(parents=True)
        (repo_dir / ".mypy_cache/cache.json").write_text("{}", encoding="utf-8")
        (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
        snap = repository.snapshot(repo_dir)
        self.assertEqual(list(snap["files"].keys()), ["app.py"])


if __name__ == "__main__":
    unittest.main()

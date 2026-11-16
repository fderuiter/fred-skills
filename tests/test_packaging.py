"""Negative package fixtures and generated-artifact drift checks."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_catalog import build
from package_skills import get_suite_skills
from sync_audit_resources import sync
from validate_skills import validate_skill


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/sample-audit"
        self.skill.mkdir(parents=True)
        self.entry = self.skill / "SKILL.md"
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n# Sample\n\nInspect the supplied contract.\n'
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


if __name__ == "__main__":
    unittest.main()

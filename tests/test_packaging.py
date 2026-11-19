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
from run_checks import (
    ALL_PHASES,
    DEFAULT_PHASES,
    build_check_env,
    build_phase_commands,
    resolve_phases,
    run_checks,
)
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
        cursor_marketplace_path = self.root / ".cursor-plugin/marketplace.json"
        gemini_ext_path = self.root / "gemini-extension.json"
        self.assertTrue(marketplace_path.is_file())
        self.assertTrue(cursor_marketplace_path.is_file())
        self.assertTrue(gemini_ext_path.is_file())
        gemini_default = json.loads(gemini_ext_path.read_text(encoding="utf-8"))
        self.assertEqual("0.0.0", gemini_default["version"])
        self.assertEqual("fred-skills", gemini_default["name"])

        (self.root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        problems_version = build(self.root, check=True)
        self.assertTrue(
            any("gemini-extension.json" in problem for problem in problems_version)
        )
        self.assertEqual([], build(self.root))
        self.assertEqual(
            "1.2.3",
            json.loads(gemini_ext_path.read_text(encoding="utf-8"))["version"],
        )

        marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        cursor_marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        gemini_ext_path.write_text('{"tampered": true}', encoding="utf-8")
        problems = build(self.root, check=True)
        self.assertTrue(
            any(".claude-plugin/marketplace.json" in problem for problem in problems)
        )
        self.assertTrue(
            any(".cursor-plugin/marketplace.json" in problem for problem in problems)
        )
        self.assertTrue(any("gemini-extension.json" in problem for problem in problems))

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
        self.assertEqual(names, ["fred-skills", "sample-audit", "sample-workflow"])

    def test_marketplace_manifest_emits_inline_pack_bundles_and_rejects_invalid_packs(
        self,
    ):
        self.manifest()
        packs_doc = {
            "schema_version": "1.0",
            "packs": {
                "starter": {
                    "description": "Starter bundle.",
                    "skills": ["sample-audit"],
                }
            },
        }
        packs_file = self.root / "catalog/packs.json"
        packs_file.write_text(json.dumps(packs_doc), encoding="utf-8")
        self.assertEqual([], build(self.root))
        manifest = json.loads(
            (self.root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["plugins"],
            [
                {
                    "name": "fred-skills",
                    "description": "Portable engineering workflows for investigating repositories, planning useful fixes, and verifying that those fixes work.",
                    "source": "./",
                    "strict": False,
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "starter",
                    "description": "Starter bundle.",
                    "source": "./",
                    "strict": False,
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "sample-audit",
                    "description": "Audit synthetic contracts when explicitly requested.",
                    "source": "./skills/sample-audit",
                    "skills": ["."],
                },
            ],
        )

        packs_doc["packs"]["starter"]["skills"] = ["nonexistent-skill"]
        packs_file.write_text(json.dumps(packs_doc), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.root)

    def test_listed_skill_without_valid_entry_point_is_rejected(self):
        self.manifest()
        self.entry.unlink()
        with self.assertRaises(ValueError) as ctx:
            build(self.root)
        self.assertIn("missing entry point file", str(ctx.exception))

    def test_committed_marketplace_manifest_is_valid_and_current(self):
        self.assertEqual([], build(ROOT, check=True))
        self.assertFalse((ROOT / ".claude-plugin/plugin.json").exists())
        self.assertFalse((ROOT / ".cursor-plugin/plugin.json").exists())
        suite_skills = get_suite_skills(ROOT)
        packs = json.loads((ROOT / "catalog/packs.json").read_text(encoding="utf-8"))[
            "packs"
        ]
        for rel_manifest in [
            ".claude-plugin/marketplace.json",
            ".cursor-plugin/marketplace.json",
        ]:
            manifest_path = ROOT / rel_manifest
            self.assertTrue(manifest_path.is_file())
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(data["name"], "fred-skills")
            self.assertEqual(data["owner"]["name"], "fderuiter")
            plugins = data["plugins"]
            self.assertEqual(len(plugins), 1 + len(packs) + len(suite_skills))
            plugin_names = [plugin["name"] for plugin in plugins]
            self.assertEqual(len(plugin_names), len(set(plugin_names)))
            skill_plugins = [p for p in plugins if p["source"].startswith("./skills/")]
            bundle_plugins = [p for p in plugins if p["source"] == "./"]
            self.assertEqual(len(skill_plugins) + len(bundle_plugins), len(plugins))
            self.assertEqual({p["name"] for p in skill_plugins}, set(suite_skills))
            self.assertEqual(
                {p["name"] for p in bundle_plugins}, {"fred-skills", *packs}
            )
            suite_plugin = bundle_plugins[0]
            self.assertEqual(suite_plugin["name"], "fred-skills")
            self.assertIs(suite_plugin["strict"], False)
            self.assertEqual(
                suite_plugin["skills"],
                [f"./skills/{member}" for member in sorted(suite_skills)],
            )
            for workflow_skill in get_suite_skills(
                ROOT, suite_name="repository-workflows"
            ):
                self.assertIn(workflow_skill, set(plugin_names))
            for plugin in skill_plugins:
                self.assertIn("description", plugin)
                self.assertNotIn("strict", plugin)
                self.assertEqual(plugin["skills"], ["."])
                for rel_skill in plugin["skills"]:
                    entry_file = ROOT / plugin["source"] / rel_skill / "SKILL.md"
                    self.assertTrue(entry_file.is_file())
            for plugin in bundle_plugins[1:]:
                expected_pack = packs[plugin["name"]]
                self.assertEqual(plugin["description"], expected_pack["description"])
                self.assertIs(plugin["strict"], False)
                self.assertEqual(
                    plugin["skills"],
                    [f"./skills/{member}" for member in expected_pack["skills"]],
                )
            for plugin in bundle_plugins:
                for member_source in plugin["skills"]:
                    self.assertTrue(member_source.startswith("./skills/"))
                    self.assertTrue(
                        (ROOT / plugin["source"] / member_source / "SKILL.md").is_file()
                    )

        gemini_ext_path = ROOT / "gemini-extension.json"
        self.assertTrue(gemini_ext_path.is_file())
        gemini_data = json.loads(gemini_ext_path.read_text(encoding="utf-8"))
        self.assertEqual(gemini_data["name"], "fred-skills")
        self.assertTrue(gemini_data["description"])
        self.assertEqual(
            gemini_data["version"],
            (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        )

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
        self.assertEqual(
            config.get("default_install_hook_types"), ["pre-commit", "pre-push"]
        )
        self.assertEqual(config.get("default_stages"), ["pre-commit"])
        local_repos = [r for r in config["repos"] if r.get("repo") == "local"]
        self.assertTrue(local_repos)
        hooks = {h["id"]: h for h in local_repos[0].get("hooks", [])}
        for hook_id in [
            "mypy",
            "sync-audit-resources",
            "build-catalog",
            "build-experience",
            "validate-skills",
            "verify-workflow-actions",
            "unit-tests",
        ]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
            self.assertFalse(hooks[hook_id].get("pass_filenames", True))
            self.assertTrue(hooks[hook_id].get("always_run", False))
        self.assertEqual(hooks["unit-tests"].get("stages"), ["pre-push"])
        for hook_id in ["ruff-lint", "ruff-format"]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
        self.assertIn("markdown", hooks["ruff-format"].get("types_or", []))
        self.assertEqual(hooks["ruff-format"].get("types_or"), ["python", "markdown"])
        for hook_id in ["check-doc-links", "verify-doc-snippets"]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
            self.assertFalse(hooks[hook_id].get("pass_filenames", True))
            self.assertTrue(hooks[hook_id].get("always_run", False))
            self.assertNotIn("files", hooks[hook_id])

        reqs_path = ROOT / "requirements-dev.txt"
        self.assertTrue(reqs_path.is_file())
        reqs = reqs_path.read_text(encoding="utf-8")
        self.assertIn("pre-commit", reqs)
        self.assertIn("ruff", reqs)

        validate_wf_path = ROOT / ".github/workflows/validate.yml"
        self.assertTrue(validate_wf_path.is_file())
        with validate_wf_path.open("r", encoding="utf-8") as f:
            validate_wf = yaml.safe_load(f)
        validate_triggers = validate_wf.get("on") or validate_wf.get(True) or {}
        self.assertIn("push", validate_triggers)
        self.assertIn("pull_request", validate_triggers)
        self.assertIn("workflow_call", validate_triggers)
        concurrency = validate_wf.get("concurrency", {})
        self.assertIsInstance(concurrency, dict)
        group_expr = concurrency.get("group", "")
        self.assertTrue(
            group_expr.startswith("skills-${{ github.workflow }}-"),
            "validate.yml concurrency group must preserve workflow prefix",
        )
        self.assertIn("github.sha", group_expr)
        self.assertIn("github.ref", group_expr)
        self.assertIn("github.event_name == 'push'", group_expr)
        self.assertIn("github.ref == 'refs/heads/main'", group_expr)
        self.assertEqual(
            concurrency.get("cancel-in-progress"),
            "${{ github.ref != 'refs/heads/main' }}",
        )
        validate_steps = (
            validate_wf.get("jobs", {}).get("validate", {}).get("steps", [])
        )
        pre_commit_steps = [
            s
            for s in validate_steps
            if "pre-commit run --all-files" in s.get("run", "")
        ]
        self.assertEqual(
            1,
            len(pre_commit_steps),
            "validate.yml must execute pre-commit run --all-files once",
        )
        skip_ids = set(pre_commit_steps[0].get("env", {}).get("SKIP", "").split(","))
        for skipped_hook in [
            "ruff-lint",
            "mypy",
            "sync-audit-resources",
            "build-catalog",
            "build-experience",
            "validate-skills",
            "check-doc-links",
            "verify-doc-snippets",
            "verify-workflow-actions",
            "unit-tests",
        ]:
            self.assertIn(skipped_hook, skip_ids)
        self.assertNotIn("ruff-format", skip_ids)

        run_cmds = [s.get("run", "") for s in validate_steps]
        for phase in ["lint", "typecheck", "packages", "tests"]:
            self.assertTrue(
                any(
                    f"scripts/run_checks.py --phase {phase}" in cmd for cmd in run_cmds
                ),
                f"validate.yml must run dedicated '{phase}' phase step",
            )
        self.assertEqual(
            1,
            sum(
                1
                for cmd in run_cmds
                if "scripts/run_checks.py --phase tests" in cmd or "unittest" in cmd
            ),
            "validate.yml must execute unittest once per job",
        )
        make_steps = [s for s in validate_steps if "make -n check" in s.get("run", "")]
        self.assertEqual(1, len(make_steps))
        self.assertEqual(make_steps[0].get("if"), "runner.os != 'Windows'")
        self.assertFalse(
            any("Windows fallback" in s.get("name", "") for s in validate_steps)
        )

    def test_validate_workflow_concurrency_isolates_main_pushes(self):
        import re
        from types import SimpleNamespace

        def eval_gha_template(template, github_ctx):
            expr_pattern = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")

            def eval_expr(expr):
                py_expr = expr.replace("&&", " and ").replace("||", " or ")
                return eval(
                    py_expr,
                    {"__builtins__": {}},
                    {"github": SimpleNamespace(**github_ctx)},
                )

            full_match = expr_pattern.fullmatch(template.strip())
            if full_match:
                return eval_expr(full_match.group(1))
            return expr_pattern.sub(lambda m: str(eval_expr(m.group(1))), template)

        validate_wf = yaml.safe_load(
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        )
        concurrency = validate_wf["concurrency"]
        group_tpl = concurrency["group"]
        cancel_tpl = concurrency["cancel-in-progress"]

        push_ctx_a = {
            "workflow": "Validate skill packages",
            "event_name": "push",
            "ref": "refs/heads/main",
            "sha": "b5a3509600000000000000000000000000000001",
        }
        push_ctx_b = {
            **push_ctx_a,
            "sha": "c6b4610700000000000000000000000000000002",
        }
        pr_ctx_1 = {
            "workflow": "Validate skill packages",
            "event_name": "pull_request",
            "ref": "refs/pull/42/merge",
            "sha": "b5a3509600000000000000000000000000000001",
        }
        pr_ctx_2 = {
            **pr_ctx_1,
            "sha": "d7c5721800000000000000000000000000000003",
        }

        group_push_a = eval_gha_template(group_tpl, push_ctx_a)
        group_push_b = eval_gha_template(group_tpl, push_ctx_b)
        self.assertNotEqual(group_push_a, group_push_b)
        self.assertIn(push_ctx_a["sha"], group_push_a)
        self.assertIn(push_ctx_b["sha"], group_push_b)
        self.assertEqual(group_push_a, eval_gha_template(group_tpl, push_ctx_a))
        self.assertIs(eval_gha_template(cancel_tpl, push_ctx_a), False)
        self.assertIs(eval_gha_template(cancel_tpl, push_ctx_b), False)

        group_pr_1 = eval_gha_template(group_tpl, pr_ctx_1)
        group_pr_2 = eval_gha_template(group_tpl, pr_ctx_2)
        self.assertEqual(
            group_pr_1, "skills-Validate skill packages-refs/pull/42/merge"
        )
        self.assertEqual(group_pr_1, group_pr_2)
        self.assertNotEqual(group_push_a, group_pr_1)
        self.assertIs(eval_gha_template(cancel_tpl, pr_ctx_1), True)

        release_wf = yaml.safe_load(
            (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            release_wf["concurrency"],
            {
                "group": "skills-${{ github.workflow }}-${{ github.ref }}",
                "cancel-in-progress": False,
            },
        )
        dispatch_release_ctx = {
            "workflow": "Release",
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/feature/dry-run",
            "sha": "e8d6832900000000000000000000000000000004",
        }
        caller_group = eval_gha_template(
            release_wf["concurrency"]["group"], dispatch_release_ctx
        )
        called_validate_group = eval_gha_template(group_tpl, dispatch_release_ctx)
        self.assertNotEqual(caller_group, called_validate_group)
        self.assertIn(dispatch_release_ctx["sha"], called_validate_group)

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
        self.assertIn("scripts/run_checks.py", content)
        self.assertNotIn("validation-telemetry.json", content)
        self.assertIn("scripts/sync_audit_resources.py", content)
        self.assertIn("scripts/build_catalog.py", content)
        self.assertIn("scripts/build_experience.py", content)
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
                self.assertIn("scripts/run_checks.py", output)
                self.assertNotIn("validation-telemetry.json", output)
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
        self.assertIn("scripts/run_checks.py", default_result.stdout)

    def test_run_checks_phase_resolution_and_doc_omission(self) -> None:
        self.assertEqual(
            ("lint", "format", "typecheck", "packages", "docs", "tests"),
            ALL_PHASES,
        )
        self.assertEqual(
            ("lint", "format", "typecheck", "packages", "tests"),
            DEFAULT_PHASES,
        )
        self.assertEqual(
            ["lint", "format", "typecheck", "packages", "tests"],
            resolve_phases(),
        )
        self.assertNotIn("docs", resolve_phases())
        self.assertEqual(["docs"], resolve_phases(["docs"]))
        self.assertEqual(
            ["lint", "typecheck", "tests"],
            resolve_phases(["tests", "typecheck", "lint", "lint"]),
        )
        with self.assertRaises(ValueError):
            resolve_phases(["unknown-phase"])

    def test_run_checks_enforces_no_bytecode_and_opt_in_telemetry(self) -> None:
        env = build_check_env({"PYTHONDONTWRITEBYTECODE": "0"})
        self.assertEqual("1", env["PYTHONDONTWRITEBYTECODE"])
        self.assertEqual("utf-8", env["PYTHONIOENCODING"])

        typecheck_cmds = build_phase_commands("typecheck", root=ROOT)
        self.assertEqual([[sys.executable, "-m", "mypy"]], typecheck_cmds)

        default_pkg_cmds = build_phase_commands("packages", root=ROOT)
        self.assertEqual(5, len(default_pkg_cmds))
        self.assertTrue(
            any(
                cmd[-2].endswith("verify_workflow_actions.py") and cmd[-1] == "--check"
                for cmd in default_pkg_cmds
            ),
            "packages phase must verify workflow action pins offline",
        )
        self.assertFalse(
            any("--telemetry" in cmd for cmd in default_pkg_cmds),
            "Default packages phase must not emit validation-telemetry.json",
        )

        telemetry_pkg_cmds = build_phase_commands(
            "packages", root=ROOT, telemetry_path="custom-telemetry.json"
        )
        self.assertIn("--telemetry", telemetry_pkg_cmds[-1])
        self.assertIn("custom-telemetry.json", telemetry_pkg_cmds[-1])

        docs_cmds = build_phase_commands("docs", root=ROOT)
        self.assertEqual(2, len(docs_cmds))
        self.assertTrue(docs_cmds[0][-1].endswith("check_doc_links.py"))
        self.assertTrue(docs_cmds[1][-1].endswith("verify_doc_snippets.py"))

    def test_run_checks_dry_run_and_failure_isolation(self) -> None:
        dry = subprocess.run(
            [sys.executable, str(ROOT / "scripts/run_checks.py"), "--dry-run"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, dry.returncode, dry.stderr)
        self.assertIn("[dry-run:lint]", dry.stdout)
        self.assertIn("[dry-run:format]", dry.stdout)
        self.assertIn("[dry-run:typecheck]", dry.stdout)
        self.assertIn("[dry-run:packages]", dry.stdout)
        self.assertIn("[dry-run:tests]", dry.stdout)
        self.assertNotIn("[dry-run:docs]", dry.stdout)

        invoked: list[tuple[list[str], dict[str, str]]] = []

        def fake_run(
            cmd: list[str],
            cwd: Path | None = None,
            env: dict[str, str] | None = None,
            check: bool = False,
        ) -> subprocess.CompletedProcess[str]:
            invoked.append((list(cmd), dict(env or {})))
            return subprocess.CompletedProcess(cmd, returncode=2)

        import contextlib
        import io

        out_buf, err_buf = io.StringIO(), io.StringIO()
        with (
            patch("run_checks.subprocess.run", side_effect=fake_run),
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = run_checks(
                phases=["lint", "tests"],
                root=ROOT,
                env={"PYTHONDONTWRITEBYTECODE": "0"},
            )
        self.assertEqual(2, rc)
        self.assertEqual(1, len(invoked))
        self.assertEqual("1", invoked[0][1]["PYTHONDONTWRITEBYTECODE"])
        self.assertIn("FAILED: phase 'lint'", err_buf.getvalue())

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
            "Register the marketplace catalog and install the `fred-skills` suite plugin, a curated pack (such as `complete` for all 51 packages or `starter`)",
            usage_text,
        )
        self.assertIn("/plugin marketplace add fderuiter/fred-skills", usage_text)
        self.assertIn("/plugin install fred-skills@fred-skills", usage_text)
        self.assertIn("/plugin install complete@fred-skills", usage_text)
        self.assertIn("$skill-installer", usage_text)
        self.assertIn(".agents/skills/", usage_text)
        self.assertNotIn("codex plugin", usage_text)

        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Register the marketplace catalog, then install the `fred-skills` suite plugin, a curated pack (such as `complete` for all 51 skills or `starter` for 6 core audit skills)",
            readme_text,
        )
        self.assertIn("/plugin marketplace add fderuiter/fred-skills", readme_text)
        self.assertIn("/plugin install fred-skills@fred-skills", readme_text)
        self.assertIn("/plugin install complete@fred-skills", readme_text)
        self.assertIn("$skill-installer", readme_text)
        self.assertIn(".agents/skills/", readme_text)
        self.assertNotIn("codex plugin", readme_text)
        self.assertNotIn("single marketplace command", readme_text)

        integration_text = (ROOT / "docs/integration.md").read_text(encoding="utf-8")
        self.assertIn("/plugin install fred-skills@fred-skills", integration_text)
        self.assertIn("$skill-installer", integration_text)
        self.assertNotIn("codex plugin", integration_text)

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
        self.assertEqual(len(hooks), 11)
        self.assertIn("mypy", hooks)
        self.assertEqual(hooks["mypy"].get("entry"), "python -m mypy")
        self.assertEqual(hooks["mypy"].get("language"), "system")
        self.assertFalse(hooks["mypy"].get("pass_filenames", True))
        self.assertTrue(hooks["mypy"].get("always_run", False))

        makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("$(PYTHON) scripts/run_checks.py", makefile_text)
        self.assertIn("typecheck", DEFAULT_PHASES)
        self.assertEqual(
            [[sys.executable, "-m", "mypy"]],
            build_phase_commands("typecheck", root=ROOT),
        )

        validate_wf = yaml.safe_load(
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        )
        steps = validate_wf["jobs"]["validate"]["steps"]
        typecheck_steps = [
            s
            for s in steps
            if s.get("run", "").strip()
            == "python scripts/run_checks.py --phase typecheck"
        ]
        self.assertEqual(
            1,
            len(typecheck_steps),
            "validate.yml must run scripts/run_checks.py --phase typecheck once per matrix job",
        )
        self.assertNotIn(
            "if",
            typecheck_steps[0],
            "typecheck phase must run unconditionally across Ubuntu and Windows matrix jobs",
        )
        pre_commit_step = next(
            s for s in steps if "pre-commit run --all-files" in s.get("run", "")
        )
        self.assertIn(
            "mypy",
            set(pre_commit_step.get("env", {}).get("SKIP", "").split(",")),
            "pre-commit formatting step must skip mypy so typecheck runs once per job",
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

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
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\nInspect the supplied contract.\n\n## Deliver\n\n"
            "Deliver the audit output conforming to [audit schema](assets/audit.schema.json).\n"
            "Do not modify application code without explicit user authorization.\n",
            encoding="utf-8",
        )
        assets_dir = self.skill / "assets"
        assets_dir.mkdir(parents=True)
        self.output_schema = assets_dir / "audit.schema.json"
        self.output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["schema_version"],
                    "properties": {
                        "schema_version": {"type": "string", "const": "1.0"}
                    },
                }
            ),
            encoding="utf-8",
        )
        self.prompt = assets_dir / "prompt.md"
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision> conforming to assets/audit.schema.json. "
            "Do not modify application code without authorization.\n",
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
                            "evaluation_note": "Fixture shape is for the author only; execute with raw source and a neutral audit task without revealing expected findings.",
                        },
                        {
                            "id": "sample-audit-safe",
                            "prompt": "Audit synthetic contracts when explicitly requested.",
                            "expected_behavior": ["Safeguards verified, no defect."],
                            "evaluation_note": "Construct a raw safe control; keep expected audit outcomes out of the execution prompt.",
                        },
                        {
                            "id": "sample-audit-routing",
                            "sibling_skill": "sample-sibling-audit",
                            "prompt": "Audit asynchronous cancellation propagation and stale completion handlers across worker tasks.",
                            "expected_behavior": [
                                "Route this request to sample-sibling-audit instead of activating sample-audit."
                            ],
                            "evaluation_note": "Near-neighbor sibling routing check within repository-audits; verify routing to sample-sibling-audit.",
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

    def test_generic_typo_routing_prompt_and_misplaced_audit_notes_are_rejected(self):
        # 1. Generic spelling/typo prompt is rejected
        data = json.loads(self.scenarios_file.read_text(encoding="utf-8"))
        data["scenarios"][2]["prompt"] = (
            "Fix a spelling mistake in the project description."
        )
        self.scenarios_file.write_text(json.dumps(data), encoding="utf-8")
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("generic typo or proofreading prompt" in error for error in errors),
            f"Expected generic typo prompt error, got: {errors}",
        )

        # 2. Misplaced audit notes inside a software-architecture skill are rejected
        arch_skill = self.root / "skills/arch-sample-spec"
        shutil.copytree(self.skill, arch_skill)
        (arch_skill / "SKILL.md").write_text(
            '---\nname: arch-sample-spec\ndescription: "Specify sample architecture boundaries."\n---\n\n# Arch Sample\n\n## Deliver\n\nDeliver architecture spec.\n',
            encoding="utf-8",
        )
        (arch_skill / "assets/prompt.md").write_text(
            "Use arch-sample-spec on <repository> at <revision>. Do not invent ungrounded boundaries.\n",
            encoding="utf-8",
        )
        (arch_skill / "references/evaluation-scenarios.json").write_text(
            json.dumps(
                {
                    "topic": "arch-sample-spec",
                    "scenarios": [
                        {
                            "id": "arch-sample-spec-failure",
                            "prompt": "Specify service boundaries for this module.",
                            "expected_behavior": ["Document trust boundary."],
                            "evaluation_note": "Execute without revealing expected findings.",
                        },
                        {
                            "id": "arch-sample-spec-safe",
                            "prompt": "Specify service boundaries for this module.",
                            "expected_behavior": ["Verify clean gateway."],
                            "evaluation_note": "Construct a raw safe implementation.",
                        },
                        {
                            "id": "arch-sample-spec-routing",
                            "sibling_skill": "arch-decision-record",
                            "prompt": "Author an architectural decision record comparing queue trade-offs.",
                            "expected_behavior": [
                                "Route to arch-decision-record instead."
                            ],
                            "evaluation_note": "This is an activation test, not an instruction to perform an audit.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        arch_errors = validate_skill(arch_skill)
        self.assertTrue(
            any("misplaced" in err or "legacy audit" in err for err in arch_errors),
            f"Expected misplaced audit note errors, got: {arch_errors}",
        )

        # 3. Out-of-suite sibling_skill is rejected when catalog/suites.json defines suite siblings
        (self.root / "catalog").mkdir(exist_ok=True)
        (self.root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "software-architecture",
                            "phases": [
                                {
                                    "id": 0,
                                    "title": "Arch",
                                    "skills": [
                                        "arch-sample-spec",
                                        "arch-decision-record",
                                    ],
                                }
                            ],
                        },
                        {
                            "name": "code-review",
                            "phases": [
                                {
                                    "id": 0,
                                    "title": "Review",
                                    "skills": ["review-diff-analysis"],
                                }
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (arch_skill / "references/evaluation-scenarios.json").write_text(
            json.dumps(
                {
                    "topic": "arch-sample-spec",
                    "scenarios": [
                        {
                            "id": "arch-sample-spec-failure",
                            "prompt": "Specify service boundaries for this module.",
                            "expected_behavior": ["Document trust boundary."],
                            "evaluation_note": "Execute without revealing expected boundary outcomes.",
                        },
                        {
                            "id": "arch-sample-spec-safe",
                            "prompt": "Specify service boundaries for this module.",
                            "expected_behavior": ["Verify clean gateway."],
                            "evaluation_note": "Construct a raw safe boundary control.",
                        },
                        {
                            "id": "arch-sample-spec-routing",
                            "sibling_skill": "review-diff-analysis",
                            "prompt": "Inspect pull request diff hunks and caller blast radius.",
                            "expected_behavior": [
                                "Route to review-diff-analysis instead."
                            ],
                            "evaluation_note": "Sibling routing check for review-diff-analysis.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        cross_suite_errors = validate_skill(arch_skill)
        self.assertTrue(
            any(
                "not a member of suite 'software-architecture'" in err
                for err in cross_suite_errors
            ),
            f"Expected cross-suite sibling error, got: {cross_suite_errors}",
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
        cursor_root_plugin_path = self.root / ".cursor-plugin/plugin.json"
        cursor_skill_plugin_path = (
            self.root / "skills/sample-audit/.cursor-plugin/plugin.json"
        )
        gemini_ext_path = self.root / "gemini-extension.json"
        self.assertTrue(marketplace_path.is_file())
        self.assertTrue(cursor_marketplace_path.is_file())
        self.assertTrue(cursor_root_plugin_path.is_file())
        self.assertTrue(cursor_skill_plugin_path.is_file())
        self.assertTrue(gemini_ext_path.is_file())
        gemini_default = json.loads(gemini_ext_path.read_text(encoding="utf-8"))
        self.assertEqual("0.0.0", gemini_default["version"])
        self.assertEqual("fred-skills", gemini_default["name"])
        self.assertEqual("AGENTS.md", gemini_default["contextFileName"])
        root_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        marketplace_default = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual(root_version, marketplace_default["version"])
        self.assertEqual(root_version, marketplace_default["metadata"]["version"])
        self.assertTrue(marketplace_default["description"])
        cursor_root_default = json.loads(
            cursor_root_plugin_path.read_text(encoding="utf-8")
        )
        self.assertEqual(root_version, cursor_root_default["version"])

        (self.root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        problems_version = build(self.root, check=True)
        self.assertTrue(
            any("gemini-extension.json" in problem for problem in problems_version)
        )
        self.assertTrue(
            any(
                ".claude-plugin/marketplace.json" in problem
                for problem in problems_version
            )
        )
        self.assertTrue(
            any(
                ".cursor-plugin/marketplace.json" in problem
                for problem in problems_version
            )
        )
        self.assertTrue(
            any(".cursor-plugin/plugin.json" in problem for problem in problems_version)
        )
        self.assertTrue(
            any(
                "skills/sample-audit/.cursor-plugin/plugin.json" in problem
                for problem in problems_version
            )
        )
        self.assertEqual([], build(self.root))
        self.assertEqual(
            "1.2.3",
            json.loads(gemini_ext_path.read_text(encoding="utf-8"))["version"],
        )
        updated_marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual("1.2.3", updated_marketplace["version"])
        self.assertEqual("1.2.3", updated_marketplace["metadata"]["version"])
        self.assertEqual(
            "1.2.3",
            json.loads(cursor_root_plugin_path.read_text(encoding="utf-8"))["version"],
        )
        self.assertEqual(
            "1.2.3",
            json.loads(cursor_skill_plugin_path.read_text(encoding="utf-8"))["version"],
        )

        marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        cursor_marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        cursor_root_plugin_path.write_text('{"tampered": true}', encoding="utf-8")
        cursor_skill_plugin_path.write_text('{"tampered": true}', encoding="utf-8")
        gemini_ext_path.write_text('{"tampered": true}', encoding="utf-8")
        problems = build(self.root, check=True)
        self.assertTrue(
            any(".claude-plugin/marketplace.json" in problem for problem in problems)
        )
        self.assertTrue(
            any(".cursor-plugin/marketplace.json" in problem for problem in problems)
        )
        self.assertTrue(
            any(".cursor-plugin/plugin.json" in problem for problem in problems)
        )
        self.assertTrue(
            any(
                "skills/sample-audit/.cursor-plugin/plugin.json" in problem
                for problem in problems
            )
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
        cursor_manifest = json.loads(
            (self.root / ".cursor-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        cursor_names = [plugin["name"] for plugin in cursor_manifest["plugins"]]
        self.assertEqual(
            cursor_names, ["fred-skills", "sample-audit", "sample-workflow"]
        )
        self.assertTrue(
            (self.root / "skills/sample-audit/.cursor-plugin/plugin.json").is_file()
        )
        self.assertTrue(
            (self.root / "skills/sample-workflow/.cursor-plugin/plugin.json").is_file()
        )

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
        root_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest = json.loads(
            (self.root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["plugins"],
            [
                {
                    "name": "fred-skills",
                    "description": "Portable engineering workflows for investigating repositories, planning useful fixes, and verifying that those fixes work.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "source": "./",
                    "strict": False,
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "starter",
                    "description": "Starter bundle.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "source": "./",
                    "strict": False,
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "sample-audit",
                    "description": "Audit synthetic contracts when explicitly requested.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "source": "./skills/sample-audit",
                    "skills": ["."],
                },
            ],
        )
        cursor_manifest = json.loads(
            (self.root / ".cursor-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            cursor_manifest["plugins"],
            [
                {
                    "name": "fred-skills",
                    "source": "./",
                    "description": "Portable engineering workflows for investigating repositories, planning useful fixes, and verifying that those fixes work.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "license": "MIT",
                },
                {
                    "name": "sample-audit",
                    "source": "./skills/sample-audit",
                    "description": "Audit synthetic contracts when explicitly requested.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "license": "MIT",
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
        self.assertTrue((ROOT / ".cursor-plugin/plugin.json").is_file())
        suite_skills = get_suite_skills(ROOT)
        packs = json.loads((ROOT / "catalog/packs.json").read_text(encoding="utf-8"))[
            "packs"
        ]
        active_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

        claude_manifest_path = ROOT / ".claude-plugin/marketplace.json"
        self.assertTrue(claude_manifest_path.is_file())
        claude_data = json.loads(claude_manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(claude_data["name"], "fred-skills")
        self.assertEqual(claude_data["version"], active_version)
        self.assertTrue(claude_data["description"])
        self.assertEqual(
            claude_data["description"], claude_data["metadata"]["description"]
        )
        self.assertEqual(claude_data["metadata"]["version"], active_version)
        self.assertEqual(claude_data["owner"], {"name": "fderuiter"})
        claude_plugins = claude_data["plugins"]
        self.assertEqual(len(claude_plugins), 1 + len(packs) + len(suite_skills))
        claude_names = [plugin["name"] for plugin in claude_plugins]
        self.assertEqual(len(claude_names), len(set(claude_names)))
        skill_plugins = [
            p for p in claude_plugins if p["source"].startswith("./skills/")
        ]
        bundle_plugins = [p for p in claude_plugins if p["source"] == "./"]
        self.assertEqual(len(skill_plugins) + len(bundle_plugins), len(claude_plugins))
        self.assertEqual({p["name"] for p in skill_plugins}, set(suite_skills))
        self.assertEqual({p["name"] for p in bundle_plugins}, {"fred-skills", *packs})
        suite_plugin = bundle_plugins[0]
        self.assertEqual(suite_plugin["name"], "fred-skills")
        self.assertIs(suite_plugin["strict"], False)
        self.assertEqual(suite_plugin["version"], active_version)
        self.assertEqual(suite_plugin["author"], {"name": "fderuiter"})
        self.assertEqual(
            suite_plugin["skills"],
            [f"./skills/{member}" for member in sorted(suite_skills)],
        )
        for workflow_skill in get_suite_skills(ROOT, suite_name="repository-workflows"):
            self.assertIn(workflow_skill, set(claude_names))
        for plugin in skill_plugins:
            self.assertIn("description", plugin)
            self.assertEqual(plugin["version"], active_version)
            self.assertEqual(plugin["author"], {"name": "fderuiter"})
            self.assertNotIn("strict", plugin)
            self.assertEqual(plugin["skills"], ["."])
            for rel_skill in plugin["skills"]:
                entry_file = ROOT / plugin["source"] / rel_skill / "SKILL.md"
                self.assertTrue(entry_file.is_file())
        for plugin in bundle_plugins[1:]:
            expected_pack = packs[plugin["name"]]
            self.assertEqual(plugin["description"], expected_pack["description"])
            self.assertEqual(plugin["version"], active_version)
            self.assertEqual(plugin["author"], {"name": "fderuiter"})
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

        cursor_root_manifest = json.loads(
            (ROOT / ".cursor-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cursor_root_manifest["name"], "fred-skills")
        self.assertEqual(cursor_root_manifest["version"], active_version)
        self.assertEqual(cursor_root_manifest["author"], {"name": "fderuiter"})
        self.assertEqual(cursor_root_manifest["license"], "MIT")
        self.assertNotIn("strict", cursor_root_manifest)
        self.assertEqual(
            cursor_root_manifest["skills"],
            [f"./skills/{member}" for member in sorted(suite_skills)],
        )

        cursor_manifest_path = ROOT / ".cursor-plugin/marketplace.json"
        self.assertTrue(cursor_manifest_path.is_file())
        cursor_data = json.loads(cursor_manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(cursor_data["name"], "fred-skills")
        self.assertEqual(cursor_data["owner"], {"name": "fderuiter"})
        self.assertEqual(cursor_data["metadata"]["version"], active_version)
        self.assertTrue(cursor_data["metadata"]["description"])
        cursor_plugins = cursor_data["plugins"]
        self.assertEqual(len(cursor_plugins), 1 + len(suite_skills))
        cursor_names = [p["name"] for p in cursor_plugins]
        self.assertEqual(len(cursor_names), len(set(cursor_names)))
        self.assertEqual(set(cursor_names), {"fred-skills", *suite_skills})
        for pack_name in packs:
            self.assertNotIn(pack_name, set(cursor_names))
        for entry in cursor_plugins:
            self.assertNotIn("strict", entry)
            self.assertNotIn("skills", entry)
            self.assertNotIn("..", entry["source"])
            self.assertEqual(entry["version"], active_version)
            self.assertEqual(entry["author"], {"name": "fderuiter"})
            self.assertEqual(entry["license"], "MIT")
            plugin_dir = (ROOT / entry["source"]).resolve()
            plugin_manifest_path = plugin_dir / ".cursor-plugin/plugin.json"
            self.assertTrue(
                plugin_manifest_path.is_file(),
                f"Missing .cursor-plugin/plugin.json for {entry['name']}",
            )
            plugin_manifest = json.loads(
                plugin_manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(plugin_manifest["name"], entry["name"])
            self.assertEqual(plugin_manifest["version"], active_version)
            self.assertEqual(plugin_manifest["author"], {"name": "fderuiter"})
            self.assertEqual(plugin_manifest["license"], "MIT")
            self.assertNotIn("strict", plugin_manifest)
            for rel_skill in plugin_manifest["skills"]:
                self.assertNotIn("..", rel_skill)
                self.assertTrue((plugin_dir / rel_skill / "SKILL.md").is_file())

        gemini_ext_path = ROOT / "gemini-extension.json"
        self.assertTrue(gemini_ext_path.is_file())
        gemini_data = json.loads(gemini_ext_path.read_text(encoding="utf-8"))
        self.assertEqual(gemini_data["name"], "fred-skills")
        self.assertTrue(gemini_data["description"])
        self.assertEqual(gemini_data["contextFileName"], "AGENTS.md")
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
        self.assertEqual(
            hooks["validate-skills"].get("entry"),
            "python scripts/validate_skills.py --telemetry validation-telemetry.json",
        )
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
        for phase in ["lint", "typecheck", "packages", "docs", "tests"]:
            self.assertTrue(
                any(
                    f"scripts/run_checks.py --phase {phase}" in cmd for cmd in run_cmds
                ),
                f"validate.yml must run dedicated '{phase}' phase step",
            )
        self.assertTrue(
            any(
                "scripts/run_checks.py --phase packages --telemetry validation-telemetry.json"
                in cmd
                for cmd in run_cmds
            ),
            "validate.yml packages phase must export validation-telemetry.json",
        )
        self.assertFalse(
            any(cmd.strip().startswith("ruff ") for cmd in run_cmds),
            "validate.yml must not run duplicate standalone ruff commands",
        )
        self.assertFalse(
            any(cmd.strip() == "make check" for cmd in run_cmds),
            "validate.yml must not run duplicate make check command",
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
        push_ctx_c = {
            **push_ctx_a,
            "sha": "e8d6832900000000000000000000000000000004",
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
        branch_push_1 = {
            "workflow": "Validate skill packages",
            "event_name": "push",
            "ref": "refs/heads/feature/branch",
            "sha": "a111111100000000000000000000000000000001",
        }
        branch_push_2 = {
            **branch_push_1,
            "sha": "b222222200000000000000000000000000000002",
        }

        group_push_a = eval_gha_template(group_tpl, push_ctx_a)
        group_push_b = eval_gha_template(group_tpl, push_ctx_b)
        group_push_c = eval_gha_template(group_tpl, push_ctx_c)
        self.assertEqual(3, len({group_push_a, group_push_b, group_push_c}))
        self.assertIn(push_ctx_a["sha"], group_push_a)
        self.assertIn(push_ctx_b["sha"], group_push_b)
        self.assertIn(push_ctx_c["sha"], group_push_c)
        self.assertEqual(group_push_a, eval_gha_template(group_tpl, push_ctx_a))
        self.assertIs(eval_gha_template(cancel_tpl, push_ctx_a), False)
        self.assertIs(eval_gha_template(cancel_tpl, push_ctx_b), False)
        self.assertIs(eval_gha_template(cancel_tpl, push_ctx_c), False)

        group_pr_1 = eval_gha_template(group_tpl, pr_ctx_1)
        group_pr_2 = eval_gha_template(group_tpl, pr_ctx_2)
        self.assertEqual(
            group_pr_1, "skills-Validate skill packages-refs/pull/42/merge"
        )
        self.assertNotIn(pr_ctx_1["sha"], group_pr_1)
        self.assertEqual(group_pr_1, group_pr_2)
        self.assertNotEqual(group_push_a, group_pr_1)
        self.assertIs(eval_gha_template(cancel_tpl, pr_ctx_1), True)

        group_branch_1 = eval_gha_template(group_tpl, branch_push_1)
        group_branch_2 = eval_gha_template(group_tpl, branch_push_2)
        self.assertEqual(
            group_branch_1, "skills-Validate skill packages-refs/heads/feature/branch"
        )
        self.assertNotIn(branch_push_1["sha"], group_branch_1)
        self.assertEqual(group_branch_1, group_branch_2)
        self.assertIs(eval_gha_template(cancel_tpl, branch_push_1), True)

        release_wf = yaml.safe_load(
            (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            release_wf["concurrency"],
            {
                "group": "skills-${{ github.workflow }}-${{ github.sha }}-${{ github.run_id }}",
                "cancel-in-progress": False,
            },
        )
        self.assertEqual(
            release_wf["jobs"]["release"]["concurrency"],
            {
                "group": "skills-release-publish-${{ needs.preflight.outputs.release_tag || github.ref }}",
                "cancel-in-progress": False,
            },
        )
        dispatch_release_ctx = {
            "workflow": "Release",
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/feature/dry-run",
            "sha": "e8d6832900000000000000000000000000000004",
            "run_id": "9004",
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
            ("lint", "format", "typecheck", "packages", "docs", "tests"),
            DEFAULT_PHASES,
        )
        self.assertEqual(
            ["lint", "format", "typecheck", "packages", "docs", "tests"],
            resolve_phases(),
        )
        self.assertIn("docs", resolve_phases())
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
        self.assertIn("[dry-run:docs]", dry.stdout)
        self.assertIn("[dry-run:tests]", dry.stdout)

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
        self.assertEqual(52, len(suite_skills))

        arch_text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
        self.assertIn("52 total packages", arch_text)

        val_text = (ROOT / "docs/validation.md").read_text(encoding="utf-8")
        self.assertIn("52 total packages", val_text)
        self.assertIn(
            "Each of the 52 skills in the suite includes three scenario specifications",
            val_text,
        )
        self.assertIn(
            "These 156 specifications are not 156 executed evaluations",
            val_text,
        )
        self.assertNotIn("42 skills in the suite", val_text)
        self.assertNotIn("126 specifications", val_text)
        self.assertNotIn("35 skills in the suite", val_text)
        self.assertNotIn("105 specifications", val_text)

        impl_text = (ROOT / "docs/implementation-status.md").read_text(encoding="utf-8")
        self.assertIn("52 total packages", impl_text)

        active_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        usage_text = (ROOT / "docs/usage.md").read_text(encoding="utf-8")
        self.assertIn("complete 52-package pack", usage_text)
        self.assertIn(f"fred-skills-{active_version}-complete.tar", usage_text)
        self.assertIn(
            "Register the marketplace catalog and install the `fred-skills` suite plugin, a curated pack (such as `complete` for all 52 packages or `starter`)",
            usage_text,
        )
        self.assertIn("/plugin marketplace add fderuiter/fred-skills", usage_text)
        self.assertIn("/plugin install fred-skills@fred-skills", usage_text)
        self.assertIn("/plugin install complete@fred-skills", usage_text)
        self.assertIn("### Claude Code", usage_text)
        self.assertIn("### Cursor", usage_text)
        self.assertNotIn("### Claude Code and Cursor", usage_text)
        self.assertIn("Dashboard -> Plugins & MCPs", usage_text)
        self.assertIn("Customize", usage_text)
        self.assertIn("$skill-installer", usage_text)
        self.assertIn(".agents/skills/", usage_text)
        self.assertNotIn("codex plugin", usage_text)

        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Register the marketplace catalog, then install the `fred-skills` suite plugin, a curated pack (such as `complete` for all 52 skills or `starter` for 6 core audit skills)",
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
        self.assertIn("### Cursor", integration_text)
        self.assertIn("Dashboard -> Plugins & MCPs", integration_text)
        self.assertIn("Customize", integration_text)
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
            "| `full-audit` | All 34 specialist audits plus coordinator, diagnostics, and synthesis. | 37 |",
            exp_text,
        )
        self.assertNotIn("| `architecture` |", exp_text)
        self.assertIn(
            "| `complete` | Every audit and engineering workflow. | 52 |",
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

    def _write_ui_metadata(
        self,
        skill_dir: Path | None = None,
        *,
        skill_name: str = "sample-audit",
        display_name: str = "Sample Audit",
        short_description: str = "Investigate synthetic behavioral contracts",
        icon_small: object = "assets/icon.svg",
        icon_large: object = "assets/icon.svg",
        policy: object = "default",
        svg_content: str | None = (
            '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" '
            'xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="20" '
            'height="20" rx="5" fill="#1E3A8A"/></svg>\n'
        ),
    ) -> Path:
        target_skill = skill_dir or self.skill
        if svg_content is not None:
            icon_file = target_skill / "assets/icon.svg"
            icon_file.parent.mkdir(parents=True, exist_ok=True)
            icon_file.write_text(svg_content, encoding="utf-8")
        interface_data: dict[str, object] = {
            "display_name": display_name,
            "short_description": short_description,
            "default_prompt": f"Use ${skill_name} on this repository.",
        }
        if icon_small is not Ellipsis:
            interface_data["icon_small"] = icon_small
        if icon_large is not Ellipsis:
            interface_data["icon_large"] = icon_large
        doc: dict[str, object] = {"interface": interface_data}
        if policy == "default":
            doc["policy"] = {
                "products": ["chatgpt", "codex", "api", "atlas"],
                "allow_implicit_invocation": True,
            }
        elif policy is not Ellipsis:
            doc["policy"] = policy
        ui_file = target_skill / "agents/openai.yaml"
        ui_file.parent.mkdir(parents=True, exist_ok=True)
        ui_file.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return ui_file

    def test_prompt_template_with_markdown_heading_or_code_fence_is_rejected(self):
        self.prompt.write_text(
            "# Diagnostic Prompt\n\nUse sample-audit on <repository>. Do not modify code.\n",
            encoding="utf-8",
        )
        heading_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Markdown headings" in error for error in heading_errors),
            f"Expected Markdown heading error, got: {heading_errors}",
        )

        self.prompt.write_text(
            "```markdown\nUse sample-audit on <repository>. Do not modify code.\n```\n",
            encoding="utf-8",
        )
        fence_errors = validate_skill(self.skill)
        self.assertTrue(
            any("triple-backtick code fences" in error for error in fence_errors),
            f"Expected code fence error, got: {fence_errors}",
        )

    def test_ui_metadata_missing_or_invalid_icons_are_rejected(self):
        self._write_ui_metadata()
        self.assertEqual([], validate_skill(self.skill))

        # Missing icon fields
        self._write_ui_metadata(icon_small=Ellipsis, icon_large=Ellipsis)
        missing_field_errors = validate_skill(self.skill)
        self.assertTrue(
            any("icon_small" in error for error in missing_field_errors),
            missing_field_errors,
        )
        self.assertTrue(
            any("icon_large" in error for error in missing_field_errors),
            missing_field_errors,
        )

        # Missing SVG file on disk
        self._write_ui_metadata(svg_content=None)
        (self.skill / "assets/icon.svg").unlink()
        missing_file_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Missing UI icon_small file" in error for error in missing_file_errors),
            missing_file_errors,
        )

        # Non-SVG extension
        (self.skill / "assets/icon.png").write_bytes(b"PNG")
        self._write_ui_metadata(
            icon_small="assets/icon.png",
            icon_large="assets/icon.png",
            svg_content=None,
        )
        non_svg_errors = validate_skill(self.skill)
        self.assertTrue(
            any(".svg" in error for error in non_svg_errors),
            non_svg_errors,
        )

        # Package-escaping relative icon path
        outside_svg = self.root / "outside.svg"
        outside_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"/>\n',
            encoding="utf-8",
        )
        self._write_ui_metadata(
            icon_small="../../outside.svg",
            icon_large="../../outside.svg",
        )
        escape_errors = validate_skill(self.skill)
        self.assertTrue(
            any("escapes standalone package" in error for error in escape_errors),
            escape_errors,
        )

        # Absolute or backslash icon paths
        for bad_icon in ("/tmp/outside.svg", r"assets\icon.svg", "C:/outside.svg"):
            self._write_ui_metadata(icon_small=bad_icon)
            bad_path_errors = validate_skill(self.skill)
            self.assertTrue(
                any("icon_small" in error for error in bad_path_errors),
                f"Expected icon_small rejection for {bad_icon}: {bad_path_errors}",
            )

        # Malformed SVG XML content
        self._write_ui_metadata(svg_content="not valid xml <svg")
        malformed_svg_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Invalid SVG" in error for error in malformed_svg_errors),
            malformed_svg_errors,
        )

    def test_ui_metadata_missing_or_non_boolean_policy_is_rejected(self):
        # Missing policy section entirely
        self._write_ui_metadata(policy=Ellipsis)
        no_policy_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "policy.allow_implicit_invocation must be a boolean" in error
                for error in no_policy_errors
            ),
            no_policy_errors,
        )

        # Missing allow_implicit_invocation key
        self._write_ui_metadata(policy={"products": ["codex"]})
        missing_key_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "policy.allow_implicit_invocation must be a boolean" in error
                for error in missing_key_errors
            ),
            missing_key_errors,
        )

        # Strings, integers, and null values must all fail isinstance(..., bool)
        for bad_val in ("true", "false", 1, 0, None):
            self._write_ui_metadata(policy={"allow_implicit_invocation": bad_val})
            bad_val_errors = validate_skill(self.skill)
            self.assertTrue(
                any(
                    "policy.allow_implicit_invocation must be a boolean" in error
                    for error in bad_val_errors
                ),
                f"Expected rejection for {bad_val!r}, got: {bad_val_errors}",
            )

        # Explicit booleans True and False both succeed
        for valid_bool in (True, False):
            self._write_ui_metadata(
                policy={
                    "products": ["chatgpt", "codex", "api", "atlas"],
                    "allow_implicit_invocation": valid_bool,
                }
            )
            self.assertEqual([], validate_skill(self.skill))

    def test_ui_metadata_repeated_adjacent_words_in_short_description_is_rejected(self):
        for bad_short in (
            "Investigate synthetic behavioral contracts contracts",
            "Audit audit synthetic behavioral contracts",
        ):
            self._write_ui_metadata(short_description=bad_short)
            errors = validate_skill(self.skill)
            self.assertTrue(
                any("repeated adjacent words" in error for error in errors),
                f"Expected repeated-word rejection for {bad_short!r}, got: {errors}",
            )

    def test_suite_requires_openai_yaml_and_unique_short_descriptions(self):
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )
        self.manifest()

        # Standalone check allows omitting agents/openai.yaml outside suite validation
        self.assertEqual([], validate_skill(self.skill))

        # Suite validation requires agents/openai.yaml on suite members
        suite_errors, _ = validate_with_telemetry(self.root)
        self.assertTrue(
            any(
                "sample-audit: Suite skill missing required UI metadata: agents/openai.yaml"
                in err
                for err in suite_errors
            ),
            suite_errors,
        )

        # Add valid UI metadata to sample-audit and create a second skill with duplicate short_description
        self._write_ui_metadata(
            self.skill,
            skill_name="sample-audit",
            short_description="Verify bounded engineering workflow outcomes",
        )
        second_skill = self.root / "skills/second-audit"
        shutil.copytree(self.skill, second_skill)
        (second_skill / "SKILL.md").write_text(
            '---\nname: second-audit\ndescription: "Audit secondary contracts when explicitly requested."\n---\n\n'
            "# Second\n\nInspect the secondary contract.\n\n## Deliver\n\n"
            "Deliver the audit output conforming to [audit schema](assets/audit.schema.json).\n"
            "Do not modify application code without explicit user authorization.\n",
            encoding="utf-8",
        )
        self._write_ui_metadata(
            second_skill,
            skill_name="second-audit",
            short_description="Verify bounded engineering workflow outcomes",
        )

        dup_errors, _ = validate_with_telemetry(self.root)
        self.assertTrue(
            any("Duplicate UI short_description" in err for err in dup_errors),
            dup_errors,
        )

        self._write_ui_metadata(
            second_skill,
            skill_name="second-audit",
            short_description="Verify distinct secondary workflow outcomes",
        )
        clean_errors, _ = validate_with_telemetry(self.root)
        self.assertEqual([], clean_errors)

    def test_all_51_suite_skills_metadata_icons_and_policies_parity(self):
        import hashlib
        import re
        import xml.etree.ElementTree as ET

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        suite_to_skills: dict[str, list[str]] = {}
        all_suite_skills: list[str] = []
        for suite in suites_doc["suites"]:
            skills_in_suite = [
                skill for phase in suite["phases"] for skill in phase["skills"]
            ]
            suite_to_skills[suite["name"]] = skills_in_suite
            all_suite_skills.extend(skills_in_suite)

        self.assertEqual(52, len(all_suite_skills))
        self.assertEqual(
            {
                "repository-audits",
                "repository-workflows",
                "software-architecture",
                "code-review",
                "automated-testing",
            },
            set(suite_to_skills),
        )

        explicit_only = {"repo-apply-fix", "test-case-generation"}
        expected_products = ["chatgpt", "codex", "api", "atlas"]
        repeated_word_re = re.compile(r"\b([A-Za-z0-9]+)\s+\1\b", re.IGNORECASE)
        heading_re = re.compile(r"^\s*#+", re.MULTILINE)

        short_descriptions: dict[str, str] = {}
        svg_hashes: set[str] = set()
        suite_bg_colors: dict[str, set[str]] = {}

        for suite_name, skills_in_suite in suite_to_skills.items():
            suite_bg_colors[suite_name] = set()
            for skill_name in skills_in_suite:
                pkg = ROOT / "skills" / skill_name
                ui_path = pkg / "agents/openai.yaml"
                icon_path = pkg / "assets/icon.svg"
                prompt_path = pkg / "assets/prompt.md"

                self.assertTrue(ui_path.is_file(), f"{skill_name} missing openai.yaml")
                self.assertTrue(icon_path.is_file(), f"{skill_name} missing icon.svg")
                self.assertTrue(
                    prompt_path.is_file(), f"{skill_name} missing prompt.md"
                )

                svg_bytes = icon_path.read_bytes()
                svg_hashes.add(hashlib.sha256(svg_bytes).hexdigest())
                svg_root = ET.fromstring(svg_bytes.decode("utf-8"))
                self.assertEqual("24", svg_root.attrib.get("width"), skill_name)
                self.assertEqual("24", svg_root.attrib.get("height"), skill_name)
                self.assertEqual(
                    "0 0 24 24", svg_root.attrib.get("viewBox"), skill_name
                )
                first_child = next(iter(svg_root))
                self.assertIsNotNone(first_child.attrib.get("fill"), skill_name)
                suite_bg_colors[suite_name].add(first_child.attrib["fill"])

                ui_doc = yaml.safe_load(ui_path.read_text(encoding="utf-8"))
                interface = ui_doc["interface"]
                self.assertEqual("assets/icon.svg", interface.get("icon_small"))
                self.assertEqual("assets/icon.svg", interface.get("icon_large"))

                short = interface.get("short_description")
                self.assertIsInstance(short, str)
                self.assertGreaterEqual(len(short), 25, skill_name)
                self.assertLessEqual(len(short), 64, skill_name)
                self.assertIsNone(
                    repeated_word_re.search(short),
                    f"{skill_name} has repeated adjacent words in {short!r}",
                )
                self.assertNotIn(
                    short,
                    short_descriptions,
                    f"Duplicate short_description {short!r} in {skill_name} and {short_descriptions.get(short)}",
                )
                short_descriptions[short] = skill_name

                policy = ui_doc.get("policy")
                self.assertIsInstance(policy, dict, skill_name)
                allow_implicit = policy.get("allow_implicit_invocation")
                self.assertIsInstance(allow_implicit, bool, skill_name)
                if skill_name in explicit_only:
                    self.assertIs(allow_implicit, False, skill_name)
                else:
                    self.assertIs(allow_implicit, True, skill_name)
                    self.assertEqual(
                        expected_products, policy.get("products"), skill_name
                    )

                prompt_text = prompt_path.read_text(encoding="utf-8")
                self.assertNotIn("```", prompt_text, skill_name)
                self.assertIsNone(heading_re.search(prompt_text), skill_name)

        # Every skill has a distinct SVG and each suite uses one distinct palette color
        self.assertEqual(52, len(svg_hashes))
        for suite_name, colors in suite_bg_colors.items():
            self.assertEqual(
                1,
                len(colors),
                f"{suite_name} must use a single suite palette: {colors}",
            )
        distinct_suite_colors = {next(iter(c)) for c in suite_bg_colors.values()}
        self.assertEqual(5, len(distinct_suite_colors))

        diagnostics_prompt = (
            ROOT / "skills/repo-audit-diagnostics/assets/prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Do not modify canonical ledger files; enforce diagnostic boundary guardrails.",
            diagnostics_prompt,
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
        noun_only_prompt = (
            "Use sample-audit on <repository> at <revision> conforming to assets/audit.schema.json. "
            "Enforce boundary guardrails, isolation barriers, invariants, and safe controls.\n"
        )
        for unconstrained_prompt in (
            "Use sample-audit on <repository> at <revision> conforming to assets/audit.schema.json.\n",
            noun_only_prompt,
        ):
            self.prompt.write_text(unconstrained_prompt, encoding="utf-8")
            errors = validate_skill(self.skill)
            self.assertTrue(
                any(
                    "prompt template lacks negative constraints or boundary guardrails"
                    in error
                    for error in errors
                ),
                f"Expected prompt constraint error for {unconstrained_prompt!r}, got: {errors}",
            )

    def test_prompt_missing_output_schema_mention_is_rejected(self):
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision>. Inspect data schemas carefully. "
            "Do not modify application code without authorization.\n",
            encoding="utf-8",
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("output schema contract" in error for error in errors),
            f"Expected missing output schema mention error, got: {errors}",
        )

    def test_skill_markdown_lacking_boundary_sections_is_rejected(self):
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\nInspect [audit schema](assets/audit.schema.json); do not mutate files.\n",
            encoding="utf-8",
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any("lacks boundary sections" in error for error in errors),
            f"Expected missing boundary section error, got: {errors}",
        )

    def test_skill_markdown_prohibition_and_delivery_or_guardrail_rules(self):
        # Noun-only SKILL.md without explicit prohibitions is rejected
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\n## Workflow\n\nMap trust boundaries and isolation barriers.\n\n"
            "## Deliver\n\nDeliver [audit schema](assets/audit.schema.json) invariant summaries.\n",
            encoding="utf-8",
        )
        noun_only_errors = validate_skill(self.skill)
        self.assertTrue(
            any("explicit negative prohibitions" in e for e in noun_only_errors),
            f"Expected explicit prohibition error, got: {noun_only_errors}",
        )

        # Prohibition only in ## Workflow without ## Guardrails or ## Deliver prohibition is rejected
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\n## Workflow\n\nDo not trust unvalidated inputs.\n\n"
            "## Deliver\n\nDeliver [audit schema](assets/audit.schema.json) summaries.\n",
            encoding="utf-8",
        )
        misplaced_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "dedicated guardrail heading" in e and "delivery section" in e
                for e in misplaced_errors
            ),
            f"Expected guardrail/delivery placement error, got: {misplaced_errors}",
        )

        # Dedicated ## Guardrails section with explicit prohibition and linked schema is accepted
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\n## Guardrails\n\n- Never modify application code without authorization.\n\n"
            "## Deliver\n\nDeliver [audit schema](assets/audit.schema.json) summaries.\n",
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

    def test_skill_markdown_missing_schema_link_is_rejected(self):
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            "# Sample\n\n## Deliver\n\nDeliver the audit output. Do not modify application code.\n",
            encoding="utf-8",
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "missing markdown link to output schema assets/audit.schema.json"
                in error
                for error in errors
            ),
            f"Expected unlinked schema error, got: {errors}",
        )

    def test_empty_and_open_schemas_are_rejected_while_map_objects_are_allowed(self):
        # 1. Empty {} schema
        self.output_schema.write_text("{}", encoding="utf-8")
        empty_errors = validate_skill(self.skill)
        self.assertTrue(
            any("empty or non-object JSON schema" in e for e in empty_errors),
            f"Expected empty schema rejection, got: {empty_errors}",
        )

        # 2. Open root object schema without properties
        self.output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                }
            ),
            encoding="utf-8",
        )
        open_root_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "root schema must define non-empty 'properties'" in e
                for e in open_root_errors
            ),
            f"Expected open root schema rejection, got: {open_root_errors}",
        )

        # 3. Nested structured object with properties omitting additionalProperties: false
        self.output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "nested": {
                            "type": "object",
                            "properties": {"field": {"type": "string"}},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        nested_open_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "$.properties.nested" in e and "'additionalProperties': false" in e
                for e in nested_open_errors
            ),
            f"Expected nested open object rejection, got: {nested_open_errors}",
        )

        # 4. Intentional map object (additionalProperties schema) and untyped details object without properties
        self.output_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "by_category": {
                            "type": "object",
                            "additionalProperties": {"type": "integer", "minimum": 0},
                        },
                        "details": {"type": "object"},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

    def test_multi_hop_markdown_links_are_rejected(self):
        refs_dir = self.skill / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        (refs_dir / "hop1.md").write_text(
            "# Hop 1\n\nSee [hop 2](hop2.md) for nested details.\n",
            encoding="utf-8",
        )
        (refs_dir / "hop2.md").write_text("# Hop 2\n", encoding="utf-8")
        self.entry.write_text(
            self.entry.read_text(encoding="utf-8")
            + "\nRead [hop 1](references/hop1.md).\n",
            encoding="utf-8",
        )
        errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "1-hop depth limit" in e
                and "SKILL.md -> references/hop1.md -> references/hop2.md" in e
                for e in errors
            ),
            f"Expected multi-hop link error, got: {errors}",
        )

    def test_progressive_disclosure_token_budgets_are_enforced(self):
        orig_entry = self.entry.read_text(encoding="utf-8")

        # 1. Frontmatter startup metadata over 100 tokens
        bloated_desc = " ".join(["contract"] * 105)
        self.entry.write_text(
            f'---\nname: sample-audit\ndescription: "{bloated_desc}"\n---\n\n'
            "# Sample\n\n## Deliver\n\nDeliver [audit schema](assets/audit.schema.json). "
            "Do not modify code.\n",
            encoding="utf-8",
        )
        meta_errors = validate_skill(self.skill)
        self.assertTrue(
            any("frontmatter metadata exceeds token budget" in e for e in meta_errors),
            f"Expected metadata token budget error, got: {meta_errors}",
        )

        # 2. SKILL.md 5,000-token body (< 500 lines)
        five_k_body = "\n".join([" ".join(["word"] * 50) for _ in range(100)])
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n'
            f"# Sample\n\n{five_k_body}\n\n## Deliver\n\n"
            "Deliver [audit schema](assets/audit.schema.json). Do not modify code.\n",
            encoding="utf-8",
        )
        body_errors = validate_skill(self.skill)
        self.assertTrue(
            any("skill body exceeds token budget" in e for e in body_errors),
            f"Expected 5,000-token body rejection, got: {body_errors}",
        )

        # 3. Reference markdown 5,000-token file
        self.entry.write_text(orig_entry, encoding="utf-8")
        bloated_ref = self.skill / "references/bloated.md"
        bloated_ref.write_text(" ".join(["detail"] * 5000), encoding="utf-8")
        ref_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "references/bloated.md: reference markdown exceeds token budget" in e
                for e in ref_errors
            ),
            f"Expected reference token budget error, got: {ref_errors}",
        )

    def test_telemetry_rejects_unconstrained_and_empty_schema_fixtures_with_zero_coverage(
        self,
    ):
        self.manifest()
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )

        # 1. Negative-constraint-free noun-only synthetic fixture -> 0% constraint coverage
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision> conforming to assets/audit.schema.json. "
            "Map boundaries and guardrails.\n",
            encoding="utf-8",
        )
        errors_no_neg, tel_no_neg = validate_with_telemetry(self.root)
        self.assertTrue(errors_no_neg)
        self.assertEqual(0, tel_no_neg["passed_packages"])
        self.assertEqual(0.0, tel_no_neg["deterministic_constraint_coverage"])
        self.assertEqual(
            0.0, tel_no_neg["deterministic_constraint_coverage_percentage"]
        )

        # 2. Empty {} schema synthetic fixture -> 0% constraint and schema coverage
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision> conforming to assets/audit.schema.json. "
            "Do not modify application code.\n",
            encoding="utf-8",
        )
        self.output_schema.write_text("{}", encoding="utf-8")
        errors_empty, tel_empty = validate_with_telemetry(self.root)
        self.assertTrue(errors_empty)
        self.assertEqual(0, tel_empty["passed_packages"])
        self.assertEqual(0.0, tel_empty["output_schema_coverage"])
        self.assertEqual(0.0, tel_empty["deterministic_constraint_coverage"])
        self.assertEqual(0.0, tel_empty["deterministic_constraint_coverage_percentage"])

    def test_validation_telemetry_metrics_and_export(self):
        errors, telemetry = validate_with_telemetry(ROOT)
        self.assertEqual([], errors)
        self.assertEqual(52, telemetry["total_packages"])
        self.assertEqual(52, telemetry["passed_packages"])
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
        self.assertEqual(52, exported["total_packages"])
        self.assertEqual(1.0, exported["first_pass_guardrail_pass_rate"])

    def test_all_skills_have_output_format_schemas(self):
        skills_dir = ROOT / "skills"
        packages = sorted(
            p for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        )
        self.assertEqual(len(packages), 52)
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

    def test_validate_skills_cyclomatic_complexity_and_nesting_bounds(self) -> None:
        import ast

        def calculate_complexity(node: ast.AST) -> int:
            complexity = 1
            for child in ast.walk(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.ExceptHandler,
                        ast.With,
                        ast.AsyncWith,
                        ast.IfExp,
                    ),
                ):
                    complexity += 1
                elif isinstance(child, ast.BoolOp):
                    complexity += len(child.values) - 1
            return complexity

        def max_nesting_depth(stmts: list[ast.stmt], depth: int = 0) -> int:
            deepest = depth
            for stmt in stmts:
                if isinstance(
                    stmt,
                    (
                        ast.If,
                        ast.For,
                        ast.AsyncFor,
                        ast.While,
                        ast.With,
                        ast.AsyncWith,
                        ast.Try,
                        ast.TryStar,
                    ),
                ):
                    orelse = getattr(stmt, "orelse", [])
                    branches: list[tuple[list[ast.stmt], int]] = [
                        (stmt.body, depth + 1),
                        (getattr(stmt, "finalbody", []), depth + 1),
                    ]
                    for handler in getattr(stmt, "handlers", []):
                        branches.append((handler.body, depth + 1))
                    if orelse:
                        is_elif = len(orelse) == 1 and isinstance(orelse[0], ast.If)
                        branches.append((orelse, depth if is_elif else depth + 1))
                    for branch_body, next_depth in branches:
                        if branch_body:
                            deepest = max(
                                deepest, max_nesting_depth(branch_body, next_depth)
                            )
            return deepest

        validator_py = ROOT / "scripts/validate_skills.py"
        tree = ast.parse(validator_py.read_text(encoding="utf-8"))
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[item.name] = item
                for sub in ast.walk(item):
                    if sub is not item and isinstance(
                        sub, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        functions[f"{item.name}.{sub.name}"] = sub
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions[f"{item.name}.{method.name}"] = method

        self.assertGreater(len(functions), 0)
        for name, func_node in functions.items():
            score = calculate_complexity(func_node)
            self.assertLessEqual(
                score,
                10,
                f"Function {name} exceeds cyclomatic complexity limit of 10 (score: {score})",
            )
            nesting = max_nesting_depth(func_node.body)
            self.assertLessEqual(
                nesting,
                3,
                f"Function {name} exceeds control-flow nesting limit of 3 (depth: {nesting})",
            )

    def test_frontmatter_failure_short_circuits_and_symlink_ui_is_guarded(self) -> None:
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )
        self._write_ui_metadata(
            self.skill,
            skill_name="sample-audit",
            short_description="Verify bounded engineering workflow outcomes",
        )

        broken_skill = self.root / "skills/broken-frontmatter"
        shutil.copytree(self.skill, broken_skill)
        (broken_skill / "SKILL.md").write_text(
            "# Missing YAML frontmatter\n", encoding="utf-8"
        )
        (broken_skill / "assets/prompt.md").unlink()

        single_errors = validate_skill(broken_skill)
        self.assertEqual(1, len(single_errors))
        self.assertIn("missing YAML frontmatter", single_errors[0])

        try:
            symlink_skill = self.root / "skills/symlink-ui-audit"
            shutil.copytree(self.skill, symlink_skill)
            (symlink_skill / "SKILL.md").write_text(
                '---\nname: symlink-ui-audit\ndescription: "Audit symlinked UI guard behavior."\n---\n\n'
                "# Symlink UI\n\n## Deliver\n\n"
                "Deliver [audit schema](assets/audit.schema.json). Do not modify code.\n",
                encoding="utf-8",
            )
            symlink_ui = symlink_skill / "agents/openai.yaml"
            symlink_ui.unlink()
            symlink_ui.symlink_to(self.skill / "agents/openai.yaml")
        except OSError:
            symlink_skill = None

        all_errors, telemetry = validate_with_telemetry(
            self.root, record_attempts=False
        )
        pkg_by_name = {pkg["name"]: pkg for pkg in telemetry["packages"]}
        broken_tel = pkg_by_name["broken-frontmatter"]
        self.assertIs(broken_tel["valid"], False)
        self.assertIs(broken_tel["has_output_schema"], False)
        self.assertIs(broken_tel["has_prompt_constraints"], False)
        self.assertIs(broken_tel["has_boundary_sections"], False)
        self.assertIs(broken_tel["deterministic_constraint_coverage"], False)
        self.assertEqual(["assets/audit.schema.json"], broken_tel["output_schemas"])
        self.assertEqual(single_errors, broken_tel["errors"])

        if symlink_skill is not None:
            self.assertTrue(
                any(
                    "symlink-ui-audit: Symlink breaks standalone packaging: agents/openai.yaml"
                    in err
                    for err in all_errors
                ),
                all_errors,
            )
            self.assertFalse(
                any("Duplicate UI short_description" in err for err in all_errors),
                f"Symlinked openai.yaml must not trigger duplicate short_description: {all_errors}",
            )

    def test_cursor_plugin_manifest_validation_and_install_exclusion(self) -> None:
        from fredlib import installation

        cursor_dir = self.skill / ".cursor-plugin"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        plugin_json = cursor_dir / "plugin.json"
        valid_manifest = {
            "name": "sample-audit",
            "description": "Audit synthetic contracts when explicitly requested.",
            "version": "0.2.0",
            "author": {"name": "fderuiter"},
            "license": "MIT",
            "skills": ["."],
        }
        plugin_json.write_text(json.dumps(valid_manifest), encoding="utf-8")
        self.assertEqual([], validate_skill(self.skill))

        # Unexpected file inside .cursor-plugin/ is rejected
        extra_file = cursor_dir / "extra.json"
        extra_file.write_text("{}", encoding="utf-8")
        errs_extra = validate_skill(self.skill)
        self.assertTrue(
            any("Unexpected file in .cursor-plugin" in e for e in errs_extra),
            errs_extra,
        )
        extra_file.unlink()

        # Unsupported strict field or mismatched name or parent traversal is rejected
        bad_manifest = {
            **valid_manifest,
            "name": "wrong-name",
            "strict": False,
            "skills": ["../escape"],
        }
        plugin_json.write_text(json.dumps(bad_manifest), encoding="utf-8")
        errs_bad = validate_skill(self.skill)
        self.assertTrue(any("must equal 'sample-audit'" in e for e in errs_bad))
        self.assertTrue(any("strict" in e for e in errs_bad))
        self.assertTrue(any("'..'" in e for e in errs_bad))

        # CLI installation excludes .cursor-plugin from installed destination
        dest_project = self.root / "dest-project"
        dest_project.mkdir(parents=True, exist_ok=True)
        install_res = installation.install(
            skills=["repo-audit-concurrency"],
            destination=dest_project,
            host="cursor",
            scope="project",
            root=ROOT,
        )
        installed_dir = Path(install_res["target"]) / "repo-audit-concurrency"
        self.assertTrue((installed_dir / "SKILL.md").is_file())
        self.assertFalse((installed_dir / ".cursor-plugin").exists())

    def test_all_repository_skill_prompt_bodies_are_unique_after_line_one(self) -> None:
        errors, telemetry = validate_with_telemetry(ROOT, record_attempts=False)
        self.assertEqual([], errors)
        self.assertEqual(52, telemetry["total_packages"])

        bodies_by_skill: dict[str, str] = {}
        for prompt_path in sorted((ROOT / "skills").glob("*/assets/prompt.md")):
            lines = prompt_path.read_text(encoding="utf-8").splitlines()
            self.assertGreater(
                len(lines), 1, f"{prompt_path} must have a multi-line body"
            )
            normalized = " ".join(" ".join(lines[1:]).split())
            self.assertTrue(normalized, f"{prompt_path} body after line 1 is empty")
            bodies_by_skill[prompt_path.parent.parent.name] = normalized

        self.assertEqual(52, len(bodies_by_skill))
        self.assertEqual(
            len(bodies_by_skill),
            len(set(bodies_by_skill.values())),
            "Every skills/*/assets/prompt.md must have a unique body after line 1",
        )

    def test_tactical_placeholders_allowed_and_duplicate_or_unknown_prompts_rejected(
        self,
    ) -> None:
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )
        self._write_ui_metadata(
            self.skill,
            skill_name="sample-audit",
            short_description="Verify tactical prompt placeholder guardrails",
        )
        tactical_body = (
            "Scope inspection to <directory> and <symbol> with issue <issue-number>.\n"
            "Write `audit.json` conforming to `assets/audit.schema.json`.\n"
            "Honor <allowed-paths> and <do-not-modify-paths>; do not modify code unauthorized.\n"
        )
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision>.\n" + tactical_body,
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

        # Unknown placeholders must be rejected.
        self.prompt.write_text(
            "Use sample-audit on <repository> with <bogus-placeholder>.\n"
            + tactical_body,
            encoding="utf-8",
        )
        unknown_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "unauthorized placeholder '<bogus-placeholder>'" in err
                for err in unknown_errors
            ),
            unknown_errors,
        )

        # Restore valid prompt on first skill and clone an identical body after line 1 onto a second skill.
        self.prompt.write_text(
            "Use sample-audit on <repository> at <revision>.\n" + tactical_body,
            encoding="utf-8",
        )
        twin = self.root / "skills/twin-audit"
        shutil.copytree(self.skill, twin)
        (twin / "SKILL.md").write_text(
            '---\nname: twin-audit\ndescription: "Audit distinct topic with cloned prompt body."\n---\n\n'
            "# Twin\n\n## Boundaries\n\nDo not modify code.\n\n## Deliver\n\n"
            "Deliver [audit schema](assets/audit.schema.json).\n",
            encoding="utf-8",
        )
        self._write_ui_metadata(
            twin,
            skill_name="twin-audit",
            short_description="Verify distinct twin UI short description",
        )
        (twin / "assets/prompt.md").write_text(
            "Use twin-audit on <repository> at <revision>.\n" + tactical_body,
            encoding="utf-8",
        )
        dup_errors, _ = validate_with_telemetry(self.root, record_attempts=False)
        self.assertTrue(
            any(
                "Duplicate prompt template body: twin-audit and sample-audit" in err
                for err in dup_errors
            ),
            dup_errors,
        )


if __name__ == "__main__":
    unittest.main()

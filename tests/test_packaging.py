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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
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
from validate_skills import (
    EXPLICIT_INVOCATION_ONLY_SKILLS,
    _validate_documentation_invariants,
    validate_skill,
    validate_with_telemetry,
)

from tests.catalog_oracles import (
    expected_audit_domain_count,
    expected_host_count,
    expected_non_audit_skill_count,
    expected_pack_skill_count,
    expected_suite_count,
    expected_suite_names,
    expected_total_skills,
)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/sample-audit"
        self.skill.mkdir(parents=True)
        self.entry = self.skill / "SKILL.md"
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts offline. Use when testing package rules. Do not use for production."\n---\n\n'
            "# Sample\n\nInspect the supplied contract using [worked examples](references/examples.md).\n\n## Deliver\n\n"
            "Deliver the audit output conforming to [audit schema](assets/audit.schema.json), "
            "verify with [audit validator](scripts/validate_audit.py), and run standalone via [prompt](assets/prompt.md).\n"
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
        self.examples_file = self.skill / "references/examples.md"
        self.examples_file.write_text(
            "# Worked Examples\n\n"
            "```python\n"
            "def check_contract(value: int) -> bool:\n"
            "    return value >= 0\n"
            "```\n\n"
            "```json\n"
            '{\n  "schema_version": "1.0"\n}\n'
            "```\n",
            encoding="utf-8",
        )
        scripts_dir = self.skill / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.validator_script = scripts_dir / "validate_audit.py"
        self.validator_script.write_text(
            "#!/usr/bin/env python3\n"
            '"""Minimal synthetic validator fixture."""\n'
            "import sys\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        self.validator_script.chmod(0o755)
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
        self.assertFalse(cursor_root_plugin_path.exists())
        self.assertTrue(cursor_skill_plugin_path.is_file())
        self.assertTrue(gemini_ext_path.is_file())
        gemini_default = json.loads(gemini_ext_path.read_text(encoding="utf-8"))
        self.assertEqual("0.0.0", gemini_default["version"])
        self.assertEqual("fred-skills", gemini_default["name"])
        self.assertNotIn("contextFileName", gemini_default)
        root_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        marketplace_default = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual(root_version, marketplace_default["version"])
        self.assertEqual(root_version, marketplace_default["metadata"]["version"])
        self.assertTrue(marketplace_default["description"])
        cursor_marketplace_default = json.loads(
            cursor_marketplace_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            root_version, cursor_marketplace_default["metadata"]["version"]
        )

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
            json.loads(cursor_marketplace_path.read_text(encoding="utf-8"))["metadata"][
                "version"
            ],
        )
        self.assertEqual(
            "1.2.3",
            json.loads(cursor_skill_plugin_path.read_text(encoding="utf-8"))["version"],
        )

        marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        cursor_marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
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
                    "description": "Audit synthetic contracts offline. Use when testing package rules. Do not use for production.",
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
        self.assertFalse((self.root / ".cursor-plugin/plugin.json").exists())
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
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "starter",
                    "source": "./",
                    "description": "Starter bundle.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "license": "MIT",
                    "skills": ["./skills/sample-audit"],
                },
                {
                    "name": "sample-audit",
                    "source": "./skills/sample-audit",
                    "description": "Audit synthetic contracts offline. Use when testing package rules. Do not use for production.",
                    "version": root_version,
                    "author": {"name": "fderuiter"},
                    "license": "MIT",
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

        cursor_manifest_path = ROOT / ".cursor-plugin/marketplace.json"
        self.assertTrue(cursor_manifest_path.is_file())
        cursor_data = json.loads(cursor_manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(cursor_data["name"], "fred-skills")
        self.assertEqual(cursor_data["owner"], {"name": "fderuiter"})
        self.assertEqual(cursor_data["metadata"]["version"], active_version)
        self.assertTrue(cursor_data["metadata"]["description"])
        cursor_plugins = cursor_data["plugins"]
        self.assertEqual(len(cursor_plugins), 1 + len(packs) + len(suite_skills))
        cursor_names = [p["name"] for p in cursor_plugins]
        self.assertEqual(len(cursor_names), len(set(cursor_names)))
        self.assertEqual(set(cursor_names), {"fred-skills", *packs, *suite_skills})
        cursor_bundles = [p for p in cursor_plugins if p["source"] == "./"]
        cursor_skill_entries = [
            p for p in cursor_plugins if p["source"].startswith("./skills/")
        ]
        self.assertEqual(
            len(cursor_bundles) + len(cursor_skill_entries), len(cursor_plugins)
        )
        self.assertEqual({p["name"] for p in cursor_bundles}, {"fred-skills", *packs})
        self.assertEqual({p["name"] for p in cursor_skill_entries}, set(suite_skills))
        self.assertEqual(
            cursor_bundles[0]["skills"],
            [f"./skills/{member}" for member in sorted(suite_skills)],
        )
        for bundle in cursor_bundles[1:]:
            expected_pack = packs[bundle["name"]]
            self.assertEqual(bundle["description"], expected_pack["description"])
            self.assertEqual(
                bundle["skills"],
                [f"./skills/{member}" for member in expected_pack["skills"]],
            )
        for entry in cursor_plugins:
            self.assertNotIn("strict", entry)
            self.assertIn("skills", entry)
            self.assertTrue(entry["skills"])
            self.assertNotIn("..", entry["source"])
            self.assertEqual(entry["version"], active_version)
            self.assertEqual(entry["author"], {"name": "fderuiter"})
            self.assertEqual(entry["license"], "MIT")
            plugin_dir = (ROOT / entry["source"]).resolve()
            for rel_skill in entry["skills"]:
                self.assertNotIn("..", rel_skill)
                self.assertTrue((plugin_dir / rel_skill / "SKILL.md").is_file())
        for entry in cursor_skill_entries:
            self.assertEqual(entry["skills"], ["."])
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
        self.assertNotIn("contextFileName", gemini_data)
        self.assertEqual(
            gemini_data["version"],
            (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        )
        for shim_name in ("CLAUDE.md", "GEMINI.md"):
            shim_path = ROOT / shim_name
            self.assertTrue(shim_path.is_file(), f"Missing host shim {shim_name}")
            self.assertEqual("@AGENTS.md\n", shim_path.read_text(encoding="utf-8"))

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
            group_expr.startswith("skills-"),
            "validate.yml concurrency group must preserve skills- prefix",
        )
        self.assertIn("validate-", group_expr)
        self.assertIn("github.workflow", group_expr)
        self.assertIn("github.sha", group_expr)
        self.assertIn("github.ref", group_expr)
        self.assertIn("github.run_id", group_expr)
        self.assertIn("github.event_name == 'push'", group_expr)
        self.assertIn("github.ref == 'refs/heads/main'", group_expr)
        self.assertEqual(
            concurrency.get("cancel-in-progress"),
            "${{ github.workflow == 'Validate skill packages' && github.ref != 'refs/heads/main' }}",
        )
        for job_name, job_cfg in validate_wf.get("jobs", {}).items():
            for step in job_cfg.get("steps", []):
                if "run" in step:
                    self.assertNotIn(
                        "${{",
                        step["run"],
                        f"Inline expression found in validate.yml {job_name} step {step.get('name')!r}",
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
        dispatch_release_ctx_2 = {
            **dispatch_release_ctx,
            "run_id": "9005",
        }
        tag_release_ctx = {
            "workflow": "Release",
            "event_name": "push",
            "ref": "refs/tags/v0.3.0",
            "sha": "e8d6832900000000000000000000000000000004",
            "run_id": "9006",
        }
        wf_run_release_ctx = {
            "workflow": "Release",
            "event_name": "workflow_run",
            "ref": "refs/heads/main",
            "sha": "e8d6832900000000000000000000000000000004",
            "run_id": "9007",
        }
        caller_group = eval_gha_template(
            release_wf["concurrency"]["group"], dispatch_release_ctx
        )
        called_validate_group = eval_gha_template(group_tpl, dispatch_release_ctx)
        self.assertEqual(
            f"skills-validate-Release-{dispatch_release_ctx['sha']}-9004",
            called_validate_group,
        )
        self.assertNotEqual(caller_group, called_validate_group)
        self.assertIs(eval_gha_template(cancel_tpl, dispatch_release_ctx), False)

        called_dispatch_2 = eval_gha_template(group_tpl, dispatch_release_ctx_2)
        called_tag_group = eval_gha_template(group_tpl, tag_release_ctx)
        called_wf_run_group = eval_gha_template(group_tpl, wf_run_release_ctx)
        self.assertEqual(
            f"skills-validate-Release-{tag_release_ctx['sha']}-9006",
            called_tag_group,
        )
        self.assertNotEqual(
            eval_gha_template(release_wf["concurrency"]["group"], tag_release_ctx),
            called_tag_group,
        )
        self.assertIs(eval_gha_template(cancel_tpl, tag_release_ctx), False)
        self.assertIs(eval_gha_template(cancel_tpl, wf_run_release_ctx), False)
        self.assertEqual(
            4,
            len(
                {
                    called_validate_group,
                    called_dispatch_2,
                    called_tag_group,
                    called_wf_run_group,
                }
            ),
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
        self.assertEqual(3, len(docs_cmds))
        self.assertTrue(docs_cmds[0][1].endswith("build_site.py"))
        self.assertEqual("--output", docs_cmds[0][2])
        self.assertEqual(
            (ROOT / "dist" / "site").resolve(), Path(docs_cmds[0][3]).resolve()
        )
        self.assertTrue(docs_cmds[1][-1].endswith("check_doc_links.py"))
        self.assertTrue(docs_cmds[2][-1].endswith("verify_doc_snippets.py"))

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
            *_args: object,
            env: dict[str, str] | None = None,
            **_kwargs: object,
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
        expected_skills = expected_total_skills(ROOT)
        expected_specs = expected_skills * 3
        expected_starter_count = expected_pack_skill_count("starter", ROOT)
        expected_complete_count = expected_pack_skill_count("complete", ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_starter_count, 1)
        self.assertGreaterEqual(expected_complete_count, 1)
        self.assertEqual(expected_skills, len(suite_skills))

        arch_text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
        self.assertIn(f"{expected_skills} total packages", arch_text)

        val_text = (ROOT / "docs/validation.md").read_text(encoding="utf-8")
        self.assertIn(f"{expected_skills} total packages", val_text)
        self.assertIn(
            f"Each of the {expected_skills} skills in the suite includes three scenario specifications",
            val_text,
        )
        self.assertIn(
            f"These {expected_specs} specifications are not {expected_specs} executed evaluations",
            val_text,
        )
        self.assertNotIn("42 skills in the suite", val_text)
        self.assertNotIn("126 specifications", val_text)
        self.assertNotIn("35 skills in the suite", val_text)
        self.assertNotIn("105 specifications", val_text)

        impl_text = (ROOT / "docs/implementation-status.md").read_text(encoding="utf-8")
        self.assertIn(f"{expected_skills} total packages", impl_text)

        active_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        usage_text = (ROOT / "docs/usage.md").read_text(encoding="utf-8")
        self.assertIn(f"complete {expected_complete_count}-package pack", usage_text)
        self.assertIn(f"fred-skills-{active_version}-starter.tar", usage_text)
        self.assertIn(f"fred-skills-{active_version}-complete.tar", usage_text)
        self.assertLess(
            usage_text.index(f"fred-skills-{active_version}-starter.tar"),
            usage_text.index(f"fred-skills-{active_version}-complete.tar"),
        )
        self.assertIn(
            f"Register the marketplace catalog and install `starter` for {expected_starter_count} core audit skills (`<= 1,500` raw characters)",
            usage_text,
        )
        self.assertIn("/plugin marketplace add fderuiter/fred-skills", usage_text)
        self.assertIn("/plugin install starter@fred-skills", usage_text)
        self.assertIn("/plugin install fred-skills@fred-skills", usage_text)
        self.assertIn("/plugin install complete@fred-skills", usage_text)
        self.assertLess(
            usage_text.index("/plugin install starter@fred-skills"),
            usage_text.index("/plugin install complete@fred-skills"),
        )
        self.assertLess(
            usage_text.index("/plugin install starter@fred-skills"),
            usage_text.index("/plugin install fred-skills@fred-skills"),
        )
        self.assertIn("https://developers.openai.com/codex/skills", usage_text)
        self.assertIn("https://code.claude.com/docs/en/skills", usage_text)
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
            f"Register the marketplace catalog, then install `starter` for {expected_starter_count} core audit skills (`<= 1,500` raw characters)",
            readme_text,
        )
        self.assertIn("/plugin marketplace add fderuiter/fred-skills", readme_text)
        self.assertIn("/plugin install starter@fred-skills", readme_text)
        self.assertIn("/plugin install fred-skills@fred-skills", readme_text)
        self.assertIn("/plugin install complete@fred-skills", readme_text)
        self.assertLess(
            readme_text.index("/plugin install starter@fred-skills"),
            readme_text.index("/plugin install complete@fred-skills"),
        )
        self.assertLess(
            readme_text.index("/plugin install starter@fred-skills"),
            readme_text.index("/plugin install fred-skills@fred-skills"),
        )
        self.assertLess(
            readme_text.index(f"fred-skills-{active_version}-starter.tar"),
            readme_text.index("fred-skills-suite.tar.gz"),
        )
        self.assertIn("https://developers.openai.com/codex/skills", readme_text)
        self.assertIn("https://code.claude.com/docs/en/skills", readme_text)
        self.assertIn("$skill-installer", readme_text)
        self.assertIn(".agents/skills/", readme_text)
        self.assertNotIn("codex plugin", readme_text)
        self.assertNotIn("single marketplace command", readme_text)

        integration_text = (ROOT / "docs/integration.md").read_text(encoding="utf-8")
        self.assertIn("/plugin install starter@fred-skills", integration_text)
        self.assertIn("/plugin install fred-skills@fred-skills", integration_text)
        self.assertLess(
            integration_text.index("/plugin install starter@fred-skills"),
            integration_text.index("/plugin install complete@fred-skills"),
        )
        self.assertLess(
            integration_text.index("/plugin install starter@fred-skills"),
            integration_text.index("/plugin install fred-skills@fred-skills"),
        )
        self.assertIn("https://developers.openai.com/codex/skills", integration_text)
        self.assertIn("https://code.claude.com/docs/en/skills", integration_text)
        self.assertIn("### Cursor", integration_text)
        self.assertIn("Dashboard -> Plugins & MCPs", integration_text)
        self.assertIn("Customize", integration_text)
        self.assertIn("$skill-installer", integration_text)
        self.assertNotIn("codex plugin", integration_text)

        gemini_ext = json.loads(
            (ROOT / "gemini-extension.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "https://developers.openai.com/codex/skills", gemini_ext["description"]
        )
        self.assertIn(
            "https://code.claude.com/docs/en/skills", gemini_ext["description"]
        )

        tagged_v020_proc = subprocess.run(
            ["git", "cat-file", "blob", "refs/tags/v0.2.0:docs/release-notes-0.2.0.md"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            tagged_v020_proc.stdout,
            (ROOT / "docs/release-notes-0.2.0.md").read_bytes(),
        )
        self.assertIn(
            "Total: 42 skills.",
            (ROOT / "docs/release-notes-0.2.0.md").read_text(encoding="utf-8"),
        )

        active_tag_exists = (
            subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"refs/tags/v{active_version}^{{commit}}",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )
        head_count_docs = [
            "docs/architecture.md",
            "docs/validation.md",
            "docs/implementation-status.md",
        ]
        if not active_tag_exists:
            head_count_docs.append(f"docs/release-notes-{active_version}.md")

        for doc_name in head_count_docs:
            doc_content = (ROOT / doc_name).read_text(encoding="utf-8")
            self.assertNotIn("41 packages", doc_content)
            self.assertNotIn("41 skills", doc_content)
            self.assertNotIn("42 skills", doc_content)
            self.assertNotIn("four explicit host", doc_content)
            self.assertNotIn("four host layouts", doc_content)

        hosts_doc = json.loads(
            (ROOT / "catalog/hosts.json").read_text(encoding="utf-8")
        )
        packs_doc = json.loads(
            (ROOT / "catalog/packs.json").read_text(encoding="utf-8")
        )
        expected_suites = len(suites_doc["suites"])
        expected_hosts = len(hosts_doc["hosts"])
        expected_packs = len(packs_doc["packs"])
        expected_skills = len(suite_skills)
        self.assertGreaterEqual(expected_suites, 1)
        self.assertGreaterEqual(expected_hosts, 1)
        self.assertGreaterEqual(expected_packs, 1)
        shared_dirs = [
            f"shared/{m.parent.name}/"
            for m in sorted((ROOT / "shared").glob("*/manifest.json"))
        ]
        deliverables = sorted(
            {s["artifact"]["filename"] for s in suites_doc["suites"] if "artifact" in s}
        )
        build_catalog_outputs = (
            "catalog/skills.json",
            "docs/catalog.md",
            ".claude-plugin/marketplace.json",
            ".cursor-plugin/marketplace.json",
            "gemini-extension.json",
        )

        rel_notes_path = ROOT / f"docs/release-notes-{active_version}.md"
        if not active_tag_exists:
            rel_notes_text = rel_notes_path.read_text(encoding="utf-8")
            self.assertIn(f"{expected_suites} suites", rel_notes_text)
            self.assertIn(f"{expected_packs} purpose packs", rel_notes_text)
            self.assertIn(
                f"{expected_hosts} explicit host installation\nlocations",
                rel_notes_text,
            )
            self.assertIn(f"Total: {expected_skills} skills.", rel_notes_text)
        else:
            tagged_active_proc = subprocess.run(
                [
                    "git",
                    "cat-file",
                    "blob",
                    f"refs/tags/v{active_version}:docs/release-notes-{active_version}.md",
                ],
                cwd=ROOT,
                capture_output=True,
                check=True,
            )
            self.assertEqual(tagged_active_proc.stdout, rel_notes_path.read_bytes())

        for shared_dir in shared_dirs:
            self.assertIn(shared_dir, arch_text)
            self.assertIn(shared_dir, (ROOT / "CONTRIBUTING.md").read_text("utf-8"))
        for deliverable in deliverables:
            self.assertIn(deliverable, arch_text)

        contrib_text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for doc_text in (arch_text, contrib_text, agents_text):
            for output_rel in build_catalog_outputs:
                self.assertIn(output_rel, doc_text)
            self.assertIn(".github/action-pins.json", doc_text)
        self.assertIn("--phase format", contrib_text)
        self.assertIn("repo-audit-diagnostics", readme_text)
        self.assertIn(f"{expected_hosts} host layouts", val_text)
        self.assertIn(f"{expected_hosts} host layouts", impl_text)

        research_text = (ROOT / "docs/research.md").read_text(encoding="utf-8")
        for banned in (
            "world's best skill repository",
            "Prioritize future work",
            "before expanding",
        ):
            self.assertNotIn(banned, impl_text)
        for banned in (
            "exhaustive market census",
            "proof that no comparable suite exists",
            "measured superiority",
            "market uniqueness",
            "into a read-only audit",
        ):
            self.assertNotIn(banned, research_text)

        self.assertEqual([], _validate_documentation_invariants(ROOT))

        exp_text = (ROOT / "docs/experience.md").read_text(encoding="utf-8")
        full_audit_pack = packs_doc["packs"]["full-audit"]
        complete_pack = packs_doc["packs"]["complete"]
        self.assertGreaterEqual(len(full_audit_pack["skills"]), 1)
        self.assertGreaterEqual(len(complete_pack["skills"]), 1)
        self.assertIn(
            f"| `full-audit` | {full_audit_pack['description']} | {len(full_audit_pack['skills'])} |",
            exp_text,
        )
        self.assertNotIn("| `architecture` |", exp_text)
        self.assertIn(
            f"| `complete` | {complete_pack['description']} | {len(complete_pack['skills'])} |",
            exp_text,
        )

    def test_documentation_invariants_reject_stale_counts_omissions_and_disclaimers(
        self,
    ) -> None:
        fixture_root = self.root / "doc-invariants-fixture"
        fixture_root.mkdir(parents=True, exist_ok=True)
        try:
            (fixture_root / "skills").symlink_to(
                ROOT / "skills", target_is_directory=True
            )
            (fixture_root / "schemas").symlink_to(
                ROOT / "schemas", target_is_directory=True
            )
            (fixture_root / "shared").symlink_to(
                ROOT / "shared", target_is_directory=True
            )
        except OSError:
            shutil.copytree(ROOT / "skills", fixture_root / "skills")
            shutil.copytree(ROOT / "schemas", fixture_root / "schemas")
            shutil.copytree(ROOT / "shared", fixture_root / "shared")

        active_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        copied_files = [
            "VERSION",
            "catalog/suites.json",
            "catalog/hosts.json",
            "catalog/packs.json",
            f"docs/release-notes-{active_version}.md",
            "README.md",
            "docs/architecture.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "docs/validation.md",
            "docs/implementation-status.md",
            "docs/research.md",
        ]
        originals: dict[str, str] = {}
        for rel in copied_files:
            dst = fixture_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            content = (ROOT / rel).read_text(encoding="utf-8")
            dst.write_text(content, encoding="utf-8")
            originals[rel] = content

        clean_proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(fixture_root),
                "--no-ledger",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0, clean_proc.returncode, clean_proc.stdout + clean_proc.stderr
        )

        expected_hosts = expected_host_count(ROOT)
        expected_skills = expected_total_skills(ROOT)
        expected_domains = expected_audit_domain_count(ROOT)
        self.assertGreaterEqual(expected_hosts, 1)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_domains, 1)

        rel_notes = f"docs/release-notes-{active_version}.md"
        mutations: list[tuple[str, str, str, str]] = [
            (
                rel_notes,
                f"{expected_hosts} explicit host installation\nlocations",
                "four explicit host installation\nlocations",
                "stale host count 4",
            ),
            (
                rel_notes,
                f"Total: {expected_skills} skills.",
                "Total: 42 skills.",
                "stale skill count 42",
            ),
            (
                "README.md",
                "diagnostics (`repo-audit-diagnostics`), ",
                "",
                "repo-audit-diagnostics",
            ),
            (
                "README.md",
                f"{expected_domains} audit\ndomains",
                f"{expected_domains - 1} audit\ndomains",
                f"itemized skill sum {expected_skills - 1}",
            ),
            (
                "README.md",
                "and Kiro)",
                "and Cursor)",
                "missing cataloged host 'Kiro'",
            ),
            (
                "docs/architecture.md",
                "`shared/spec-driven-development/`",
                "`shared/legacy/`",
                "shared/spec-driven-development/",
            ),
            (
                "docs/architecture.md",
                "`spec.json`",
                "`legacy.json`",
                "spec.json",
            ),
            (
                "CONTRIBUTING.md",
                "`.github/action-pins.json`",
                "`.github/pins.lock`",
                ".github/action-pins.json",
            ),
            (
                "CONTRIBUTING.md",
                "python scripts/run_checks.py --phase format\n",
                "",
                "--phase format",
            ),
            (
                "AGENTS.md",
                "`.github/action-pins.json`",
                "`.github/pins.lock`",
                ".github/action-pins.json",
            ),
            (
                "docs/validation.md",
                f"Copy-path tests cover {expected_hosts} host layouts",
                "Copy-path tests cover four host layouts",
                "stale host count 4",
            ),
            (
                "docs/implementation-status.md",
                "bounded by actual validation.",
                "bounded by actual validation. There is no claim of being the world's best skill repository.",
                "prompt-guardrail disclaimer",
            ),
            (
                "docs/implementation-status.md",
                f"Explicit copy adapters for {expected_hosts} host layouts",
                f"Explicit copy adapters for {expected_hosts - 1} host layouts",
                f"stale host count {expected_hosts - 1}",
            ),
            (
                "docs/research.md",
                "into standalone skill packages.",
                "into a read-only audit.",
                "obsolete read-only scope claim",
            ),
            (
                "docs/research.md",
                "spec-driven development.",
                "spec-driven development. No claim of measured superiority or market uniqueness is made.",
                "prompt-guardrail disclaimer",
            ),
        ]

        for rel_path, old_sub, new_sub, expected_marker in mutations:
            with self.subTest(file=rel_path, marker=expected_marker):
                self.assertIn(old_sub, originals[rel_path])
                (fixture_root / rel_path).write_text(
                    originals[rel_path].replace(old_sub, new_sub, 1),
                    encoding="utf-8",
                )
                try:
                    errs = _validate_documentation_invariants(fixture_root)
                    self.assertTrue(
                        any(rel_path in err and expected_marker in err for err in errs),
                        f"Expected '{expected_marker}' in {rel_path} errors, got: {errs}",
                    )
                finally:
                    (fixture_root / rel_path).write_text(
                        originals[rel_path], encoding="utf-8"
                    )

        # Also verify CLI exit code 1 when stale count is injected into release notes
        stale_count = expected_skills - 6
        (fixture_root / rel_notes).write_text(
            originals[rel_notes].replace(
                f"Total: {expected_skills} skills.",
                f"Total: {stale_count} skills.",
                1,
            ),
            encoding="utf-8",
        )
        stale_proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/validate_skills.py"),
                "--root",
                str(fixture_root),
                "--no-ledger",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, stale_proc.returncode)
        self.assertIn(f"stale skill count {stale_count}", stale_proc.stdout)

    def test_tagged_release_notes_immutability_enforced_by_validate_and_run_checks(
        self,
    ) -> None:
        git_fixture = self.root / "tagged-notes-immutability"
        git_fixture.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "scripts", git_fixture / "scripts")
        try:
            for dname in (
                "skills",
                "schemas",
                "shared",
                "evals",
                ".github",
                ".claude-plugin",
                ".cursor-plugin",
            ):
                (git_fixture / dname).symlink_to(ROOT / dname, target_is_directory=True)
        except OSError:
            for dname in (
                "skills",
                "schemas",
                "shared",
                "evals",
                ".github",
                ".claude-plugin",
                ".cursor-plugin",
            ):
                shutil.copytree(ROOT / dname, git_fixture / dname)

        active_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        for rel in (
            "VERSION",
            "gemini-extension.json",
            "catalog/skills.json",
            "catalog/suites.json",
            "catalog/hosts.json",
            "catalog/packs.json",
            "catalog/experience.json",
            "catalog/recipes.json",
            "catalog/profiles.json",
            "docs/catalog.md",
            "docs/experience.md",
            "docs/explorer.html",
            "CLAUDE.md",
            "GEMINI.md",
            "README.md",
            "docs/architecture.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "docs/validation.md",
            "docs/implementation-status.md",
            "docs/research.md",
            "docs/release-notes-0.2.0.md",
            f"docs/release-notes-{active_version}.md",
        ):
            dst = git_fixture / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((ROOT / rel).read_bytes())

        def run_git(*args: str) -> None:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "core.autocrlf=false",
                    *args,
                ],
                cwd=git_fixture,
                capture_output=True,
                text=True,
                check=True,
            )

        run_git("init")
        # Tag v0.1.0 without release-notes-0.1.0.md to test retroactive addition guard
        (git_fixture / "VERSION").write_text("0.1.0\n", encoding="utf-8", newline="\n")
        v020_bytes = (git_fixture / "docs/release-notes-0.2.0.md").read_bytes()
        (git_fixture / "docs/release-notes-0.2.0.md").unlink()
        run_git("add", "VERSION")
        run_git("commit", "-m", "Initial 0.1.0")
        run_git("tag", "v0.1.0")

        # Tag v0.2.0 with historical 42-skill release-notes-0.2.0.md
        (git_fixture / "VERSION").write_text("0.2.0\n", encoding="utf-8", newline="\n")
        (git_fixture / "docs/release-notes-0.2.0.md").write_bytes(v020_bytes)
        run_git("add", "VERSION", "docs/release-notes-0.2.0.md")
        run_git("commit", "-m", "Release 0.2.0")
        run_git("tag", "v0.2.0")

        # Advance working tree to active_version (untagged)
        (git_fixture / "VERSION").write_bytes((ROOT / "VERSION").read_bytes())
        run_git("add", "-f", "VERSION", f"docs/release-notes-{active_version}.md")
        run_git("commit", "-m", f"Advance to {active_version}")

        # Clean baseline passes with 42 skills in 0.2.0 notes and 57 skills in active notes
        self.assertEqual([], _validate_documentation_invariants(git_fixture))

        # Editing docs/release-notes-0.2.0.md after refs/tags/v0.2.0 exists fails immutability
        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        (git_fixture / "docs/release-notes-0.2.0.md").write_bytes(
            v020_bytes.replace(
                b"Total: 42 skills.",
                f"Total: {expected_skills} skills.".encode(),
            )
        )
        edited_errs = _validate_documentation_invariants(git_fixture)
        self.assertTrue(
            any(
                "docs/release-notes-0.2.0.md" in err and "immutability" in err
                for err in edited_errs
            ),
            f"Expected immutability error on edited v0.2.0 notes, got: {edited_errs}",
        )

        # Verify scripts/run_checks.py --phase packages fails with immutability error
        checks_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(git_fixture / "scripts/run_checks.py"),
                "--phase",
                "packages",
            ],
            cwd=git_fixture,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, checks_proc.returncode)
        self.assertIn(
            "tagged release notes immutability violation",
            checks_proc.stdout + checks_proc.stderr,
        )

        # Deleting docs/release-notes-0.2.0.md after refs/tags/v0.2.0 exists also fails
        (git_fixture / "docs/release-notes-0.2.0.md").unlink()
        missing_errs = _validate_documentation_invariants(git_fixture)
        self.assertTrue(
            any(
                "docs/release-notes-0.2.0.md" in err and "immutability" in err
                for err in missing_errs
            ),
            f"Expected immutability error on deleted v0.2.0 notes, got: {missing_errs}",
        )
        (git_fixture / "docs/release-notes-0.2.0.md").write_bytes(v020_bytes)

        # Retroactively adding docs/release-notes-0.1.0.md when v0.1.0 had none fails
        (git_fixture / "docs/release-notes-0.1.0.md").write_text(
            "# 0.1.0 — Retroactive notes\n",
            encoding="utf-8",
            newline="\n",
        )
        retro_errs = _validate_documentation_invariants(git_fixture)
        self.assertTrue(
            any(
                "docs/release-notes-0.1.0.md" in err and "immutability" in err
                for err in retro_errs
            ),
            f"Expected immutability error on retroactive v0.1.0 notes, got: {retro_errs}",
        )
        (git_fixture / "docs/release-notes-0.1.0.md").unlink()
        self.assertEqual([], _validate_documentation_invariants(git_fixture))

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

        # Explicit booleans True and False both succeed when paired with matching SKILL.md policy
        orig_entry = self.entry.read_text(encoding="utf-8")
        for valid_bool in (True, False):
            if valid_bool:
                self.entry.write_text(orig_entry, encoding="utf-8")
            else:
                self.entry.write_text(
                    orig_entry.replace(
                        "name: sample-audit\n",
                        "name: sample-audit\ndisable-model-invocation: true\n",
                    ),
                    encoding="utf-8",
                )
            self._write_ui_metadata(
                policy={
                    "products": ["chatgpt", "codex", "api", "atlas"],
                    "allow_implicit_invocation": valid_bool,
                }
            )
            self.assertEqual([], validate_skill(self.skill))
        self.entry.write_text(orig_entry, encoding="utf-8")

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
            '---\nname: second-audit\ndescription: "Audit secondary contracts offline. Use when testing suite rules. Do not use for production."\n---\n\n'
            "# Second\n\nInspect the secondary contract using [worked examples](references/examples.md).\n\n## Deliver\n\n"
            "Deliver the audit output conforming to [audit schema](assets/audit.schema.json), "
            "[audit validator](scripts/validate_audit.py), and [prompt](assets/prompt.md).\n"
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

        expected_skills = expected_total_skills(ROOT)
        expected_suites = expected_suite_count(ROOT)
        expected_names = expected_suite_names(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_suites, 1)
        self.assertGreaterEqual(len(expected_names), 1)
        self.assertEqual(expected_skills, len(all_suite_skills))
        self.assertEqual(expected_names, set(suite_to_skills))

        explicit_only = {
            "repo-apply-fix",
            "repo-audit-diagnostics",
            "repo-audit-program",
            "repo-audit-synthesis",
            "repo-reconcile-findings",
            "repo-specify-regression",
            "test-case-generation",
        }
        self.assertEqual(explicit_only, set(EXPLICIT_INVOCATION_ONLY_SKILLS))
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

                skill_fm = yaml.safe_load(
                    (pkg / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
                )
                policy = ui_doc.get("policy")
                self.assertIsInstance(policy, dict, skill_name)
                allow_implicit = policy.get("allow_implicit_invocation")
                self.assertIsInstance(allow_implicit, bool, skill_name)
                self.assertEqual(expected_products, policy.get("products"), skill_name)
                if skill_name in explicit_only:
                    self.assertIs(allow_implicit, False, skill_name)
                    self.assertIs(
                        skill_fm.get("disable-model-invocation"), True, skill_name
                    )
                else:
                    self.assertIs(allow_implicit, True, skill_name)
                    self.assertNotEqual(
                        skill_fm.get("disable-model-invocation"), True, skill_name
                    )

                prompt_text = prompt_path.read_text(encoding="utf-8")
                self.assertNotIn("```", prompt_text, skill_name)
                self.assertIsNone(heading_re.search(prompt_text), skill_name)

        # Every skill has a distinct SVG and each suite uses one distinct palette color
        self.assertEqual(expected_skills, len(svg_hashes))
        for suite_name, colors in suite_bg_colors.items():
            self.assertEqual(
                1,
                len(colors),
                f"{suite_name} must use a single suite palette: {colors}",
            )
        distinct_suite_colors = {next(iter(c)) for c in suite_bg_colors.values()}
        self.assertEqual(expected_suites, len(distinct_suite_colors))

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
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
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
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
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
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
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

        # Dedicated ## Guardrails section with explicit prohibition and linked companions is accepted
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts offline. Use when testing package rules. Do not use for production."\n---\n\n'
            "# Sample\n\nSee [worked examples](references/examples.md).\n\n"
            "## Guardrails\n\n- Never modify application code without authorization.\n\n"
            "## Deliver\n\nDeliver [audit schema](assets/audit.schema.json) summaries with "
            "[validator](scripts/validate_audit.py) and [prompt](assets/prompt.md).\n",
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

    def test_unreferenced_companion_files_are_rejected_and_sync_exclusions_enforced(
        self,
    ) -> None:
        # 1. Unreferenced references/*.md, assets/*.md, and scripts/*.py are blocked
        orphan_ref = self.skill / "references/unreferenced-notes.md"
        orphan_ref.write_text("# Orphan Notes\n\nUnused text.\n", encoding="utf-8")
        orphan_tpl = self.skill / "assets/unused-template.md"
        orphan_tpl.write_text("# Unused Template\n", encoding="utf-8")
        orphan_script = self.skill / "scripts/extra_helper.py"
        orphan_script.write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8"
        )
        orphan_script.chmod(0o755)

        orphan_errors = validate_skill(self.skill)
        for expected_rel in (
            "references/unreferenced-notes.md",
            "assets/unused-template.md",
            "scripts/extra_helper.py",
        ):
            self.assertTrue(
                any(
                    f"missing markdown link to companion file {expected_rel}" in e
                    for e in orphan_errors
                ),
                f"Expected companion link error for {expected_rel}, got: {orphan_errors}",
            )

        orphan_ref.unlink()
        orphan_tpl.unlink()
        orphan_script.unlink()

        # Dropping the link to assets/prompt.md is also rejected
        orig_entry = self.entry.read_text(encoding="utf-8")
        self.entry.write_text(
            orig_entry.replace("[prompt](assets/prompt.md)", "`assets/prompt.md`"),
            encoding="utf-8",
        )
        prompt_unlinked = validate_skill(self.skill)
        self.assertTrue(
            any(
                "missing markdown link to companion file assets/prompt.md" in e
                for e in prompt_unlinked
            ),
            prompt_unlinked,
        )
        self.entry.write_text(orig_entry, encoding="utf-8")
        self.assertEqual([], validate_skill(self.skill))

        # 2. Per-skill exclude in shared manifest drops unused templates on sync and flags drift on --check
        diag_dir = ROOT / "skills/repo-audit-diagnostics"
        self.assertFalse((diag_dir / "assets/report-template.md").exists())
        self.assertFalse((diag_dir / "assets/handoff-template.md").exists())

        shared_dir = self.root / "shared/repo-audit/resources/assets"
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "report-template.md").write_text("# Report\n", encoding="utf-8")
        (self.root / "shared/repo-audit/manifest.json").write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "suite": "repository-audits",
                    "files": ["assets/report-template.md"],
                    "exclude": {"sample-audit": ["assets/report-template.md"]},
                }
            ),
            encoding="utf-8",
        )
        (self.root / "catalog").mkdir(exist_ok=True)
        (self.root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "repository-audits",
                            "phases": [
                                {"id": 1, "title": "Init", "skills": ["sample-audit"]}
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        stale = self.skill / "assets/report-template.md"
        stale.write_text("# Stale\n", encoding="utf-8")
        check_problems = sync(self.root, check=True)
        self.assertIn(
            "Unexpected excluded resource in skill: sample-audit/assets/report-template.md",
            check_problems,
        )
        self.assertEqual([], sync(self.root, check=False))
        self.assertFalse(stale.exists())

    def test_skill_markdown_missing_schema_link_is_rejected(self):
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
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
        self.examples_file.write_text(
            "# Worked Examples\n\n```python\nx = 1\n```\n\n```json\n"
            '{"by_category": {"auth": 2}, "details": {"scope": "api"}}\n```\n',
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
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
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
        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        errors, telemetry = validate_with_telemetry(ROOT)
        self.assertEqual([], errors)
        self.assertEqual(expected_skills, telemetry["total_packages"])
        self.assertEqual(expected_skills, telemetry["passed_packages"])
        self.assertEqual(0, telemetry["failed_packages"])
        self.assertEqual(1.0, telemetry["first_pass_guardrail_pass_rate"])
        self.assertEqual(100.0, telemetry["first_pass_guardrail_pass_rate_percentage"])
        self.assertEqual(1.0, telemetry["deterministic_constraint_coverage"])
        self.assertEqual(
            100.0, telemetry["deterministic_constraint_coverage_percentage"]
        )
        self.assertEqual(1.0, telemetry["output_schema_coverage"])
        self.assertEqual(100.0, telemetry["output_schema_coverage_percentage"])
        self.assertEqual(1.0, telemetry["routing_trigger_coverage"])
        self.assertEqual(100.0, telemetry["routing_trigger_coverage_percentage"])
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
        self.assertEqual(expected_skills, exported["total_packages"])
        self.assertEqual(1.0, exported["first_pass_guardrail_pass_rate"])
        self.assertEqual(1.0, exported["routing_trigger_coverage"])
        self.assertEqual(100.0, exported["routing_trigger_coverage_percentage"])

    def test_all_skills_have_output_format_schemas(self):
        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        skills_dir = ROOT / "skills"
        packages = sorted(
            p for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        )
        self.assertEqual(len(packages), expected_skills)
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
                "# Symlink UI\n\nRead [examples](references/examples.md).\n\n## Deliver\n\n"
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
            "description": "Audit synthetic contracts offline. Use when testing package rules. Do not use for production.",
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

        # CLI installation preserves .cursor-plugin/plugin.json in installed destination
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
        self.assertTrue((installed_dir / ".cursor-plugin/plugin.json").is_file())

        # Root .cursor-plugin/plugin.json and unsafe marketplace paths are rejected
        plugin_json.write_text(json.dumps(valid_manifest), encoding="utf-8")
        self.manifest()
        root_cursor_dir = self.root / ".cursor-plugin"
        root_cursor_dir.mkdir(parents=True, exist_ok=True)
        root_plugin = root_cursor_dir / "plugin.json"
        root_plugin.write_text(json.dumps(valid_manifest), encoding="utf-8")
        errs_root_plugin, _ = validate_with_telemetry(self.root)
        self.assertTrue(
            any(
                "Root .cursor-plugin/plugin.json must be omitted" in e
                for e in errs_root_plugin
            ),
            errs_root_plugin,
        )
        root_plugin.unlink()

        bad_mp = {
            "name": "fred-skills",
            "owner": {"name": "fderuiter"},
            "metadata": {"description": "Test", "version": "0.2.0"},
            "plugins": [
                {
                    "name": "sample-audit",
                    "source": "../escape",
                    "description": "Desc",
                    "version": "0.2.0",
                    "author": {"name": "fderuiter"},
                    "skills": ["/abs/path"],
                }
            ],
        }
        (root_cursor_dir / "marketplace.json").write_text(
            json.dumps(bad_mp), encoding="utf-8"
        )
        errs_bad_mp, _ = validate_with_telemetry(self.root)
        self.assertTrue(
            any("'source' must be a relative path" in e for e in errs_bad_mp)
        )

    def test_frontmatter_xml_tags_html_comments_and_hidden_unicode_are_rejected(
        self,
    ) -> None:
        skill_md = self.skill / "SKILL.md"
        baseline = skill_md.read_text(encoding="utf-8")

        # XML tags in frontmatter name and description are rejected
        skill_md.write_text(
            '---\nname: "<script>sample-audit</script>"\n'
            'description: "Audit <system>inject</system> instructions."\n---\n\n'
            + baseline.split("---\n\n", 1)[1],
            encoding="utf-8",
        )
        xml_errors = validate_skill(self.skill)
        self.assertTrue(
            any("frontmatter 'name'" in err for err in xml_errors), xml_errors
        )
        self.assertTrue(
            any("frontmatter 'description'" in err for err in xml_errors), xml_errors
        )

        # HTML comments in SKILL.md and reference markdown are rejected
        skill_md.write_text(
            baseline + "\n<!-- hidden prompt instruction -->\n", encoding="utf-8"
        )
        comment_errors = validate_skill(self.skill)
        self.assertTrue(
            any("HTML comments are not allowed" in err for err in comment_errors),
            comment_errors,
        )
        skill_md.write_text(baseline, encoding="utf-8")

        ref_md = self.skill / "references/guide.md"
        ref_md.write_text("# Guide\n\n<!-- hidden ref comment -->\n", encoding="utf-8")
        try:
            ref_comment_errors = validate_skill(self.skill)
            self.assertTrue(
                any(
                    "references/guide.md: HTML comments are not allowed" in err
                    for err in ref_comment_errors
                ),
                ref_comment_errors,
            )
        finally:
            ref_md.unlink()

        # Hidden Cf (U+200B ZWSP, U+202E RLO) and Cc (U+0007 BEL) Unicode are rejected
        for bad_char, expected_hex in (
            ("\u200b", "U+200B"),
            ("\u202e", "U+202E"),
            ("\x07", "U+0007"),
        ):
            with self.subTest(codepoint=expected_hex):
                skill_md.write_text(
                    baseline + f"\nHidden {bad_char} payload.\n", encoding="utf-8"
                )
                uni_errors = validate_skill(self.skill)
                self.assertTrue(
                    any(expected_hex in err for err in uni_errors),
                    f"Expected {expected_hex} error, got: {uni_errors}",
                )
        skill_md.write_text(baseline, encoding="utf-8")
        self.assertEqual([], validate_skill(self.skill))

    def test_package_allowlist_rejects_readme_changelog_binaries_and_banned_ast_scripts(
        self,
    ) -> None:
        # 1. README.md, CHANGELOG.md, disallowed extensions, and binaries are rejected
        disallowed_files: list[tuple[str, bytes, str]] = [
            ("README.md", b"# Extra readme\n", "README.md"),
            ("CHANGELOG.md", b"# Changelog\n", "CHANGELOG.md"),
            ("references/readme.md", b"# Nested readme\n", "references/readme.md"),
            ("helper.py", b"x = 1\n", "helper.py"),
            ("scripts/sub/nested.py", b"x = 1\n", "scripts/sub/nested.py"),
            ("scripts/runner.sh", b"#!/bin/sh\necho hi\n", "scripts/runner.sh"),
            ("assets/blob.bin", b"\x00\x01\x02\x03", "assets/blob.bin"),
            ("scripts/nul.py", b"x = 1\x00\n", "scripts/nul.py"),
            ("references/bad_utf8.md", b"\xff\xfe\xfd", "references/bad_utf8.md"),
        ]
        for rel_path, content, marker in disallowed_files:
            target = self.skill / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            try:
                errs = validate_skill(self.skill)
                self.assertTrue(
                    any(marker in e for e in errs),
                    f"Expected rejection naming {marker}, got: {errs}",
                )
            finally:
                target.unlink()

        # 2. Safe offline stdlib script (including re.compile and shlex.quote) passes
        original_skill_md = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        scripts_dir = self.skill / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        script_file = scripts_dir / "offline_helper.py"
        script_file.write_text(
            "import json\nimport re\nimport shlex\n\n"
            "PAT = re.compile(r'[a-z]+')\n"
            "def quote_arg(val: str) -> str:\n"
            "    return shlex.quote(json.dumps(bool(PAT.match(val))))\n",
            encoding="utf-8",
        )
        (self.skill / "SKILL.md").write_text(
            original_skill_md
            + "\n- Run [scripts/offline_helper.py](scripts/offline_helper.py) when quoting offline helper arguments.\n",
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

        # 3. Network, subprocess, pty, ctypes, importlib, os.system, and eval/exec/compile fail AST vetting
        banned_snippets = [
            "import urllib.request\n",
            "from http.client import HTTPSConnection\n",
            "import socket\n",
            "import requests\n",
            "import subprocess\n",
            "import pty\n",
            "import ctypes\n",
            "import importlib\n",
            "import os\nos.system('ls')\n",
            "eval('1 + 1')\n",
            "exec('x = 1')\n",
            "compile('1', '<str>', 'eval')\n",
            "__import__('socket')\n",
        ]
        try:
            for snippet in banned_snippets:
                with self.subTest(snippet=snippet.strip()):
                    script_file.write_text(snippet, encoding="utf-8")
                    ast_errs = validate_skill(self.skill)
                    self.assertTrue(
                        any("scripts/offline_helper.py" in e for e in ast_errs),
                        f"Expected AST rejection for {snippet!r}, got: {ast_errs}",
                    )
        finally:
            script_file.unlink()
            (self.skill / "SKILL.md").write_text(original_skill_md, encoding="utf-8")

    def test_all_repository_skill_prompt_bodies_are_unique_after_line_one(self) -> None:
        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        errors, telemetry = validate_with_telemetry(ROOT, record_attempts=False)
        self.assertEqual([], errors)
        self.assertEqual(expected_skills, telemetry["total_packages"])

        bodies_by_skill: dict[str, str] = {}
        for prompt_path in sorted((ROOT / "skills").glob("*/assets/prompt.md")):
            lines = prompt_path.read_text(encoding="utf-8").splitlines()
            self.assertGreater(
                len(lines), 1, f"{prompt_path} must have a multi-line body"
            )
            normalized = " ".join(" ".join(lines[1:]).split())
            self.assertTrue(normalized, f"{prompt_path} body after line 1 is empty")
            bodies_by_skill[prompt_path.parent.parent.name] = normalized

        self.assertEqual(expected_skills, len(bodies_by_skill))
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
            "# Twin\n\nRead [examples](references/examples.md).\n\n## Deliver\n\n"
            "Deliver [audit schema](assets/audit.schema.json). Do not modify code.\n",
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

    def test_description_triggers_vague_phrases_acronyms_collisions_and_telemetry(
        self,
    ) -> None:
        import re

        from skill_tools import read_frontmatter

        # 1. All cataloged repository skills include an explicit 'Use when' trigger clause.
        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        repo_skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(expected_skills, len(repo_skills))
        for skill_md in repo_skills:
            meta, _ = read_frontmatter(skill_md)
            self.assertRegex(
                meta["description"],
                re.compile(r"\buse when\s+\S+", re.IGNORECASE),
                f"{skill_md.parent.name} missing 'Use when' trigger",
            )

        body_tail = (
            "# API Sample Design\n\nInspect the supplied contract using [worked examples](references/examples.md) and the [prompt](assets/prompt.md).\n\n## Deliver\n\n"
            "Deliver the audit output conforming to [audit schema](assets/audit.schema.json) and verified with [audit validator](scripts/validate_audit.py).\n"
            "Do not modify application code without explicit user authorization.\n"
        )

        def write_sample(desc: str, heading_body: str = body_tail) -> None:
            self.entry.write_text(
                f'---\nname: sample-audit\ndescription: "{desc}"\n---\n\n'
                + heading_body,
                encoding="utf-8",
            )

        # 2. Reject "x", missing triggers, 'Use after', and <4 content-word triggers.
        for missing_or_short in (
            "x",
            "Diagnose stalled repository audits and generate recovery bundles.",
            "Consolidate reports. Use after one or more repository audits finish.",
            "Audit synthetic contracts. Use when requested.",
        ):
            with self.subTest(missing_or_short=missing_or_short):
                write_sample(missing_or_short)
                errs = validate_skill(self.skill)
                self.assertTrue(
                    any("'Use when' trigger clause" in e for e in errs),
                    f"Expected 'Use when' error for {missing_or_short!r}, got: {errs}",
                )

        # 3. Reject vague catch-all phrases both alone and inside 'Use when' clauses.
        for vague_desc in (
            "Use for productivity tasks",
            "Use when handling general tasks or productivity tasks across repositories.",
            "Helps with code reviews. Use when reviewing pull request changes as needed.",
        ):
            with self.subTest(vague_desc=vague_desc):
                write_sample(vague_desc)
                errs = validate_skill(self.skill)
                self.assertTrue(
                    any("vague catch-all phrasing" in e for e in errs),
                    f"Expected vague-phrase rejection for {vague_desc!r}, got: {errs}",
                )

        # 4. Reject lowercased heading acronyms while allowing uppercase acronyms & normal title words.
        write_sample(
            "Audit api design consistency. Use when inspecting API endpoint signatures, pagination cursors, or error shapes."
        )
        acronym_errs = validate_skill(self.skill)
        self.assertTrue(
            any("lowercases heading acronym 'API'" in e for e in acronym_errs),
            f"Expected lowercase acronym error, got: {acronym_errs}",
        )

        write_sample(
            "Audit API design consistency. Use when inspecting sample design signatures or cursors. Do not use for live patches."
        )
        self.assertEqual([], validate_skill(self.skill))

        # Valid workflow description without heading acronyms passes without false acronym errors.
        write_sample(
            "Verify candidate defect claims offline. Use when reproducing or narrowing a repository finding. Do not use for edits."
        )
        self.assertEqual([], validate_skill(self.skill))

        # 5. Cross-suite bigram collisions, intra-suite exemption, missing suites.json, and telemetry.
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )
        self._write_ui_metadata(
            self.skill,
            skill_name="sample-audit",
            short_description="Inspect synthetic behavioral contracts safely",
        )

        # Missing trigger yields 0.0 / 0.0% routing_trigger_coverage.
        write_sample("x")
        errs_x, tel_x = validate_with_telemetry(self.root, record_attempts=False)
        self.assertTrue(errs_x)
        self.assertEqual(0.0, tel_x["routing_trigger_coverage"])
        self.assertEqual(0.0, tel_x["routing_trigger_coverage_percentage"])

        # Restore valid sample-audit and add a second skill sharing bigram 'behavioral contracts'.
        write_sample(
            "Audit behavioral contracts with static checks. Use when mapping undocumented caller obligations. Do not use for fixes."
        )
        peer_skill = self.root / "skills/peer-skill"
        shutil.copytree(self.skill, peer_skill)
        (peer_skill / "SKILL.md").write_text(
            '---\nname: peer-skill\ndescription: "Generate automated test suites. Use when testing behavioral contracts or isolated oracles. Do not use for triage."\n---\n\n'
            "# Peer Skill\n\nSee [worked examples](references/examples.md) and [prompt](assets/prompt.md).\n\n## Deliver\n\n"
            "Deliver [audit schema](assets/audit.schema.json) verified with [audit validator](scripts/validate_audit.py). Do not modify code.\n",
            encoding="utf-8",
        )
        (peer_skill / "assets/prompt.md").write_text(
            "Use peer-skill on <repository> at <revision> conforming to assets/audit.schema.json. "
            "Never mutate production files.\n",
            encoding="utf-8",
        )
        self.scenarios_file.write_text(
            self.scenarios_file.read_text(encoding="utf-8").replace(
                "sample-sibling-audit", "peer-skill"
            ),
            encoding="utf-8",
        )
        scenarios_path = peer_skill / "references/evaluation-scenarios.json"
        scenarios_path.write_text(
            scenarios_path.read_text(encoding="utf-8")
            .replace("sample-audit", "peer-skill")
            .replace("sample-sibling-audit", "sample-audit")
            .replace("expected findings", "expected outputs"),
            encoding="utf-8",
        )
        self._write_ui_metadata(
            peer_skill,
            skill_name="peer-skill",
            short_description="Generate automated checks for peer skill",
        )

        # Without catalog/suites.json, cross-suite check is skipped cleanly.
        no_suites_errs, no_suites_tel = validate_with_telemetry(
            self.root, record_attempts=False
        )
        self.assertEqual([], no_suites_errs)
        self.assertEqual(1.0, no_suites_tel["routing_trigger_coverage"])
        self.assertEqual(100.0, no_suites_tel["routing_trigger_coverage_percentage"])

        # Intra-suite specialist pair in the same suite is exempt.
        (self.root / "catalog").mkdir(exist_ok=True)
        (self.root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "repository-audits",
                            "phases": [
                                {
                                    "id": 1,
                                    "title": "Discovery",
                                    "skills": ["sample-audit", "peer-skill"],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        intra_errs, _ = validate_with_telemetry(self.root, record_attempts=False)
        self.assertEqual([], intra_errs)

        # Cross-suite pair sharing 'behavioral contracts' bigram is rejected.
        (self.root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "repository-audits",
                            "phases": [
                                {
                                    "id": 1,
                                    "title": "Discovery",
                                    "skills": ["sample-audit"],
                                }
                            ],
                        },
                        {
                            "name": "repository-workflows",
                            "phases": [
                                {
                                    "id": 0,
                                    "title": "Workflows",
                                    "skills": ["peer-skill"],
                                }
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        cross_errs, _ = validate_with_telemetry(self.root, record_attempts=False)
        self.assertEqual(1, len(cross_errs), f"Unexpected errors: {cross_errs}")
        self.assertIn("Cross-suite description bigram collision", cross_errs[0])
        self.assertIn("behavioral contracts", cross_errs[0])

    def test_worked_examples_count_link_and_json_schema_validation(self) -> None:
        # 1. Missing worked examples file is rejected
        self.examples_file.unlink()
        missing_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Missing worked examples" in e for e in missing_errors),
            missing_errors,
        )

        # 2. Fewer than 2 fenced blocks is rejected
        self.examples_file.write_text(
            '# Examples\n\n```json\n{"schema_version": "1.0"}\n```\n',
            encoding="utf-8",
        )
        count_errors = validate_skill(self.skill)
        self.assertTrue(
            any("at least 2 fenced code blocks" in e for e in count_errors),
            count_errors,
        )

        # 3. Missing JSON block is rejected
        self.examples_file.write_text(
            "# Examples\n\n```python\nx = 1\n```\n\n```python\ny = 2\n```\n",
            encoding="utf-8",
        )
        no_json_errors = validate_skill(self.skill)
        self.assertTrue(
            any("at least 1 fenced json output example" in e for e in no_json_errors),
            no_json_errors,
        )

        # 4. Schema-invalid JSON worked example is rejected
        self.examples_file.write_text(
            '# Examples\n\n```python\nx = 1\n```\n\n```json\n{"schema_version": "9.9"}\n```\n',
            encoding="utf-8",
        )
        bad_schema_errors = validate_skill(self.skill)
        self.assertTrue(
            any("fails output schema validation" in e for e in bad_schema_errors),
            bad_schema_errors,
        )

        # 5. Unlinked worked examples file is rejected
        self.examples_file.write_text(
            '# Examples\n\n```python\nx = 1\n```\n\n```json\n{"schema_version": "1.0"}\n```\n',
            encoding="utf-8",
        )
        self.entry.write_text(
            '---\nname: sample-audit\ndescription: "Audit synthetic contracts with deterministic checks. Use when inspecting synthetic contract fixtures, boundary rules, or schema compliance."\n---\n\n'
            "# Sample\n\n## Deliver\n\nDeliver [audit schema](assets/audit.schema.json). "
            "Do not modify application code.\n",
            encoding="utf-8",
        )
        unlinked_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "missing markdown link to worked examples references/examples.md" in e
                for e in unlinked_errors
            ),
            unlinked_errors,
        )

    def test_protocol_depth_rejects_reference_inversion(self) -> None:
        self.entry.write_text(
            self.entry.read_text(encoding="utf-8").replace(
                "# Sample\n\n",
                "# Sample\n\nRead [review protocol](references/review-protocol.md).\n\n",
            ),
            encoding="utf-8",
        )
        proto = self.skill / "references/review-protocol.md"
        proto.write_text("# Shallow Protocol\n\nToo short.\n", encoding="utf-8")
        inv_errors = validate_skill(self.skill)
        self.assertTrue(
            any("reference inversion detected" in e for e in inv_errors),
            inv_errors,
        )

        deep_lines = ["# Deep Review Protocol", ""] + [
            f"- Rule {i}: Verify boundary precondition and evidence line spans before promotion."
            for i in range(1, 28)
        ]
        proto.write_text("\n".join(deep_lines) + "\n", encoding="utf-8")
        self.assertEqual([], validate_skill(self.skill))

    def test_method_uniqueness_rejects_boilerplate_duplication_and_skill_copying(
        self,
    ) -> None:
        schemas_dir = self.root / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            ROOT / "schemas/evaluation-scenarios.schema.json",
            schemas_dir / "evaluation-scenarios.schema.json",
        )
        self.examples_file.unlink()
        method_a = self.skill / "references/method.md"
        method_a.write_text(
            "# Specialist Method A\n\n"
            "Do not modify application code without explicit user authorization.\n\n"
            "```python\nx = 1\n```\n\n"
            '```json\n{"schema_version": "1.0"}\n```\n',
            encoding="utf-8",
        )
        self.entry.write_text(
            self.entry.read_text(encoding="utf-8").replace(
                "references/examples.md", "references/method.md"
            ),
            encoding="utf-8",
        )
        verbatim_errors = validate_skill(self.skill)
        self.assertTrue(
            any("repeats SKILL.md prose verbatim" in e for e in verbatim_errors),
            verbatim_errors,
        )

        shared_boilerplate = "Shared boilerplate sentence copied across specialists."
        method_a.write_text(
            f"# Specialist Method A\n\n{shared_boilerplate}\n\n"
            "```python\nx = 1\n```\n\n"
            '```json\n{"schema_version": "1.0"}\n```\n',
            encoding="utf-8",
        )
        self.assertEqual([], validate_skill(self.skill))

        second = self.root / "skills/second-specialist"
        shutil.copytree(self.skill, second)
        (second / "SKILL.md").write_text(
            '---\nname: second-specialist\ndescription: "Audit second specialist domain. Use when checking secondary preconditions or invariants. Do not use for live edits."\n---\n\n'
            "# Second Specialist\n\nRead [method](references/method.md) and [prompt](assets/prompt.md).\n\n## Deliver\n\n"
            "Emit [audit schema](assets/audit.schema.json) with [validator](scripts/validate_audit.py). Do not modify application code.\n",
            encoding="utf-8",
        )
        (second / "references/method.md").write_text(
            f"# Specialist Method B\n\n{shared_boilerplate}\n\n"
            "```python\ny = 2\n```\n\n"
            '```json\n{"schema_version": "1.0"}\n```\n',
            encoding="utf-8",
        )
        dup_errors, dup_tel = validate_with_telemetry(self.root, record_attempts=False)
        self.assertTrue(
            any(
                "Specialist references/method.md boilerplate duplication detected" in e
                for e in dup_errors
            ),
            dup_errors,
        )
        self.assertGreater(dup_tel["method_boilerplate_duplication_percentage"], 0.0)

    def test_bundled_validator_script_presence_shebang_and_permissions(self) -> None:
        import os

        # Missing shebang
        self.validator_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        shebang_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Python shebang" in e for e in shebang_errors), shebang_errors
        )

        # Restore shebang, drop execute bit on POSIX
        self.validator_script.write_text(
            "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8"
        )
        if os.name != "nt":
            self.validator_script.chmod(0o644)
            perm_errors = validate_skill(self.skill)
            self.assertTrue(
                any("executable permissions" in e for e in perm_errors), perm_errors
            )
            self.validator_script.chmod(0o755)

        # Missing validator script
        self.validator_script.unlink()
        missing_script_errors = validate_skill(self.skill)
        self.assertTrue(
            any("Missing bundled validator script" in e for e in missing_script_errors),
            missing_script_errors,
        )

    def test_all_57_skills_worked_examples_protocols_and_validators_parity(
        self,
    ) -> None:
        import re
        import tomllib

        expected_skills = expected_total_skills(ROOT)
        expected_non_audit = expected_non_audit_skill_count(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_non_audit, 1)
        errors, telemetry = validate_with_telemetry(ROOT, record_attempts=False)
        self.assertEqual([], errors)
        self.assertEqual(expected_skills, telemetry["total_packages"])
        self.assertEqual(100.0, telemetry["skill_worked_example_coverage_percentage"])
        self.assertEqual(100.0, telemetry["validator_script_coverage_percentage"])
        self.assertEqual(0.0, telemetry["method_boilerplate_duplication_percentage"])

        self.assertEqual([], sync(ROOT, check=True))

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        non_audit_skills: list[str] = []
        for s_obj in suites_doc["suites"]:
            members = [sk for p in s_obj["phases"] for sk in p["skills"]]
            if s_obj["name"] != "repository-audits":
                non_audit_skills.extend(members)
        self.assertEqual(expected_non_audit, len(non_audit_skills))

        for skill_name in non_audit_skills:
            pkg = ROOT / "skills" / skill_name
            skill_text = (pkg / "SKILL.md").read_text(encoding="utf-8")
            protos = list((pkg / "references").glob("*-protocol.md"))
            self.assertEqual(
                1, len(protos), f"Expected 1 *-protocol.md in {skill_name}"
            )
            proto_text = protos[0].read_text(encoding="utf-8")
            self.assertGreater(
                len(proto_text.splitlines()),
                len(skill_text.splitlines()),
                f"{skill_name} protocol lines must exceed SKILL.md lines",
            )
            self.assertGreater(len(proto_text), len(skill_text))

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        excludes = pyproject["tool"]["mypy"]["exclude"]
        for synced_sample in (
            "skills/repo-audit-concurrency/scripts/validate_audit.py",
            r"skills\repo-audit-concurrency\scripts\validate_audit.py",
            "skills/repo-audit-agent-configs/scripts/validate_audit.py",
            "skills/repo-plan-fix/scripts/validate_workflow.py",
            r"skills\repo-plan-fix\scripts\validate_workflow.py",
            "skills/arch-decision-record/scripts/validate_architecture.py",
            "skills/review-diff-analysis/scripts/validate_review.py",
            "skills/test-gap-analysis/scripts/validate_testing.py",
            "skills/spec-authoring/scripts/validate_spec.py",
            r"skills\spec-authoring\scripts\validate_spec.py",
        ):
            self.assertTrue(
                any(re.search(pat, synced_sample) for pat in excludes),
                f"Synced validator {synced_sample} must be excluded from duplicate mypy module discovery",
            )
        for canonical_sample in (
            "scripts/validate_audit.py",
            "scripts/validate_workflow.py",
            "scripts/validate_architecture.py",
            "scripts/validate_review.py",
            "scripts/validate_testing.py",
            "scripts/validate_spec.py",
        ):
            self.assertFalse(
                any(re.search(pat, canonical_sample) for pat in excludes),
                f"Canonical validator {canonical_sample} must remain typechecked by mypy",
            )

    def test_description_contract_rejects_missing_clauses_and_length_overflow(
        self,
    ) -> None:
        orig_entry = self.entry.read_text(encoding="utf-8")

        def _set_desc(desc: str) -> list[str]:
            self.entry.write_text(
                orig_entry.replace(
                    "Audit synthetic contracts offline. Use when testing package rules. Do not use for production.",
                    desc,
                ),
                encoding="utf-8",
            )
            return validate_skill(self.skill)

        # 1. Missing leading capability clause
        no_cap_errors = _set_desc(
            "Use when testing package rules. Do not use for production."
        )
        self.assertTrue(
            any("must state capability before 'Use when'" in e for e in no_cap_errors),
            no_cap_errors,
        )

        # 2. Missing Use when trigger clause
        no_trigger_errors = _set_desc(
            "Audit synthetic contracts offline. Do not use for production."
        )
        self.assertTrue(
            any("missing 'Use when' trigger clause" in e for e in no_trigger_errors),
            no_trigger_errors,
        )

        # 3. Missing Do not use negative-boundary clause
        no_boundary_errors = _set_desc(
            "Audit synthetic contracts offline. Use when testing package rules."
        )
        self.assertTrue(
            any(
                "missing 'Do not use' negative-boundary clause" in e
                for e in no_boundary_errors
            ),
            no_boundary_errors,
        )

        # 4. Exceeding 140 characters despite having all three clauses
        over_140 = (
            "Audit synthetic behavioral contracts across complex multi-service boundaries. "
            "Use when testing package validation rules. Do not use for live fixes."
        )
        self.assertGreater(len(over_140), 140)
        len_errors = _set_desc(over_140)
        self.assertTrue(
            any("exceeds 140 characters" in e for e in len_errors), len_errors
        )

        self.entry.write_text(orig_entry, encoding="utf-8")
        self.assertEqual([], validate_skill(self.skill))

    def test_stage1_budgets_and_cross_suite_jaccard_gates(self) -> None:
        from validate_skills import (
            _validate_cross_suite_jaccard,
            _validate_stage1_char_budgets,
        )

        # Verify live suite meets all Stage-1 character budgets and cross-suite Jaccard caps
        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        packs_doc = json.loads(
            (ROOT / "catalog/packs.json").read_text(encoding="utf-8")
        )
        live_descs: dict[str, str] = {}
        skill_to_suite: dict[str, str] = {}
        for s_obj in suites_doc["suites"]:
            for phase in s_obj["phases"]:
                for sk in phase["skills"]:
                    skill_to_suite[sk] = s_obj["name"]
                    fm = yaml.safe_load(
                        (ROOT / "skills" / sk / "SKILL.md")
                        .read_text(encoding="utf-8")
                        .split("---", 2)[1]
                    )
                    live_descs[sk] = fm["description"]
                    self.assertLessEqual(len(fm["description"]), 140, sk)

        total_raw = sum(len(k) + len(v) for k, v in live_descs.items())
        self.assertLess(total_raw, 8000)
        starter_raw = sum(
            len(sk) + len(live_descs[sk])
            for sk in packs_doc["packs"]["starter"]["skills"]
        )
        self.assertLessEqual(starter_raw, 1500)
        curated_covered: set[str] = set()
        for pack_name, pack_obj in packs_doc["packs"].items():
            if pack_name in {"complete", "full-audit"}:
                continue
            curated_covered.update(pack_obj["skills"])
            pack_raw = sum(len(sk) + len(live_descs[sk]) for sk in pack_obj["skills"])
            self.assertLess(pack_raw, 2500, pack_name)
        self.assertEqual(set(live_descs), curated_covered)

        self.assertEqual([], _validate_stage1_char_budgets(ROOT, live_descs))
        self.assertEqual([], _validate_cross_suite_jaccard(ROOT, live_descs))

        # Verify cross-suite Jaccard overlap > 0.20 is rejected while same-suite overlap is ignored
        jaccard_root = self.root / "jaccard-fixture"
        (jaccard_root / "catalog").mkdir(parents=True, exist_ok=True)
        overlap_descs = {
            "alpha-skill": "Audit boundary contracts and trust payloads. Use when reviewing inputs. Do not use for patches.",
            "beta-skill": "Specify boundary contracts and trust payloads. Use when designing inputs. Do not use for audits.",
        }
        (jaccard_root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "suites": [
                        {
                            "name": "suite-a",
                            "phases": [{"id": 1, "skills": ["alpha-skill"]}],
                        },
                        {
                            "name": "suite-b",
                            "phases": [{"id": 1, "skills": ["beta-skill"]}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        jaccard_errors = _validate_cross_suite_jaccard(jaccard_root, overlap_descs)
        self.assertTrue(
            any("Cross-suite description overlap" in e for e in jaccard_errors),
            jaccard_errors,
        )
        (jaccard_root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "suites": [
                        {
                            "name": "suite-a",
                            "phases": [
                                {"id": 1, "skills": ["alpha-skill", "beta-skill"]}
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual([], _validate_cross_suite_jaccard(jaccard_root, overlap_descs))

        # Verify Stage-1 pack budget overflow rejections while exempting umbrella packs and repository totals (>= 8000)
        (self.root / "catalog").mkdir(exist_ok=True)
        synth_descs = {f"skill-{i:02d}": "x" * 130 for i in range(64)}
        (self.root / "catalog/suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "repository-audits",
                            "phases": [
                                {"id": 1, "title": "P1", "skills": list(synth_descs)}
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "catalog/packs.json").write_text(
            json.dumps(
                {
                    "packs": {
                        "starter": {"skills": [f"skill-{i:02d}" for i in range(12)]},
                        "focused": {"skills": [f"skill-{i:02d}" for i in range(20)]},
                        "complete": {"skills": list(synth_descs)},
                    }
                }
            ),
            encoding="utf-8",
        )
        budget_errors = _validate_stage1_char_budgets(self.root, synth_descs)
        self.assertFalse(
            any(
                "Aggregate Stage-1" in e or "Pack 'complete'" in e
                for e in budget_errors
            ),
            budget_errors,
        )
        self.assertTrue(
            any(
                "Pack 'starter'" in e and "1500 raw characters" in e
                for e in budget_errors
            ),
            budget_errors,
        )
        self.assertTrue(
            any(
                "Pack 'focused'" in e and "2500 raw characters" in e
                for e in budget_errors
            ),
            budget_errors,
        )
        self.assertTrue(
            any(
                "Skill 'skill-20' in catalog/suites.json must belong to at least one curated pack"
                in e
                for e in budget_errors
            ),
            budget_errors,
        )

        # When all 64 skills (> 8,000 aggregate raw chars) belong to bounded curated packs (< 2,500), zero Stage-1 errors
        bounded_packs = {
            f"domain-{group_idx}": {
                "skills": [
                    f"skill-{i:02d}"
                    for i in range(group_idx * 16, (group_idx + 1) * 16)
                ]
            }
            for group_idx in range(4)
        }
        bounded_packs["starter"] = {"skills": [f"skill-{i:02d}" for i in range(6)]}
        bounded_packs["complete"] = {"skills": list(synth_descs)}
        (self.root / "catalog/packs.json").write_text(
            json.dumps({"packs": bounded_packs}),
            encoding="utf-8",
        )
        self.assertEqual([], _validate_stage1_char_budgets(self.root, synth_descs))

    def test_invocation_policy_parity_and_guarded_skills_rejected_when_mismatched(
        self,
    ) -> None:
        orig_entry = self.entry.read_text(encoding="utf-8")

        # 1. Non-boolean disable-model-invocation in SKILL.md is rejected
        self.entry.write_text(
            orig_entry.replace(
                "name: sample-audit\n",
                'name: sample-audit\ndisable-model-invocation: "true"\n',
            ),
            encoding="utf-8",
        )
        non_bool_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "'disable-model-invocation' must be a boolean" in e
                for e in non_bool_errors
            ),
            non_bool_errors,
        )

        # 2. disable-model-invocation: true paired with allow_implicit_invocation: true is rejected
        self.entry.write_text(
            orig_entry.replace(
                "name: sample-audit\n",
                "name: sample-audit\ndisable-model-invocation: true\n",
            ),
            encoding="utf-8",
        )
        self._write_ui_metadata(policy={"allow_implicit_invocation": True})
        mismatch_true_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "Invocation policy mismatch: SKILL.md 'disable-model-invocation' (True) conflicts with agents/openai.yaml 'policy.allow_implicit_invocation' (True)"
                in e
                for e in mismatch_true_errors
            ),
            mismatch_true_errors,
        )

        # 3. Omitted disable-model-invocation paired with allow_implicit_invocation: false is rejected
        self.entry.write_text(orig_entry, encoding="utf-8")
        self._write_ui_metadata(policy={"allow_implicit_invocation": False})
        mismatch_false_errors = validate_skill(self.skill)
        self.assertTrue(
            any(
                "Invocation policy mismatch: SKILL.md 'disable-model-invocation' (False) conflicts with agents/openai.yaml 'policy.allow_implicit_invocation' (False)"
                in e
                for e in mismatch_false_errors
            ),
            mismatch_false_errors,
        )

        # 4. Guarded mutating/publishing/transcript/multi-pass skill without explicit-only guards is rejected
        guarded_dir = self.root / "skills/repo-audit-diagnostics"
        shutil.copytree(self.skill, guarded_dir)
        (guarded_dir / "SKILL.md").write_text(
            orig_entry.replace("name: sample-audit", "name: repo-audit-diagnostics"),
            encoding="utf-8",
        )
        self._write_ui_metadata(
            guarded_dir,
            skill_name="repo-audit-diagnostics",
            short_description="Diagnose stalled audit sessions safely",
            policy={"allow_implicit_invocation": True},
        )
        guarded_errors = validate_skill(guarded_dir)
        self.assertTrue(
            any("disable-model-invocation: true" in e for e in guarded_errors),
            guarded_errors,
        )
        self.assertTrue(
            any("allow_implicit_invocation: false" in e for e in guarded_errors),
            guarded_errors,
        )

    def test_stdlib_frontmatter_parser_matches_yaml_safe_load_across_all_skills(
        self,
    ) -> None:
        from unittest.mock import patch

        import yaml
        from skill_tools import _parse_frontmatter_stdlib, read_frontmatter

        expected_skills = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        skill_files = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(expected_skills, len(skill_files))
        for skill_md in skill_files:
            raw_text = skill_md.read_text(encoding="utf-8")
            _, rest = raw_text.split("---\n", 1)
            frontmatter_text, expected_body = rest.split("\n---\n", 1)
            expected_meta = yaml.safe_load(frontmatter_text)
            stdlib_meta = _parse_frontmatter_stdlib(frontmatter_text, skill_md)
            self.assertEqual(expected_meta, stdlib_meta, str(skill_md))

            with patch.dict(sys.modules, {"yaml": None}):
                fallback_meta, fallback_body = read_frontmatter(skill_md)
            self.assertEqual(expected_meta, fallback_meta, str(skill_md))
            self.assertEqual(expected_body, fallback_body, str(skill_md))

    def test_all_non_test_subprocess_sites_set_explicit_timeouts_and_handle_cli_exits(
        self,
    ) -> None:
        import ast
        import contextlib
        import importlib.util
        import io

        import validate_skills

        # 1. Every direct subprocess.run(...) call across scripts/ and examples/ must specify timeout=
        missing_timeout: list[str] = []
        for search_dir in (ROOT / "scripts", ROOT / "examples"):
            for py_path in sorted(search_dir.rglob("*.py")):
                tree = ast.parse(py_path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    is_subproc_run = (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.func.attr == "run"
                    )
                    if is_subproc_run and not any(
                        kw.arg == "timeout" for kw in node.keywords
                    ):
                        rel = py_path.relative_to(ROOT).as_posix()
                        missing_timeout.append(f"{rel}:L{node.lineno}")
        self.assertEqual(
            [],
            missing_timeout,
            f"Unbounded subprocess.run call(s) missing timeout=: {missing_timeout}",
        )

        # 2. validate_skills.py routes Git timeouts to FredCliError and returns exit code 2 cleanly
        err_val_git = io.StringIO()
        with (
            patch(
                "skill_tools.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["git", "rev-parse"], timeout=30
                ),
            ),
            contextlib.redirect_stderr(err_val_git),
        ):
            rc_val_git = validate_skills.main(["--root", str(ROOT), "--no-ledger"])
        self.assertEqual(2, rc_val_git)
        self.assertIn("Error [git_timeout]:", err_val_git.getvalue())
        self.assertIn("Hint:", err_val_git.getvalue())

        err_val_raw = io.StringIO()
        with (
            patch(
                "validate_skills.validate_with_telemetry",
                side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
            ),
            contextlib.redirect_stderr(err_val_raw),
        ):
            rc_val_raw = validate_skills.main(["--root", str(self.root), "--no-ledger"])
        self.assertEqual(2, rc_val_raw)
        self.assertIn("Error [subprocess_timeout]:", err_val_raw.getvalue())

        # 3. run_checks.py enforces 300s fast phases, 900s tests phase, and returns non-zero cleanly on timeout
        observed_timeouts: list[tuple[str, object]] = []

        def record_timeout_run(
            cmd: list[str], *_args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            observed_timeouts.append((cmd[-1], kwargs.get("timeout")))
            if "-v" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)
            return subprocess.CompletedProcess(cmd, returncode=0)

        out_chk, err_chk = io.StringIO(), io.StringIO()
        with (
            patch("run_checks.subprocess.run", side_effect=record_timeout_run),
            contextlib.redirect_stdout(out_chk),
            contextlib.redirect_stderr(err_chk),
        ):
            rc_chk = run_checks(["lint", "tests"], root=ROOT)
        self.assertNotEqual(0, rc_chk)
        self.assertEqual([(".", 300), ("-v", 900)], observed_timeouts)
        self.assertIn("timed out after 900s", err_chk.getvalue())
        self.assertNotIn("Traceback", err_chk.getvalue())

        # 4. examples/walkthrough/run_demo.py enforces timeout=30 and returns exit code 1 cleanly on timeout
        demo_path = ROOT / "examples/walkthrough/run_demo.py"
        spec = importlib.util.spec_from_file_location("run_demo_mod", demo_path)
        assert spec is not None and spec.loader is not None
        run_demo_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run_demo_mod)

        demo_out = self.root / "demo-timeout-out"
        err_demo = io.StringIO()
        with (
            patch.object(
                run_demo_mod.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["node", "check.mjs"], timeout=30
                ),
            ),
            contextlib.redirect_stderr(err_demo),
        ):
            rc_demo = run_demo_mod.main(["--output", str(demo_out)])
        self.assertEqual(1, rc_demo)
        self.assertIn("Error [subprocess_timeout]:", err_demo.getvalue())
        self.assertNotIn("Traceback", err_demo.getvalue())


if __name__ == "__main__":
    unittest.main()

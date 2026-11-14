"""Negative package fixtures and generated-artifact drift checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_catalog import build
from sync_audit_resources import sync
from validate_skills import validate_skill


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills/sample-audit"
        self.skill.mkdir(parents=True)
        self.entry = self.skill / "SKILL.md"
        self.entry.write_text('---\nname: sample-audit\ndescription: "Audit synthetic contracts when explicitly requested."\n---\n\n# Sample\n\nInspect the supplied contract.\n')

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self):
        (self.root / "catalog").mkdir(exist_ok=True)
        document = {"schema_version": "1.0", "suites": [{"name": "repository-audits", "phases": [{"id": 1, "title": "Discovery", "skills": ["sample-audit"]}]}]}
        (self.root / "catalog/suites.json").write_text(json.dumps(document))

    def test_minimal_portable_package_is_valid(self):
        self.assertEqual([], validate_skill(self.skill))

    def test_name_mismatch_is_rejected(self):
        self.entry.write_text(self.entry.read_text().replace("name: sample-audit", "name: different-name"))
        self.assertTrue(any("mismatch" in error for error in validate_skill(self.skill)))

    def test_missing_reference_and_package_escape_are_rejected(self):
        self.entry.write_text(self.entry.read_text() + '\n[Missing](references/absent.md)\n[Outside](../../outside.md)\n')
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
        self.entry.write_text(self.entry.read_text() + '\n[External](https://example.invalid/docs)\n[Local](#sample)\n')
        self.assertEqual([], validate_skill(self.skill))

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
        self.assertEqual(first_marketplace, (self.root / ".claude-plugin/marketplace.json").read_bytes())
        self.entry.write_text(self.entry.read_text().replace("synthetic contracts", "revised contracts"))
        problems = build(self.root, check=True)
        self.assertTrue(problems)
        self.assertTrue(any(".claude-plugin/marketplace.json" in problem for problem in problems))

    def test_marketplace_drift_is_detected(self):
        self.manifest()
        self.assertEqual([], build(self.root))
        marketplace_path = self.root / ".claude-plugin/marketplace.json"
        self.assertTrue(marketplace_path.is_file())
        marketplace_path.write_text('{"tampered": true}', encoding="utf-8")
        problems = build(self.root, check=True)
        self.assertTrue(any(".claude-plugin/marketplace.json" in problem for problem in problems))

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
        self.assertEqual(len(plugins), 35)
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
        (shared / "manifest.json").write_text(json.dumps({"files": ["references/protocol.md"]}))
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
        for hook_id in ["sync-audit-resources", "build-catalog", "validate-skills", "unit-tests"]:
            self.assertIn(hook_id, hooks)
            self.assertEqual(hooks[hook_id].get("language"), "system")
            self.assertFalse(hooks[hook_id].get("pass_filenames", True))
            self.assertTrue(hooks[hook_id].get("always_run", False))

        reqs_path = ROOT / "requirements-dev.txt"
        self.assertTrue(reqs_path.is_file())
        reqs = reqs_path.read_text(encoding="utf-8")
        self.assertIn("pre-commit", reqs)


if __name__ == "__main__":
    unittest.main()

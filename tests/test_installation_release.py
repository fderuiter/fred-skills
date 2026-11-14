import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import installation, releases
from fredlib.common import atomic_json, read_json


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.destination = self.root / "toolkit", self.root / "project"
        (self.source / "catalog").mkdir(parents=True)
        (self.source / "VERSION").write_text("0.2.0\n")
        atomic_json(
            self.source / "catalog/hosts.json", read_json(ROOT / "catalog/hosts.json")
        )
        atomic_json(
            self.source / "catalog/skills.json",
            {"skills": [{"name": "sample"}, {"name": "other"}]},
        )
        atomic_json(
            self.source / "catalog/packs.json",
            {"packs": {"starter": {"skills": ["sample", "other"]}}},
        )
        for name in ["sample", "other"]:
            path = self.source / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text("Synthetic package " + name)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, **options):
        return installation.install(
            self.destination, "codex", pack="starter", root=self.source, **options
        )

    def test_dry_run_writes_nothing(self):
        self.assertTrue(self.install(dry_run=True)["dry_run"])
        self.assertFalse(self.destination.exists())

    def test_all_four_hosts_copy_complete_packages(self):
        for host in ["codex", "claude-code", "antigravity", "gemini-cli"]:
            destination = self.root / host
            installation.install(destination, host, pack="starter", root=self.source)
            target, _ = installation.locations(
                destination, host, "project", self.source
            )
            self.assertEqual(
                (self.source / "skills/sample/SKILL.md").read_bytes(),
                (target / "sample/SKILL.md").read_bytes(),
            )

    def test_update_and_rollback_restore_exact_previous_bytes(self):
        self.install()
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        before = (target / "sample/SKILL.md").read_bytes()
        (self.source / "skills/sample/SKILL.md").write_text("Updated synthetic package")
        self.install(update=True)
        self.assertNotEqual(before, (target / "sample/SKILL.md").read_bytes())
        installation.rollback(self.destination, "codex", "project", self.source)
        self.assertEqual(before, (target / "sample/SKILL.md").read_bytes())

    def test_unmanaged_and_modified_packages_are_preserved(self):
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (target / "sample").mkdir(parents=True)
        (target / "sample/SKILL.md").write_text("User content")
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual("User content", (target / "sample/SKILL.md").read_text())
        shutil.rmtree(target / "sample")
        self.install()
        (target / "sample/SKILL.md").write_text("Locally edited")
        with self.assertRaises(ValueError):
            self.install(update=True)
        with self.assertRaises(ValueError):
            installation.remove(
                self.destination, "codex", "project", ["sample"], root=self.source
            )

    def test_remove_and_rollback_preserve_other_skills(self):
        self.install()
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        installation.remove(
            self.destination, "codex", "project", ["sample"], root=self.source
        )
        self.assertFalse((target / "sample").exists())
        self.assertTrue((target / "other").exists())
        installation.rollback(self.destination, "codex", "project", self.source)
        self.assertTrue((target / "sample").exists())

    def test_first_install_rollback_removes_only_managed_packages(self):
        self.install()
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (target / "unrelated").mkdir()
        installation.rollback(self.destination, "codex", "project", self.source)
        self.assertFalse((target / "sample").exists())
        self.assertTrue((target / "unrelated").exists())

    def test_lock_prevents_overlapping_writes(self):
        _, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        state.mkdir(parents=True)
        (state / "operation.lock").write_text("held")
        with self.assertRaises(ValueError):
            self.install()

    def test_failed_install_rolls_back_all_already_moved_packages(self):
        import os

        self.install()
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        before = (target / "sample/SKILL.md").read_bytes()
        (self.source / "skills/sample/SKILL.md").write_text("New version")
        original = os.replace

        def fail_second_stage(source, dest):
            if "staging" in Path(source).parts and Path(source).name == "other":
                raise OSError("Synthetic write failure")
            return original(source, dest)

        with (
            patch.object(installation.os, "replace", side_effect=fail_second_stage),
            self.assertRaises(OSError),
        ):
            self.install(update=True)
        self.assertEqual(before, (target / "sample/SKILL.md").read_bytes())
        self.assertTrue((target / "other/SKILL.md").exists())

    def test_source_symlink_and_escaped_host_paths_are_rejected(self):
        data = read_json(self.source / "catalog/hosts.json")
        data["hosts"]["codex"]["project_path"] = "../escape"
        atomic_json(self.source / "catalog/hosts.json", data)
        with self.assertRaises(ValueError):
            self.install()

    def test_release_is_reproducible_and_tampering_is_detected(self):
        one, two = self.root / "one", self.root / "two"
        first = releases.build(one, pack="starter", root=self.source)
        second = releases.build(two, pack="starter", root=self.source)
        self.assertEqual(first, second)
        self.assertEqual(2, releases.verify(one / "manifest.json")["verified_archives"])
        archive = one / first["archives"][0]["file"]
        archive.write_bytes(archive.read_bytes() + b"corrupt")
        with self.assertRaises(ValueError):
            releases.verify(one / "manifest.json")

    def test_recovery_restores_a_partially_swapped_install(self):
        import os

        from fredlib.common import digest, package_files

        self.install()
        target, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        before = installation.receipt(state)
        backups = state / "backups/crash-fixture"
        backups.mkdir()
        expected_before = (target / "sample/SKILL.md").read_bytes()
        os.replace(target / "sample", backups / "sample")
        (target / "sample").mkdir()
        (target / "sample/SKILL.md").write_text("Interrupted new package")
        atomic_json(
            state / "operation.json",
            {
                "id": "crash-fixture",
                "phase": "prepared",
                "skills": ["sample"],
                "before": before["skills"],
                "backups": str(backups),
                "after_hashes": {"sample": digest(package_files(target / "sample"))},
            },
        )
        (state / "operation.lock").write_text("interrupted")
        with self.assertRaises(ValueError):
            installation.recover(
                self.destination, "codex", "project", False, self.source
            )
        result = installation.recover(
            self.destination, "codex", "project", True, self.source
        )
        self.assertEqual("restored-before-state", result["recovery"])
        self.assertEqual(expected_before, (target / "sample/SKILL.md").read_bytes())
        self.assertFalse((state / "operation.lock").exists())

    def test_auto_detect_claude_marker(self):
        dest = self.root / "claude_project"
        (dest / ".claude").mkdir(parents=True)
        res = installation.install(dest, pack="starter", root=self.source)
        self.assertEqual("claude-code", res["host"])
        self.assertTrue((dest / ".claude/skills/sample/SKILL.md").is_file())

    def test_auto_detect_gemini_marker(self):
        dest = self.root / "gemini_project"
        (dest / ".gemini").mkdir(parents=True)
        res = installation.install(dest, pack="starter", root=self.source)
        self.assertEqual("gemini-cli", res["host"])
        self.assertTrue((dest / ".gemini/skills/sample/SKILL.md").is_file())

    def test_auto_detect_agents_marker_defaults_to_codex(self):
        dest = self.root / "codex_project"
        (dest / ".agents").mkdir(parents=True)
        res = installation.install(dest, pack="starter", root=self.source)
        self.assertEqual("codex", res["host"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())

    def test_auto_detect_agents_resolves_antigravity_with_existing_receipt(self):
        dest = self.root / "antigravity_project"
        (dest / ".agents").mkdir(parents=True)
        # First install explicitly with antigravity host
        res1 = installation.install(
            dest, host="antigravity", pack="starter", root=self.source
        )
        self.assertEqual("antigravity", res1["host"])
        # Now update without specifying host
        (self.source / "skills/sample/SKILL.md").write_text("Updated antigravity skill")
        res2 = installation.install(
            dest, host=None, pack="starter", update=True, root=self.source
        )
        self.assertEqual("antigravity", res2["host"])
        self.assertEqual(
            "Updated antigravity skill",
            (dest / ".agents/skills/sample/SKILL.md").read_text(),
        )

    def test_auto_detect_fails_with_zero_markers(self):
        dest = self.root / "empty_project"
        dest.mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            installation.install(dest, pack="starter", root=self.source)
        self.assertIn("--host", str(ctx.exception))

    def test_auto_detect_fails_with_ambiguous_markers(self):
        dest = self.root / "ambiguous_project"
        (dest / ".claude").mkdir(parents=True)
        (dest / ".agents").mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            installation.install(dest, pack="starter", root=self.source)
        self.assertIn("--host", str(ctx.exception))

    def test_auto_detect_fails_with_conflicting_receipts(self):
        dest = self.root / "conflicting_project"
        (dest / ".agents").mkdir(parents=True)
        state_codex = dest / ".fred-skills/codex-project"
        state_antigravity = dest / ".fred-skills/antigravity-project"
        state_codex.mkdir(parents=True)
        state_antigravity.mkdir(parents=True)
        atomic_json(
            state_codex / "receipt.json",
            {"schema_version": "1.0", "host": "codex", "skills": {}, "history": []},
        )
        atomic_json(
            state_antigravity / "receipt.json",
            {
                "schema_version": "1.0",
                "host": "antigravity",
                "skills": {},
                "history": [],
            },
        )
        with self.assertRaises(ValueError) as ctx:
            installation.detect_host(dest)
        self.assertIn("--host", str(ctx.exception))

    def test_explicit_host_overrides_markers(self):
        dest = self.root / "override_project"
        (dest / ".claude").mkdir(parents=True)
        res = installation.install(dest, host="codex", pack="starter", root=self.source)
        self.assertEqual("codex", res["host"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())

        dest2 = self.root / "no_marker_project"
        dest2.mkdir(parents=True)
        res2 = installation.install(
            dest2, host="claude-code", pack="starter", root=self.source
        )
        self.assertEqual("claude-code", res2["host"])
        self.assertTrue((dest2 / ".claude/skills/sample/SKILL.md").is_file())

        dest3 = self.root / "ambiguous_override_project"
        (dest3 / ".claude").mkdir(parents=True)
        (dest3 / ".gemini").mkdir(parents=True)
        res3 = installation.install(
            dest3, host="codex", pack="starter", root=self.source
        )
        self.assertEqual("codex", res3["host"])

    def test_cli_parser_makes_host_optional(self):
        import fred

        p = fred.parser()
        for cmd in ["install", "update", "remove", "rollback", "recover-install"]:
            args = p.parse_args([cmd, "--destination", str(self.destination)])
            self.assertIsNone(args.host)
            self.assertEqual(self.destination, args.destination)

    def test_remove_and_rollback_with_auto_detect_host(self):
        dest = self.root / "auto_remove_rollback"
        (dest / ".claude").mkdir(parents=True)
        installation.install(dest, pack="starter", root=self.source)
        self.assertTrue((dest / ".claude/skills/sample/SKILL.md").is_file())
        # remove without host
        installation.remove(dest, skills=["sample"], root=self.source)
        self.assertFalse((dest / ".claude/skills/sample").exists())
        # rollback without host
        installation.rollback(dest, root=self.source)
        self.assertTrue((dest / ".claude/skills/sample/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()

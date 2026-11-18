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
        hosts = list(read_json(self.source / "catalog/hosts.json")["hosts"].keys())
        self.assertEqual(
            hosts,
            [
                "codex",
                "claude-code",
                "antigravity",
                "gemini-cli",
                "cursor",
                "copilot",
                "opencode",
                "windsurf",
            ],
        )
        initial_bytes = b"Synthetic package sample"
        updated_bytes = b"Updated synthetic package sample"
        for host in hosts:
            for scope in ["project", "user"]:
                (self.source / "skills/sample/SKILL.md").write_bytes(initial_bytes)
                destination = self.root / f"{host}-{scope}"
                installation.install(
                    destination,
                    host,
                    scope=scope,
                    pack="starter",
                    root=self.source,
                )
                target, state = installation.locations(
                    destination, host, scope, self.source
                )
                self.assertEqual(
                    initial_bytes, (target / "sample/SKILL.md").read_bytes()
                )
                rec = installation.receipt(state)
                self.assertEqual(host, rec["host"])
                self.assertIn("sample", rec["skills"])
                self.assertIn("other", rec["skills"])

                (self.source / "skills/sample/SKILL.md").write_bytes(updated_bytes)
                installation.install(
                    destination,
                    host,
                    scope=scope,
                    pack="starter",
                    update=True,
                    root=self.source,
                )
                self.assertEqual(
                    updated_bytes, (target / "sample/SKILL.md").read_bytes()
                )

                installation.rollback(destination, host, scope, self.source)
                self.assertEqual(
                    initial_bytes, (target / "sample/SKILL.md").read_bytes()
                )
        (self.source / "skills/sample/SKILL.md").write_bytes(initial_bytes)

    def test_query_installed_returns_installed_skills(self):
        self.install()
        target, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        res = installation.query_installed(
            self.destination, "codex", "project", root=self.source
        )
        self.assertEqual(str(self.destination.resolve()), res["destination"])
        self.assertEqual("codex", res["host"])
        self.assertEqual("project", res["scope"])
        self.assertEqual(str(target), res["target"])
        self.assertEqual(str(state), res["state"])
        self.assertIn("sample", res["skills"])
        self.assertIn("other", res["skills"])
        self.assertEqual("0.2.0", res["skills"]["sample"]["source_version"])
        self.assertIn("SKILL.md", res["skills"]["sample"]["files"])
        self.assertTrue(len(res["skills"]["sample"]["content_hash"]) > 0)

    def test_query_installed_no_receipt_returns_empty_skills_with_message(self):
        res = installation.query_installed(
            self.destination, "codex", "project", root=self.source
        )
        self.assertEqual({}, res["skills"])
        self.assertIn("message", res)
        self.assertTrue(len(res["message"]) > 0)

        # Empty destination without specifying host
        empty_dest = self.root / "empty-project"
        empty_dest.mkdir(parents=True)
        res_empty = installation.query_installed(empty_dest, root=self.source)
        self.assertEqual({}, res_empty["skills"])
        self.assertIn("message", res_empty)

    def test_query_installed_auto_detects_host(self):
        claude_dest = self.root / "claude-project"
        installation.install(
            claude_dest, "claude-code", pack="starter", root=self.source
        )
        res = installation.query_installed(claude_dest, root=self.source)
        self.assertEqual("claude-code", res["host"])
        self.assertIn("sample", res["skills"])

    def test_query_installed_default_destination_none_project_scope(self):
        self.install()
        target, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        with patch("pathlib.Path.cwd", return_value=self.destination):
            res = installation.query_installed(
                destination=None, host="codex", scope="project", root=self.source
            )
            self.assertEqual(str(self.destination.resolve()), res["destination"])
            self.assertEqual("codex", res["host"])
            self.assertEqual("project", res["scope"])
            self.assertEqual(str(target), res["target"])
            self.assertEqual(str(state), res["state"])
            self.assertIn("sample", res["skills"])

            # Auto-detects host when destination=None
            res_auto = installation.query_installed(root=self.source)
            self.assertEqual("codex", res_auto["host"])
            self.assertIn("sample", res_auto["skills"])

    def test_query_installed_default_destination_none_outside_project_returns_empty_skills(
        self,
    ):
        empty_dir = self.root / "empty-outside-project"
        empty_dir.mkdir(parents=True)
        with patch("pathlib.Path.cwd", return_value=empty_dir):
            res = installation.query_installed(destination=None, root=self.source)
            self.assertEqual(str(empty_dir.resolve()), res["destination"])
            self.assertIsNone(res["host"])
            self.assertEqual({}, res["skills"])
            self.assertIn("message", res)
            self.assertIn("No installation receipt found", res["message"])

    def test_query_installed_default_destination_none_user_scope(self):
        user_home = self.root / "mock-user-home"
        user_home.mkdir(parents=True)
        installation.install(
            user_home, "claude-code", scope="user", pack="starter", root=self.source
        )
        with patch("pathlib.Path.home", return_value=user_home):
            res = installation.query_installed(
                destination=None, scope="user", root=self.source
            )
            self.assertEqual(str(user_home.resolve()), res["destination"])
            self.assertEqual("claude-code", res["host"])
            self.assertEqual("user", res["scope"])
            self.assertIn("sample", res["skills"])

    def test_query_installed_ambiguous_hosts_raises_error(self):
        dest = self.root / "conflicting_installed_project"
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
            installation.query_installed(dest, root=self.source)
        self.assertIn("Ambiguous host receipts", str(ctx.exception))

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

    def test_unified_release_builds_all_documented_packs_and_standalone_skills(self):
        one = self.root / "unified_one"
        two = self.root / "unified_two"
        first = releases.build(one, all_packs=True, root=ROOT)
        second = releases.build(two, all_packs=True, root=ROOT)
        self.assertEqual(first, second)

        manifest_path = one / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        self.assertTrue((one / "SHA256SUMS").is_file())
        self.assertTrue((one / "checksums.sha256").is_file())

        ver_result = releases.verify(manifest_path)
        self.assertEqual(len(first["archives"]), ver_result["verified_archives"])

        # Check documented packs (tar and zip)
        for pack in ["starter", "full-audit", "complete"]:
            self.assertTrue(
                (one / f"fred-skills-{first['version']}-{pack}.tar").is_file()
            )
            self.assertTrue(
                (one / f"fred-skills-{first['version']}-{pack}.zip").is_file()
            )

        # Check consolidated suite and standalone skills
        self.assertTrue((one / "fred-skills-suite.tar.gz").is_file())
        self.assertTrue((one / "repo-audit-concurrency.tar.gz").is_file())

        # Verify tampering is detected
        corrupt_target = one / first["archives"][0]["file"]
        corrupt_target.write_bytes(corrupt_target.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            releases.verify(manifest_path)

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

    def test_auto_detect_new_project_markers_and_copilot_fallback(self):
        cases = [
            ("cursor_proj", [".cursor"], "cursor", ".cursor/skills/sample/SKILL.md"),
            (
                "opencode_proj",
                [".opencode"],
                "opencode",
                ".opencode/skills/sample/SKILL.md",
            ),
            (
                "windsurf_proj",
                [".windsurf"],
                "windsurf",
                ".windsurf/skills/sample/SKILL.md",
            ),
            (
                "devin_proj",
                [".devin"],
                "windsurf",
                ".windsurf/skills/sample/SKILL.md",
            ),
            (
                "windsurf_both_proj",
                [".windsurf", ".devin"],
                "windsurf",
                ".windsurf/skills/sample/SKILL.md",
            ),
            (
                "copilot_bare_github",
                [".github/workflows"],
                "copilot",
                ".github/skills/sample/SKILL.md",
            ),
            (
                "copilot_skills_dir",
                [".github/skills"],
                "copilot",
                ".github/skills/sample/SKILL.md",
            ),
        ]
        for folder, markers, expected_host, expected_skill in cases:
            with self.subTest(folder=folder):
                dest = self.root / folder
                for marker in markers:
                    (dest / marker).mkdir(parents=True, exist_ok=True)
                self.assertEqual(expected_host, installation.detect_host(dest))
                res = installation.install(dest, pack="starter", root=self.source)
                self.assertEqual(expected_host, res["host"])
                self.assertTrue((dest / expected_skill).is_file())

    def test_primary_markers_outrank_bare_github_unless_copilot_skills_or_receipt(self):
        claude_gh = self.root / "claude_with_github_workflows"
        (claude_gh / ".claude").mkdir(parents=True)
        (claude_gh / ".github/workflows").mkdir(parents=True)
        self.assertEqual("claude-code", installation.detect_host(claude_gh))
        res = installation.install(claude_gh, pack="starter", root=self.source)
        self.assertEqual("claude-code", res["host"])

        cursor_gh = self.root / "cursor_with_github_workflows"
        (cursor_gh / ".cursor").mkdir(parents=True)
        (cursor_gh / ".github/workflows").mkdir(parents=True)
        self.assertEqual("cursor", installation.detect_host(cursor_gh))

        claude_copilot_skills = self.root / "claude_and_github_skills"
        (claude_copilot_skills / ".claude").mkdir(parents=True)
        (claude_copilot_skills / ".github/skills").mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx1:
            installation.detect_host(claude_copilot_skills)
        self.assertIn("--host", str(ctx1.exception))

        claude_cursor = self.root / "claude_and_cursor"
        (claude_cursor / ".claude").mkdir(parents=True)
        (claude_cursor / ".cursor").mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx2:
            installation.detect_host(claude_cursor)
        self.assertIn("--host", str(ctx2.exception))

        claude_copilot_receipt = self.root / "claude_github_and_copilot_receipt"
        (claude_copilot_receipt / ".claude").mkdir(parents=True)
        (claude_copilot_receipt / ".github/workflows").mkdir(parents=True)
        copilot_state = claude_copilot_receipt / ".fred-skills/copilot-project"
        copilot_state.mkdir(parents=True)
        atomic_json(
            copilot_state / "receipt.json",
            {"schema_version": "1.0", "host": "copilot", "skills": {}, "history": []},
        )
        with self.assertRaises(ValueError) as ctx3:
            installation.detect_host(claude_copilot_receipt)
        self.assertIn("--host", str(ctx3.exception))

        bare_gh_conflicting_receipt = self.root / "bare_github_claude_receipt"
        (bare_gh_conflicting_receipt / ".github/workflows").mkdir(parents=True)
        claude_state = bare_gh_conflicting_receipt / ".fred-skills/claude-code-project"
        claude_state.mkdir(parents=True)
        atomic_json(
            claude_state / "receipt.json",
            {
                "schema_version": "1.0",
                "host": "claude-code",
                "skills": {},
                "history": [],
            },
        )
        with self.assertRaises(ValueError) as ctx4:
            installation.detect_host(bare_gh_conflicting_receipt)
        self.assertIn("--host", str(ctx4.exception))

    def test_detect_host_user_scope_markers(self):
        user_cases = [
            (".claude", "claude-code"),
            (".cursor", "cursor"),
            (".agents", "codex"),
            (".gemini", "gemini-cli"),
            (".copilot", "copilot"),
            (".config/opencode", "opencode"),
            (".config/devin", "windsurf"),
        ]
        for idx, (marker, expected_host) in enumerate(user_cases):
            with self.subTest(marker=marker):
                home_dir = self.root / f"user_home_{idx}"
                (home_dir / marker).mkdir(parents=True)
                self.assertEqual(
                    expected_host, installation.detect_host(home_dir, scope="user")
                )

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

    def test_cli_parser_makes_destination_optional(self):
        import fred

        p = fred.parser()
        for cmd in ["install", "update", "remove", "rollback", "recover-install"]:
            args = p.parse_args([cmd])
            self.assertIsNone(args.destination)
            self.assertIsNone(args.host)

    def test_locations_default_destination_resolution(self):
        target_proj, state_proj = installation.locations(
            host="codex", scope="project", root=self.source
        )
        self.assertEqual(Path.cwd().resolve() / ".agents/skills", target_proj)
        self.assertEqual(
            Path.cwd().resolve() / ".fred-skills/codex-project", state_proj
        )

        target_user, state_user = installation.locations(
            host="codex", scope="user", root=self.source
        )
        self.assertEqual(Path.home().resolve() / ".agents/skills", target_user)
        self.assertEqual(Path.home().resolve() / ".fred-skills/codex-user", state_user)

    def test_detect_host_default_destination_and_error_message(self):
        import os

        empty_dir = self.root / "empty_cwd"
        empty_dir.mkdir(parents=True)
        orig_cwd = os.getcwd()
        try:
            os.chdir(empty_dir)
            with self.assertRaises(ValueError) as ctx:
                installation.detect_host(scope="project")
            self.assertIn(str(empty_dir.resolve()), str(ctx.exception))
            self.assertIn("--host", str(ctx.exception))

            (empty_dir / ".claude").mkdir()
            self.assertEqual("claude-code", installation.detect_host(scope="project"))
        finally:
            os.chdir(orig_cwd)

        empty_home = self.root / "empty_home"
        empty_home.mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=empty_home):
            with self.assertRaises(ValueError) as ctx:
                installation.detect_host(scope="user")
            self.assertIn(str(empty_home.resolve()), str(ctx.exception))
            self.assertIn("--host", str(ctx.exception))

            (empty_home / ".agents").mkdir()
            self.assertEqual("codex", installation.detect_host(scope="user"))

    def test_install_without_destination_uses_cwd(self):
        import os

        proj_dir = self.root / "project_without_dest"
        (proj_dir / ".claude").mkdir(parents=True)
        orig_cwd = os.getcwd()
        try:
            os.chdir(proj_dir)
            res = installation.install(pack="starter", root=self.source)
            self.assertEqual("claude-code", res["host"])
            self.assertTrue((proj_dir / ".claude/skills/sample/SKILL.md").is_file())
        finally:
            os.chdir(orig_cwd)

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

    def test_lifecycle_functions_accept_typed_dataclasses(self):
        dest = self.root / "typed_dataclass_project"
        target_env = installation.TargetEnvironment(
            destination=dest, host="codex", scope="project", root=self.source
        )
        install_opts = installation.InstallOptions(skills=["sample"])
        res_install = installation.install(target_env, install_opts)
        self.assertEqual("install", res_install["action"])
        self.assertEqual(["sample"], res_install["skills"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())

        res_query = installation.query_installed(target_env)
        self.assertIn("sample", res_query["skills"])

        remove_opts = installation.RemoveOptions(skills=["sample"])
        res_remove = installation.remove(target_env, remove_opts)
        self.assertEqual("remove", res_remove["action"])
        self.assertFalse((dest / ".agents/skills/sample").exists())

        rollback_opts = installation.RollbackOptions(target=target_env)
        res_rollback = installation.rollback(target_env, rollback_opts)
        self.assertEqual("rollback", res_rollback["action"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())

    def test_single_dataclass_invocation_with_embedded_target(self):
        dest = self.root / "single_dataclass_project"
        target_env = installation.TargetEnvironment(
            destination=dest, host="codex", scope="project", root=self.source
        )
        install_opts = installation.InstallOptions(
            pack="starter", skills=["sample"], target=target_env
        )
        res_install = installation.install(install_opts)
        self.assertEqual("install", res_install["action"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())

        remove_opts = installation.RemoveOptions(skills=["sample"], target=target_env)
        res_remove = installation.remove(remove_opts)
        self.assertEqual("remove", res_remove["action"])
        self.assertFalse((dest / ".agents/skills/sample").exists())

    def test_recover_accepts_typed_dataclass(self):
        import os

        from fredlib.common import digest, package_files

        dest = self.root / "recover_typed_project"
        target_env = installation.TargetEnvironment(
            destination=dest, host="codex", scope="project", root=self.source
        )
        installation.install(target_env, installation.InstallOptions(pack="starter"))
        target, state = installation.locations(target_env)
        before = installation.receipt(state)
        backups = state / "backups/crash-fixture-typed"
        backups.mkdir(parents=True)
        expected_before = (target / "sample/SKILL.md").read_bytes()
        os.replace(target / "sample", backups / "sample")
        (target / "sample").mkdir()
        (target / "sample/SKILL.md").write_text("Interrupted typed package")
        atomic_json(
            state / "operation.json",
            {
                "id": "crash-fixture-typed",
                "phase": "prepared",
                "skills": ["sample"],
                "before": before["skills"],
                "backups": str(backups),
                "after_hashes": {"sample": digest(package_files(target / "sample"))},
            },
        )
        (state / "operation.lock").write_text("interrupted")

        rec_opts_unconfirmed = installation.RecoverOptions(
            confirm_interrupted=False, target=target_env
        )
        with self.assertRaises(ValueError):
            installation.recover(target_env, rec_opts_unconfirmed)

        rec_opts_confirmed = installation.RecoverOptions(
            confirm_interrupted=True, target=target_env
        )
        result = installation.recover(target_env, rec_opts_confirmed)
        self.assertEqual("restored-before-state", result["recovery"])
        self.assertEqual(expected_before, (target / "sample/SKILL.md").read_bytes())
        self.assertFalse((state / "operation.lock").exists())

    def test_installation_cyclomatic_complexity_under_ten(self):
        import ast

        def calculate_complexity(node):
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

        install_py = ROOT / "scripts/fredlib/installation.py"
        tree = ast.parse(install_py.read_text(encoding="utf-8"))
        complexities = {}
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexities[item.name] = calculate_complexity(item)
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        complexities[f"{item.name}.{method.name}"] = (
                            calculate_complexity(method)
                        )

        for name, score in complexities.items():
            self.assertLessEqual(
                score,
                10,
                f"Function {name} exceeds cyclomatic complexity limit of 10 (score: {score})",
            )

    def test_installation_functions_have_at_most_four_positional_parameters(self):
        import ast

        install_py = ROOT / "scripts/fredlib/installation.py"
        tree = ast.parse(install_py.read_text(encoding="utf-8"))
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                pos_count = len(item.args.posonlyargs) + len(item.args.args)
                self.assertLessEqual(
                    pos_count,
                    4,
                    f"Function {item.name} has {pos_count} positional parameters (limit is 4)",
                )
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        pos_count = len(method.args.posonlyargs) + len(method.args.args)
                        self.assertLessEqual(
                            pos_count,
                            4,
                            f"Method {item.name}.{method.name} has {pos_count} positional parameters (limit is 4)",
                        )


if __name__ == "__main__":
    unittest.main()

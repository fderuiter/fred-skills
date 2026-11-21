import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import installation, releases
from fredlib.common import (
    FredCliError,
    atomic_json,
    digest,
    package_files,
    read_json,
)

from tests.catalog_oracles import (
    assert_module_ast_bounds,
    expected_pack_count,
    expected_suite_skill_count,
    expected_total_skills,
    expected_unified_release_archive_count,
)


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
                "kiro",
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
        for entry in first["archives"]:
            self.assertEqual(["sample", "other"], entry["skills"])
            self.assertEqual(first["skills"], entry["skills"])
        archive = one / first["archives"][0]["file"]
        archive.write_bytes(archive.read_bytes() + b"corrupt")
        with self.assertRaises(ValueError):
            releases.verify(one / "manifest.json")

    def test_atomic_json_and_manifest_permissions_respect_umask_and_existing_mode(self):
        import os
        import stat

        if os.name == "nt":
            self.skipTest("POSIX file permission bits are not enforced on Windows")

        old_umask = os.umask(0o022)
        try:
            rel_dir = self.root / "perm_release"
            releases.build(rel_dir, pack="starter", root=self.source)
            manifest_mode = stat.S_IMODE((rel_dir / "manifest.json").stat().st_mode)
            sums_mode = stat.S_IMODE((rel_dir / "SHA256SUMS").stat().st_mode)
            self.assertEqual(0o644, manifest_mode)
            self.assertEqual(0o644, sums_mode)

            # Restrictive umask 0077 masks 0o666 so new state files stay private (0600)
            os.umask(0o077)
            private_json = self.root / "private_state" / "receipt.json"
            atomic_json(private_json, {"private": True})
            self.assertEqual(0o600, stat.S_IMODE(private_json.stat().st_mode))

            # Overwriting an existing private file under umask 0022 preserves 0600
            os.umask(0o022)
            atomic_json(private_json, {"private": True, "updated": 2})
            self.assertEqual(0o600, stat.S_IMODE(private_json.stat().st_mode))
        finally:
            os.umask(old_umask)

    def test_verify_enforces_archive_bytes_checksum_files_and_standalone_manifest(self):
        import hashlib as hashlib_mod

        out = self.root / "verify_contracts"
        manifest = releases.build(out, pack="starter", root=self.source)
        manifest_path = out / "manifest.json"
        self.assertEqual(2, releases.verify(manifest_path)["verified_archives"])

        # 1. Tampered entry["bytes"] fails before SHA-256 computation
        tampered_bytes_manifest = read_json(manifest_path)
        tampered_bytes_manifest["archives"][0]["bytes"] += 1
        atomic_json(manifest_path, tampered_bytes_manifest)
        with (
            patch.object(
                releases.hashlib,
                "sha256",
                side_effect=AssertionError("sha256 called before byte size check"),
            ),
            self.assertRaises(ValueError) as bytes_ctx,
        ):
            releases.verify(manifest_path)
        self.assertIn("Byte size mismatch", str(bytes_ctx.exception))
        atomic_json(manifest_path, manifest)

        # 2. Missing archive raises ValueError
        first_archive = out / manifest["archives"][0]["file"]
        saved_archive = first_archive.read_bytes()
        first_archive.unlink()
        with self.assertRaises(ValueError) as missing_ctx:
            releases.verify(manifest_path)
        self.assertIn("Missing release archive", str(missing_ctx.exception))
        first_archive.write_bytes(saved_archive)
        self.assertEqual(2, releases.verify(manifest_path)["verified_archives"])

        # 3. Corrupted digest and malformed lines in SHA256SUMS and checksums.sha256
        orig_sums = (out / "SHA256SUMS").read_text(encoding="utf-8")
        for checksum_name in ("SHA256SUMS", "checksums.sha256"):
            cpath = out / checksum_name
            bad_digest = "0" * 64 + f"  {manifest['archives'][0]['file']}\n"
            cpath.write_text(
                bad_digest
                + f"{manifest['archives'][1]['sha256']}  {manifest['archives'][1]['file']}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                releases.verify(manifest_path)

            cpath.write_text("malformed-checksum-line\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                releases.verify(manifest_path)

            # Truncated checksum file missing second archive
            cpath.write_text(orig_sums.splitlines(keepends=True)[0], encoding="utf-8")
            with self.assertRaises(ValueError):
                releases.verify(manifest_path)

            cpath.write_text(orig_sums, encoding="utf-8")

        # 4. Symlinked checksum files are rejected
        outside_sums = self.root / "outside_sums.txt"
        outside_sums.write_text(orig_sums, encoding="utf-8")
        symlink_supported = True
        try:
            (out / "SHA256SUMS").unlink()
            (out / "SHA256SUMS").symlink_to(outside_sums)
        except OSError:
            symlink_supported = False
            (out / "SHA256SUMS").write_text(orig_sums, encoding="utf-8")
        if symlink_supported:
            with self.assertRaises(ValueError) as sym_ctx:
                releases.verify(manifest_path)
            self.assertIn("Symlink", str(sym_ctx.exception))
            (out / "SHA256SUMS").unlink()

        # 5. Standalone manifest.json verification (when SHA256SUMS / checksums.sha256 are absent)
        if (out / "SHA256SUMS").exists():
            (out / "SHA256SUMS").unlink()
        (out / "checksums.sha256").unlink()
        res_standalone = releases.verify(manifest_path)
        self.assertEqual(2, res_standalone["verified_archives"])

        # Standalone verification still catches same-length byte corruption via sha256
        flipped = bytearray(saved_archive)
        flipped[-1] ^= 0xFF
        first_archive.write_bytes(bytes(flipped))
        self.assertEqual(len(saved_archive), first_archive.stat().st_size)
        self.assertNotEqual(
            hashlib_mod.sha256(bytes(flipped)).hexdigest(),
            manifest["archives"][0]["sha256"],
        )
        with self.assertRaises(ValueError) as standalone_sha_ctx:
            releases.verify(manifest_path)
        self.assertIn("Checksum mismatch", str(standalone_sha_ctx.exception))
        first_archive.write_bytes(saved_archive)

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

        expected_archives = expected_unified_release_archive_count(ROOT)
        expected_packs = expected_pack_count(ROOT)
        expected_full_audit = expected_suite_skill_count("repository-audits", ROOT)
        expected_total = expected_total_skills(ROOT)
        self.assertGreaterEqual(expected_archives, 1)
        self.assertGreaterEqual(expected_packs, 1)
        self.assertGreaterEqual(expected_full_audit, 1)
        self.assertGreaterEqual(expected_total, 1)

        ver_result = releases.verify(manifest_path)
        self.assertEqual(expected_archives, ver_result["verified_archives"])
        self.assertEqual(len(first["archives"]), ver_result["verified_archives"])

        # Check all canonical catalog packs (tar and zip) and archive skills lists
        packs_data = read_json(ROOT / "catalog/packs.json")["packs"]
        catalog_packs = list(packs_data.keys())
        self.assertEqual(expected_packs, len(catalog_packs))
        self.assertNotIn("architecture", catalog_packs)
        self.assertEqual(expected_full_audit, len(packs_data["full-audit"]["skills"]))
        self.assertIn("repo-audit-diagnostics", packs_data["full-audit"]["skills"])
        self.assertEqual(tuple(catalog_packs), releases.DOCUMENTED_PACKS)
        self.assertEqual(catalog_packs, list(first["packs"].keys()))
        archives_by_file = {item["file"]: item for item in first["archives"]}
        cli_names = {
            f"fred-skills-{first['version']}-cli.tar.gz",
            f"fred-skills-{first['version']}-cli.zip",
        }
        for item in first["archives"]:
            self.assertIn("skills", item)
            if item["file"] in cli_names:
                self.assertEqual([], item["skills"])
            else:
                self.assertTrue(
                    len(item["skills"]) > 0, f"Empty skills for {item['file']}"
                )
        for pack in catalog_packs:
            tar_name = f"fred-skills-{first['version']}-{pack}.tar"
            zip_name = f"fred-skills-{first['version']}-{pack}.zip"
            self.assertTrue((one / tar_name).is_file(), f"Missing {tar_name}")
            self.assertTrue((one / zip_name).is_file(), f"Missing {zip_name}")
            self.assertIn(tar_name, archives_by_file)
            self.assertIn(zip_name, archives_by_file)
            self.assertEqual(
                packs_data[pack]["skills"],
                archives_by_file[tar_name]["skills"],
            )
            self.assertEqual(
                packs_data[pack]["skills"],
                archives_by_file[zip_name]["skills"],
            )

        # Check consolidated suite and standalone skills
        self.assertTrue((one / "fred-skills-suite.tar.gz").is_file())
        self.assertEqual(
            expected_total, len(archives_by_file["fred-skills-suite.tar.gz"]["skills"])
        )
        self.assertTrue((one / "repo-audit-concurrency.tar.gz").is_file())
        self.assertEqual(
            ["repo-audit-concurrency"],
            archives_by_file["repo-audit-concurrency.tar.gz"]["skills"],
        )

        # Check Gemini CLI platform bundles
        for gemini_name in (
            "darwin.fred-skills.tar.gz",
            "linux.fred-skills.tar.gz",
            "win32.fred-skills.zip",
        ):
            self.assertTrue((one / gemini_name).is_file(), f"Missing {gemini_name}")
            self.assertIn(gemini_name, archives_by_file)
            self.assertEqual(
                expected_total, len(archives_by_file[gemini_name]["skills"])
            )
        self.assertEqual(
            (one / "darwin.fred-skills.tar.gz").read_bytes(),
            (one / "linux.fred-skills.tar.gz").read_bytes(),
        )
        with tarfile.open(one / "darwin.fred-skills.tar.gz", "r:gz") as gtar:
            gnames = set(gtar.getnames())
            self.assertIn("gemini-extension.json", gnames)
            self.assertIn("LICENSE", gnames)
            self.assertEqual(0o644, gtar.getmember("LICENSE").mode)
            self.assertIn("skills/repo-audit-concurrency/SKILL.md", gnames)
            self.assertIn(
                "skills/repo-audit-concurrency/.cursor-plugin/plugin.json", gnames
            )
            self.assertEqual(
                0o755,
                gtar.getmember(
                    "skills/repo-audit-program/scripts/validate_audit.py"
                ).mode,
            )
        with zipfile.ZipFile(one / "win32.fred-skills.zip", "r") as gzip_file:
            gznames = set(gzip_file.namelist())
            self.assertIn("gemini-extension.json", gznames)
            self.assertIn("LICENSE", gznames)
            self.assertIn("skills/repo-audit-concurrency/SKILL.md", gznames)

        # Check standalone CLI bundles (tar.gz and zip) and execute extracted CLI under -S
        cli_tar_gz = one / f"fred-skills-{first['version']}-cli.tar.gz"
        cli_zip = one / f"fred-skills-{first['version']}-cli.zip"
        self.assertTrue(cli_tar_gz.is_file())
        self.assertTrue(cli_zip.is_file())
        with tarfile.open(cli_tar_gz, "r:gz") as ctar:
            cnames = set(ctar.getnames())
            for req in (
                "LICENSE",
                "VERSION",
                "catalog/packs.json",
                "catalog/skills.json",
                "scripts/fred.py",
                "scripts/skill_tools.py",
                "scripts/_markdown_utils.py",
                "scripts/audit_to_sarif.py",
                "scripts/fredlib/__init__.py",
                "scripts/fredlib/releases.py",
            ):
                self.assertIn(req, cnames)
            self.assertEqual(0o755, ctar.getmember("scripts/fred.py").mode)
            self.assertEqual(0o755, ctar.getmember("scripts/audit_to_sarif.py").mode)
            self.assertEqual(0o644, ctar.getmember("scripts/skill_tools.py").mode)
            self.assertEqual(0o644, ctar.getmember("scripts/_markdown_utils.py").mode)
            self.assertEqual(0o644, ctar.getmember("scripts/fredlib/releases.py").mode)
            self.assertEqual(0o644, ctar.getmember("LICENSE").mode)
        with zipfile.ZipFile(cli_zip, "r") as czip:
            self.assertEqual(
                0o100755, czip.getinfo("scripts/fred.py").external_attr >> 16
            )
            self.assertEqual(
                0o100755, czip.getinfo("scripts/audit_to_sarif.py").external_attr >> 16
            )
            self.assertEqual(
                0o100644, czip.getinfo("scripts/skill_tools.py").external_attr >> 16
            )

        standalone_cli_dir = self.root / "standalone_cli_check"
        standalone_cli_dir.mkdir()
        starter_tar_name = f"fred-skills-{first['version']}-starter.tar"
        shutil.copy2(one / "manifest.json", standalone_cli_dir / "manifest.json")
        shutil.copy2(one / "SHA256SUMS", standalone_cli_dir / "SHA256SUMS")
        shutil.copy2(one / starter_tar_name, standalone_cli_dir / starter_tar_name)
        with tarfile.open(cli_tar_gz, "r:gz") as ctar:
            ctar.extractall(standalone_cli_dir, filter="data")
        cli_proc = subprocess.run(
            [
                sys.executable,
                "-S",
                "scripts/fred.py",
                "verify-release",
                "manifest.json",
                "--archive",
                starter_tar_name,
                "--expected-version",
                first["version"],
                "--require-licensing",
            ],
            cwd=standalone_cli_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, cli_proc.returncode, cli_proc.stderr)
        cli_verified = json.loads(cli_proc.stdout)
        self.assertEqual(1, cli_verified["verified_archives"])

        # Verify tampering is detected
        corrupt_target = one / first["archives"][0]["file"]
        corrupt_target.write_bytes(corrupt_target.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            releases.verify(manifest_path)

    def test_unified_release_supports_custom_root_packs(self):
        atomic_json(
            self.source / "catalog/packs.json",
            {
                "packs": {
                    "starter": {"skills": ["sample"]},
                    "custom-domain": {"skills": ["sample", "other"]},
                }
            },
        )
        out = self.root / "custom_unified"
        manifest = releases.build(out, all_packs=True, root=self.source)
        self.assertEqual(["starter", "custom-domain"], list(manifest["packs"].keys()))
        for pack in ["starter", "custom-domain"]:
            self.assertTrue((out / f"fred-skills-0.2.0-{pack}.tar").is_file())
            self.assertTrue((out / f"fred-skills-0.2.0-{pack}.zip").is_file())

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
                "kiro_proj",
                [".kiro"],
                "kiro",
                ".kiro/skills/sample/SKILL.md",
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
            (".kiro", "kiro"),
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
        from fredlib.common import choose_skills

        with self.assertRaises(ValueError):
            choose_skills(self.source)

        dest = self.root / "empty_project"
        dest.mkdir(parents=True)
        with self.assertRaises(ValueError) as detect_ctx:
            installation.detect_host(dest)
        self.assertIn("--host", str(detect_ctx.exception))

        res = installation.install(dest, root=self.source)
        self.assertEqual("codex", res["host"])
        self.assertEqual(["sample", "other"], res["skills"])
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())
        self.assertTrue((dest / ".agents/skills/other/SKILL.md").is_file())
        rec = installation.receipt(dest / ".fred-skills/codex-project")
        self.assertEqual("codex", rec["host"])

        empty_update = self.root / "empty_update_project"
        empty_update.mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            installation.install(empty_update, update=True, root=self.source)
        self.assertIn("--host", str(ctx.exception))

    def test_auto_detect_fails_with_ambiguous_markers(self):
        dest = self.root / "ambiguous_project"
        (dest / ".claude").mkdir(parents=True)
        (dest / ".agents").mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            installation.detect_host(dest)
        self.assertIn("--host", str(ctx.exception))

        res = installation.install(dest, root=self.source)
        self.assertEqual(["codex", "claude-code"], [h["host"] for h in res["hosts"]])
        self.assertTrue((dest / ".claude/skills/sample/SKILL.md").is_file())
        self.assertTrue((dest / ".agents/skills/sample/SKILL.md").is_file())
        self.assertTrue(
            (dest / ".fred-skills/claude-code-project/receipt.json").is_file()
        )
        self.assertTrue((dest / ".fred-skills/codex-project/receipt.json").is_file())
        self.assertFalse((dest / ".fred-skills/antigravity-project").exists())

        (self.source / "skills/sample/SKILL.md").write_text("Updated across hosts")
        upd = installation.install(dest, update=True, root=self.source)
        self.assertEqual("update", upd["action"])
        self.assertEqual(
            "Updated across hosts",
            (dest / ".claude/skills/sample/SKILL.md").read_text(),
        )
        self.assertEqual(
            "Updated across hosts",
            (dest / ".agents/skills/sample/SKILL.md").read_text(),
        )

        queried = installation.query_installed(dest, root=self.source)
        self.assertTrue(queried["healthy"])
        self.assertEqual(
            ["codex", "claude-code"], [h["host"] for h in queried["hosts"]]
        )

        for fn in (
            lambda: installation.remove(dest, skills=["sample"], root=self.source),
            lambda: installation.rollback(dest, root=self.source),
            lambda: installation.recover(
                dest, confirm_interrupted=True, root=self.source
            ),
        ):
            with self.assertRaises(ValueError) as guard_ctx:
                fn()
            self.assertIn("--host", str(guard_ctx.exception))

    def test_multi_host_atomic_rollback_when_second_host_commit_fails(self):
        import os

        dest = self.root / "multi_host_atomic"
        (dest / ".claude").mkdir(parents=True)
        (dest / ".agents").mkdir(parents=True)
        installation.install(dest, root=self.source)
        before_claude = (dest / ".claude/skills/sample/SKILL.md").read_bytes()
        before_codex = (dest / ".agents/skills/sample/SKILL.md").read_bytes()

        (self.source / "skills/sample/SKILL.md").write_text("Broken multi update")
        orig_replace = os.replace

        def fail_on_second_host_swap(src, dst):
            if "staging" in Path(src).parts and ".claude" in Path(dst).parts:
                raise OSError("Simulated second host failure")
            return orig_replace(src, dst)

        with (
            patch.object(
                installation.os, "replace", side_effect=fail_on_second_host_swap
            ),
            self.assertRaises(OSError),
        ):
            installation.install(dest, update=True, root=self.source)

        self.assertEqual(
            before_claude, (dest / ".claude/skills/sample/SKILL.md").read_bytes()
        )
        self.assertEqual(
            before_codex, (dest / ".agents/skills/sample/SKILL.md").read_bytes()
        )
        self.assertTrue(installation.query_installed(dest, root=self.source)["healthy"])

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

    def test_query_installed_reports_tracked_modified_missing_and_untracked(self):
        self.install()
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        res_clean = installation.query_installed(
            self.destination, "codex", "project", root=self.source
        )
        self.assertTrue(res_clean["healthy"])
        self.assertFalse(res_clean["locked"])
        self.assertEqual([], res_clean["untracked"])
        self.assertEqual("tracked", res_clean["skills"]["sample"]["status"])
        self.assertEqual("tracked", res_clean["skills"]["other"]["status"])

        # Edit sample, delete other, add untracked skill and non-skill directory
        (target / "sample/SKILL.md").write_text("Edited on disk")
        shutil.rmtree(target / "other")
        (target / "custom-local").mkdir(parents=True)
        (target / "custom-local/SKILL.md").write_text("Local custom skill")
        (target / "not-a-skill").mkdir(parents=True)
        (target / "not-a-skill/README.md").write_text("Not a skill")

        res_drift = installation.query_installed(
            self.destination, "codex", "project", root=self.source
        )
        self.assertFalse(res_drift["healthy"])
        self.assertFalse(res_drift["locked"])
        self.assertEqual("modified", res_drift["skills"]["sample"]["status"])
        self.assertEqual("missing", res_drift["skills"]["other"]["status"])
        self.assertEqual(["custom-local"], res_drift["untracked"])
        # Existing metadata keys remain intact
        self.assertEqual("0.2.0", res_drift["skills"]["sample"]["source_version"])
        self.assertIn("content_hash", res_drift["skills"]["sample"])

    def test_query_installed_detects_operation_lock_as_unhealthy(self):
        self.install()
        _, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (state / "operation.lock").write_text("active")
        res = installation.query_installed(
            self.destination, "codex", "project", root=self.source
        )
        self.assertTrue(res["locked"])
        self.assertFalse(res["healthy"])
        self.assertEqual("tracked", res["skills"]["sample"]["status"])

    def test_require_unmodified_errors_guide_to_force_flag(self):
        self.install()
        target, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        rec = installation.receipt(state)["skills"]["sample"]
        (target / "sample/SKILL.md").write_text("Drifted content")
        with self.assertRaises(ValueError) as mod_ctx:
            installation.require_unmodified(target / "sample", rec)
        self.assertIn("Locally modified skill: sample", str(mod_ctx.exception))
        self.assertIn("--force", str(mod_ctx.exception))

        shutil.rmtree(target / "sample")
        with self.assertRaises(ValueError) as miss_ctx:
            installation.require_unmodified(target / "sample", rec)
        self.assertIn("Managed skill is missing: sample", str(miss_ctx.exception))
        self.assertIn("--force", str(miss_ctx.exception))

    def test_install_and_update_force_archive_edits_and_restore_deleted_skills(self):
        dest = self.root / "claude-force-project"
        installation.install(dest, host="claude-code", pack="starter", root=self.source)
        target, state = installation.locations(
            dest, "claude-code", "project", self.source
        )
        (target / "sample/SKILL.md").write_text("Operator custom edit")
        shutil.rmtree(target / "other")
        (target / "user-local").mkdir(parents=True)
        (target / "user-local/SKILL.md").write_text("Untracked user skill")

        installation.install(
            dest,
            host="claude-code",
            pack="starter",
            update=True,
            force=True,
            root=self.source,
        )
        archived = list((state / "backups").glob("*/sample/SKILL.md"))
        self.assertTrue(any(p.read_text() == "Operator custom edit" for p in archived))
        self.assertEqual(
            "Synthetic package sample", (target / "sample/SKILL.md").read_text()
        )
        self.assertEqual(
            "Synthetic package other", (target / "other/SKILL.md").read_text()
        )
        self.assertEqual(
            "Untracked user skill", (target / "user-local/SKILL.md").read_text()
        )
        res_after_update = installation.query_installed(
            dest, "claude-code", "project", root=self.source
        )
        self.assertTrue(res_after_update["healthy"])
        self.assertEqual(["user-local"], res_after_update["untracked"])

        # Delete entire .claude/skills target and repair with install --force
        shutil.rmtree(target)
        installation.install(
            dest,
            host="claude-code",
            pack="starter",
            force=True,
            root=self.source,
        )
        res_after_install = installation.query_installed(
            dest, "claude-code", "project", root=self.source
        )
        self.assertTrue(res_after_install["healthy"])
        self.assertEqual("tracked", res_after_install["skills"]["sample"]["status"])
        self.assertEqual("tracked", res_after_install["skills"]["other"]["status"])

    def test_remove_force_archives_modified_and_removes_missing_skills(self):
        self.install()
        target, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (target / "sample/SKILL.md").write_text("Edited before uninstall")
        shutil.rmtree(target / "other")
        (target / "untracked-keep").mkdir(parents=True)
        (target / "untracked-keep/SKILL.md").write_text("Keep me")

        installation.remove(
            self.destination,
            host="codex",
            scope="project",
            skills=["sample", "other"],
            force=True,
            root=self.source,
        )
        archived = list((state / "backups").glob("*/sample/SKILL.md"))
        self.assertTrue(
            any(p.read_text() == "Edited before uninstall" for p in archived)
        )
        rec = installation.receipt(state)
        self.assertEqual({}, rec["skills"])
        self.assertEqual("Keep me", (target / "untracked-keep/SKILL.md").read_text())

    def test_force_never_touches_untracked_or_symlink_targets(self):
        target, _ = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (target / "sample").mkdir(parents=True)
        (target / "sample/SKILL.md").write_text("User unmanaged skill")
        with self.assertRaises(ValueError) as ctx1:
            installation.install(
                self.destination,
                host="codex",
                pack="starter",
                force=True,
                root=self.source,
            )
        self.assertIn("Refusing to replace unmanaged skill", str(ctx1.exception))
        self.assertEqual(
            "User unmanaged skill", (target / "sample/SKILL.md").read_text()
        )

        with self.assertRaises(ValueError) as ctx2:
            installation.remove(
                self.destination,
                host="codex",
                skills=["sample"],
                force=True,
                root=self.source,
            )
        self.assertIn("Not managed by this installation", str(ctx2.exception))
        self.assertEqual(
            "User unmanaged skill", (target / "sample/SKILL.md").read_text()
        )

        shutil.rmtree(target / "sample")
        self.install()
        shutil.rmtree(target / "sample")
        outside = self.root / "outside_dir"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("Outside")
        try:
            (target / "sample").symlink_to(outside, target_is_directory=True)
        except OSError:
            return
        with self.assertRaises(ValueError) as sym_ctx:
            installation.install(
                self.destination,
                host="codex",
                pack="starter",
                update=True,
                force=True,
                root=self.source,
            )
        self.assertIn("Unsafe installed package path", str(sym_ctx.exception))

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

    def test_releases_cyclomatic_complexity_and_nesting_bounds(self) -> None:
        assert_module_ast_bounds(
            self,
            ROOT / "scripts/fredlib/releases.py",
            max_complexity=10,
            max_nesting=4,
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

    def test_recover_without_operation_journal_advises_checking_installed(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(FredCliError) as fresh_ctx:
            installation.recover(
                self.destination,
                "codex",
                confirm_interrupted=True,
                root=self.source,
            )
        self.assertEqual("missing_operation_journal", fresh_ctx.exception.code)
        self.assertIn("installed", fresh_ctx.exception.hint or "")

        self.install()
        _, state = installation.locations(
            self.destination, "codex", "project", self.source
        )
        (state / "operation.json").unlink()
        with self.assertRaises(FredCliError) as ctx:
            installation.recover(
                self.destination,
                "codex",
                confirm_interrupted=True,
                root=self.source,
            )
        self.assertEqual("missing_operation_journal", ctx.exception.code)
        self.assertIsNotNone(ctx.exception.hint)
        self.assertIn("installed", ctx.exception.hint or "")

    def test_release_verify_resolves_directory_and_guards_manifest_inputs(self):
        rel_out = self.root / "release-bundle"
        releases.build(rel_out, "starter", root=self.source)
        # Directory path resolves manifest.json automatically
        verified = releases.verify(rel_out)
        self.assertEqual(2, verified["verified_archives"])

        empty_dir = self.root / "empty-release"
        empty_dir.mkdir()
        with self.assertRaises(FredCliError) as missing_man:
            releases.verify(empty_dir)
        self.assertEqual("missing_manifest", missing_man.exception.code)
        self.assertTrue(missing_man.exception.hint)

        arr_manifest = self.root / "array-manifest.json"
        arr_manifest.write_text("[]", encoding="utf-8")
        with self.assertRaises(FredCliError) as arr_ctx:
            releases.verify(arr_manifest)
        self.assertEqual("invalid_json_root", arr_ctx.exception.code)

        missing_keys = self.root / "missing-keys.json"
        atomic_json(missing_keys, {})
        with self.assertRaises(FredCliError) as keys_ctx:
            releases.verify(missing_keys)
        self.assertEqual("invalid_manifest", keys_ctx.exception.code)

        # Missing archive file raises missing_archive FredCliError
        archive_files = list(rel_out.glob("*.zip"))
        self.assertTrue(archive_files)
        archive_files[0].unlink()
        with self.assertRaises(FredCliError) as miss_arch:
            releases.verify(rel_out)
        self.assertEqual("missing_archive", miss_arch.exception.code)

    def test_install_rejects_disallowed_and_binary_skill_files(self):
        sample_dir = self.source / "skills" / "sample"
        bad_cases: list[tuple[str, bytes]] = [
            ("README.md", b"# Readme clutter\n"),
            ("CHANGELOG.md", b"# Changelog clutter\n"),
            ("scripts/runner", b"#!/bin/sh\necho bad\n"),
            ("assets/payload.bin", b"\x00\xff\xfe"),
            ("scripts/bad.py", b"x = 1\x00\n"),
        ]
        for idx, (rel_path, raw_bytes) in enumerate(bad_cases):
            bad_file = sample_dir / rel_path
            bad_file.parent.mkdir(parents=True, exist_ok=True)
            bad_file.write_bytes(raw_bytes)
            dest = self.root / f"bad_install_{idx}"
            try:
                with self.subTest(rel_path=rel_path), self.assertRaises(ValueError):
                    installation.install(
                        dest, host="codex", skills=["sample"], root=self.source
                    )
                self.assertFalse((dest / ".agents/skills/sample").exists())
            finally:
                bad_file.unlink()

    def test_corrupted_catalogs_raise_fred_cli_error_instead_of_directory_fallback(
        self,
    ):
        synth = self.root / "synth-release-repo"
        synth.mkdir(parents=True, exist_ok=True)
        (synth / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        for skill_name in ("arch-boundary-specification", "repo-verify-finding"):
            sdir = synth / "skills" / skill_name
            sdir.mkdir(parents=True)
            (sdir / "SKILL.md").write_text(f"# {skill_name}\n", encoding="utf-8")

        # Absent optional catalogs still fall back cleanly to directory prefix inference
        self.assertEqual(
            ["repo-verify-finding"],
            releases._get_suite_skills(synth, suite_name="repository-workflows"),
        )
        self.assertEqual(
            ["arch-boundary-specification"],
            releases._get_suite_skills(synth, suite_name="software-architecture"),
        )

        # Corrupted catalog/suites.json raises FredCliError instead of falling back to packs/directory
        cat_dir = synth / "catalog"
        cat_dir.mkdir()
        suites_path = cat_dir / "suites.json"
        suites_path.write_text('{"suites": [\n', encoding="utf-8")
        with self.assertRaises(FredCliError) as suites_ctx:
            releases._get_suite_skills(synth, suite_name="repository-workflows")
        self.assertEqual("invalid_json", suites_ctx.exception.code)
        self.assertIn("suites.json", suites_ctx.exception.message)
        suites_path.unlink()

        # Corrupted catalog/packs.json raises FredCliError instead of falling back to skills/ scan
        packs_path = cat_dir / "packs.json"
        packs_path.write_text('{"packs": {\n', encoding="utf-8")
        with self.assertRaises(FredCliError) as packs_ctx:
            releases._get_suite_skills(synth, suite_name="repository-workflows")
        self.assertEqual("invalid_json", packs_ctx.exception.code)
        self.assertIn("packs.json", packs_ctx.exception.message)

        with self.assertRaises(FredCliError) as build_ctx:
            releases.build(self.root / "bad-rel-out", all_packs=True, root=synth)
        self.assertEqual("invalid_json", build_ctx.exception.code)

        # Structurally invalid packs entry also raises FredCliError(code="invalid_catalog")
        packs_path.write_text(
            '{"packs": {"complete": {"skills": "not-a-list"}}}', encoding="utf-8"
        )
        with self.assertRaises(FredCliError) as struct_ctx:
            releases._get_suite_skills(synth)
        self.assertEqual("invalid_catalog", struct_ctx.exception.code)

    def test_cursor_install_preserves_plugin_manifest_and_accepts_legacy_receipt_hash(
        self,
    ) -> None:
        cursor_dir = self.source / "skills/sample/.cursor-plugin"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        (cursor_dir / "plugin.json").write_text(
            json.dumps({"name": "sample", "skills": ["."]}), encoding="utf-8"
        )
        dest = self.root / "cursor_legacy_ws"
        res = installation.install(
            dest, host="cursor", skills=["sample"], root=self.source
        )
        installed_pkg = Path(res["target"]) / "sample"
        self.assertTrue((installed_pkg / ".cursor-plugin/plugin.json").is_file())
        _, state = installation.locations(dest, "cursor", "project", self.source)
        rec = installation.receipt(state)
        self.assertEqual("1.0", rec["schema_version"])
        self.assertIn(".cursor-plugin/plugin.json", rec["skills"]["sample"]["files"])

        # Simulate legacy receipt computed without .cursor-plugin/plugin.json
        full_files = package_files(installed_pkg)
        legacy_files = {
            k: v for k, v in full_files.items() if not k.startswith(".cursor-plugin/")
        }
        legacy_hash = digest(legacy_files)
        self.assertNotEqual(legacy_hash, rec["skills"]["sample"]["content_hash"])
        rec["skills"]["sample"]["files"] = legacy_files
        rec["skills"]["sample"]["content_hash"] = legacy_hash
        atomic_json(state / "receipt.json", rec)

        queried = installation.query_installed(
            dest, "cursor", "project", root=self.source
        )
        self.assertTrue(queried["healthy"])
        self.assertEqual("tracked", queried["skills"]["sample"]["status"])
        installation.require_unmodified(installed_pkg, rec["skills"]["sample"])

        # Updating refreshes receipt hash to include .cursor-plugin/plugin.json without --force
        (self.source / "skills/sample/SKILL.md").write_text(
            "Updated Cursor skill\n", encoding="utf-8"
        )
        installation.install(dest, host="cursor", update=True, root=self.source)
        refreshed = installation.receipt(state)
        self.assertEqual(
            digest(package_files(installed_pkg)),
            refreshed["skills"]["sample"]["content_hash"],
        )
        self.assertIn(
            ".cursor-plugin/plugin.json", refreshed["skills"]["sample"]["files"]
        )

    def test_multi_host_update_isolates_per_host_receipts_and_ignores_unmanaged_markers(
        self,
    ) -> None:
        ws = self.root / "isolated_multihost_ws"
        (ws / ".claude").mkdir(parents=True)
        (ws / ".cursor").mkdir(parents=True)
        (ws / ".gemini").mkdir(parents=True)

        # Update without any receipts refuses to install onto unmanaged markers
        with self.assertRaises(ValueError) as no_rec_ctx:
            installation.install(ws, update=True, root=self.source)
        self.assertIn("--host", str(no_rec_ctx.exception))
        self.assertFalse((ws / ".gemini/skills").exists())

        # Install distinct skill subsets on claude-code and cursor while leaving .gemini unmanaged
        installation.install(
            ws, host="claude-code", skills=["sample"], root=self.source
        )
        installation.install(ws, host="cursor", skills=["other"], root=self.source)

        (self.source / "skills/sample/SKILL.md").write_text(
            "Sample v2\n", encoding="utf-8"
        )
        (self.source / "skills/other/SKILL.md").write_text(
            "Other v2\n", encoding="utf-8"
        )

        upd = installation.install(ws, update=True, root=self.source)
        self.assertEqual("update", upd["action"])
        by_host = {h["host"]: h["skills"] for h in upd["hosts"]}
        self.assertEqual({"claude-code": ["sample"], "cursor": ["other"]}, by_host)
        self.assertEqual(
            "Sample v2\n",
            (ws / ".claude/skills/sample/SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertFalse((ws / ".claude/skills/other").exists())
        self.assertEqual(
            "Other v2\n",
            (ws / ".cursor/skills/other/SKILL.md").read_text(encoding="utf-8"),
        )
        self.assertFalse((ws / ".cursor/skills/sample").exists())
        self.assertFalse((ws / ".gemini/skills").exists())
        self.assertFalse((ws / ".fred-skills/gemini-cli-project").exists())

    def test_antigravity_user_scope_longest_prefix_marker_detection(self) -> None:
        home_antigravity = self.root / "home_antigravity"
        (home_antigravity / ".gemini/antigravity").mkdir(parents=True)
        self.assertEqual(
            "antigravity",
            installation.detect_host(home_antigravity, scope="user"),
        )
        target, state = installation.locations(
            home_antigravity, scope="user", root=ROOT
        )
        self.assertEqual(
            (home_antigravity / ".gemini/antigravity/skills").resolve(), target
        )
        self.assertEqual(
            (home_antigravity / ".fred-skills/antigravity-user").resolve(), state
        )

        home_gemini = self.root / "home_gemini"
        (home_gemini / ".gemini").mkdir(parents=True)
        self.assertEqual(
            "gemini-cli",
            installation.detect_host(home_gemini, scope="user"),
        )

        # Explicit gemini-cli user receipt alongside .gemini/antigravity preserves ambiguity guard
        gemini_state = home_antigravity / ".fred-skills/gemini-cli-user"
        gemini_state.mkdir(parents=True)
        atomic_json(
            gemini_state / "receipt.json",
            {
                "schema_version": "1.0",
                "host": "gemini-cli",
                "skills": {},
                "history": [],
            },
        )
        with self.assertRaises(ValueError) as ambig_ctx:
            installation.detect_host(home_antigravity, scope="user")
        self.assertIn("--host", str(ambig_ctx.exception))

    def test_verify_partial_flags_and_extracted_license_compatibility(self) -> None:
        (self.source / "LICENSE").write_text("MIT License\n", encoding="utf-8")
        rel_dir = self.root / "partial_verify_bundle"
        manifest = releases.build(rel_dir, skills=["sample"], root=self.source)
        tar_name = f"fred-skills-{manifest['version']}-selected.tar"
        zip_name = f"fred-skills-{manifest['version']}-selected.zip"

        # Extracting pack tar (which contains LICENSE) into .agents/skills/ keeps installation healthy
        dest = self.root / "extracted_pack_dest"
        skills_target = dest / ".agents/skills"
        skills_target.mkdir(parents=True)
        with tarfile.open(rel_dir / tar_name, "r") as tf:
            self.assertIn("LICENSE", tf.getnames())
            tf.extractall(skills_target, filter="data")
        self.assertTrue((skills_target / "LICENSE").is_file())
        status_before = installation.query_installed(
            installation.TargetEnvironment(dest, "codex", "project"),
            root=self.source,
        )
        self.assertNotIn("LICENSE", status_before.get("untracked", []))
        installation.install(
            installation.TargetEnvironment(dest, "codex", "project"),
            installation.InstallOptions(skills=["other"]),
            root=self.source,
        )
        status_after = installation.query_installed(
            installation.TargetEnvironment(dest, "codex", "project"),
            root=self.source,
        )
        self.assertNotIn("LICENSE", status_after.get("untracked", []))

        # Remove .zip to simulate a partial release download
        (rel_dir / zip_name).unlink()
        with self.assertRaises(FredCliError) as strict_err:
            releases.verify(rel_dir / "manifest.json")
        self.assertEqual("missing_archive", strict_err.exception.code)

        by_archive = releases.verify(
            rel_dir / "manifest.json",
            archive=tar_name,
            expected_version=manifest["version"],
            require_licensing=True,
        )
        self.assertEqual(1, by_archive["verified_archives"])

        by_ignore = releases.verify(
            rel_dir / "manifest.json",
            ignore_missing=True,
            expected_version=manifest["version"],
            require_licensing=True,
        )
        self.assertEqual(1, by_ignore["verified_archives"])

        with self.assertRaises(FredCliError) as ver_err:
            releases.verify(
                rel_dir / "manifest.json", archive=tar_name, expected_version="9.9.9"
            )
        self.assertIn("does not match VERSION", str(ver_err.exception))

        with self.assertRaises(FredCliError) as unk_err:
            releases.verify(rel_dir / "manifest.json", archive="nonexistent.tar")
        self.assertEqual("no_matching_archives", unk_err.exception.code)

        # Tampered SHA256SUMS must still fail during --archive and --ignore-missing
        orig_sums = (rel_dir / "SHA256SUMS").read_text(encoding="utf-8")
        (rel_dir / "SHA256SUMS").write_text(
            f"{'0' * 64}  {tar_name}\n", encoding="utf-8"
        )
        with self.assertRaises(FredCliError):
            releases.verify(rel_dir / "manifest.json", archive=tar_name)
        with self.assertRaises(FredCliError):
            releases.verify(rel_dir / "manifest.json", ignore_missing=True)
        (rel_dir / "SHA256SUMS").write_text(orig_sums, encoding="utf-8")

        # Zero matched archives on disk with --ignore-missing raises FredCliError
        (rel_dir / tar_name).unlink()
        with self.assertRaises(FredCliError) as zero_err:
            releases.verify(rel_dir / "manifest.json", ignore_missing=True)
        self.assertEqual("no_matching_archives", zero_err.exception.code)

    def test_stdlib_only_imports_do_not_eagerly_load_optional_packages(self) -> None:
        probe = (
            "import sys; sys.path.insert(0, 'scripts'); "
            "import fredlib, skill_tools; "
            "from fredlib import common, evaluations, installation, releases, reports, repository, runs, vetting; "
            "review_starter = common._DEFAULT_SUITE_CONTRACTS['code-review']['starter']; "
            "assert runs.audit_errors({}) and common._validate_canonical_artifact(review_starter, artifact_name='review.json') == []; "
            "reports._validate_domain_payload(review_starter, 'review'); "
            "assert 'yaml' not in sys.modules; "
            "assert 'jsonschema' not in sys.modules"
        )
        proc = subprocess.run(
            [sys.executable, "-S", "-c", probe],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)


if __name__ == "__main__":
    unittest.main()

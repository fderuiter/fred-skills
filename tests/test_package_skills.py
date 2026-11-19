"""Tests for standalone skill packaging, suite bundling, and checksum manifests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_skills as package_skills_module
from fredlib import releases
from fredlib.common import package_files
from fredlib.releases import (
    _collect_skill_files,
    _collect_skills_entries,
    _create_tar_gz_bytes,
    _write_tar_archive,
    _write_tar_entries,
    _write_zip_archive,
)
from package_skills import (
    collect_skill_files,
    get_suite_skills,
    package_skills,
    verify_checksums,
)


class PackageSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_get_suite_skills_returns_audit_skills(self):
        skills = get_suite_skills(ROOT)
        self.assertEqual(52, len(skills))
        for skill in skills:
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        audit_skills = get_suite_skills(ROOT, suite_name="repository-audits")
        self.assertEqual(37, len(audit_skills))
        for skill in audit_skills:
            self.assertTrue(skill.startswith("repo-audit-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        arch_skills = get_suite_skills(ROOT, suite_name="software-architecture")
        self.assertEqual(3, len(arch_skills))
        for skill in arch_skills:
            self.assertTrue(skill.startswith("arch-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        review_skills = get_suite_skills(ROOT, suite_name="code-review")
        self.assertEqual(3, len(review_skills))
        for skill in review_skills:
            self.assertTrue(skill.startswith("review-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        test_skills = get_suite_skills(ROOT, suite_name="automated-testing")
        self.assertEqual(3, len(test_skills))
        for skill in test_skills:
            self.assertTrue(skill.startswith("test-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

    def test_package_skills_creates_all_archives_and_checksums(self):
        suite_skills = get_suite_skills(ROOT)
        self.assertEqual(52, len(suite_skills))
        checksums = package_skills(self.output_dir, root=ROOT)
        expected_archive_count = len(suite_skills) + 1
        self.assertEqual(53, expected_archive_count)
        self.assertEqual(expected_archive_count, len(checksums))
        self.assertIn("fred-skills-suite.tar.gz", checksums)
        self.assertIn("repo-audit-concurrency.tar.gz", checksums)
        self.assertIn("repo-apply-fix.tar.gz", checksums)
        self.assertIn("arch-decision-record.tar.gz", checksums)
        self.assertIn("review-diff-analysis.tar.gz", checksums)
        self.assertIn("test-gap-analysis.tar.gz", checksums)

        manifest_file = self.output_dir / "checksums.sha256"
        self.assertTrue(manifest_file.is_file())

        verified = verify_checksums(self.output_dir)
        self.assertEqual(expected_archive_count, len(verified))
        for name in checksums:
            self.assertTrue(verified[name])
            archive_path = self.output_dir / name
            self.assertTrue(archive_path.is_file())
            self.assertEqual(
                checksums[name],
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )

    def test_individual_skill_archive_structure(self):
        package_skills(self.output_dir, root=ROOT)
        archive_path = self.output_dir / "repo-audit-concurrency.tar.gz"
        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("repo-audit-concurrency/SKILL.md", names)
            self.assertIn("repo-audit-concurrency/references/method.md", names)
            for name in names:
                self.assertTrue(name.startswith("repo-audit-concurrency/"))
                self.assertNotIn("__pycache__", name)
                self.assertFalse(name.endswith(".pyc"))
                self.assertFalse(name.endswith(".pyo"))

    def test_suite_archive_contains_all_skills(self):
        package_skills(self.output_dir, root=ROOT)
        suite_path = self.output_dir / "fred-skills-suite.tar.gz"
        with tarfile.open(suite_path, "r:gz") as tar:
            names = set(tar.getnames())
            for skill in get_suite_skills(ROOT):
                self.assertIn(f"{skill}/SKILL.md", names)

    def test_package_skills_raises_on_empty_suite(self):
        empty_root = self.output_dir / "empty_repo"
        empty_catalog = empty_root / "catalog"
        empty_catalog.mkdir(parents=True)
        (empty_catalog / "suites.json").write_text(
            '{"schema_version": "1.0", "suites": []}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            package_skills(self.output_dir / "dist", root=empty_root)

    def test_packaging_is_deterministic_and_reproducible(self):
        temp_dir2 = tempfile.TemporaryDirectory()
        try:
            out2 = Path(temp_dir2.name)
            first = package_skills(self.output_dir, root=ROOT)
            second = package_skills(out2, root=ROOT)
            self.assertEqual(first, second)

            for filename in first:
                b1 = (self.output_dir / filename).read_bytes()
                b2 = (out2 / filename).read_bytes()
                self.assertEqual(b1, b2, f"Nondeterministic bytes for {filename}")
        finally:
            temp_dir2.cleanup()

    def test_verify_detects_corrupted_archive(self):
        package_skills(self.output_dir, root=ROOT)
        corrupted = self.output_dir / "repo-audit-concurrency.tar.gz"
        corrupted.write_bytes(corrupted.read_bytes() + b"\x00bad")
        with self.assertRaises(ValueError):
            verify_checksums(self.output_dir)

    def test_collect_skill_files_excludes_pycache_and_bytecode(self):
        skill_dir = ROOT / "skills" / "repo-audit-concurrency"
        files = collect_skill_files(skill_dir)
        paths = [rel for rel, _ in files]
        self.assertIn("SKILL.md", paths)
        for rel in paths:
            self.assertNotIn("__pycache__", rel)
            self.assertFalse(rel.endswith(".pyc"))
            self.assertFalse(rel.endswith(".pyo"))

    def test_get_suite_skills_fallback_to_packs(self):
        repo_root = self.output_dir / "repo_packs_fallback"
        catalog_dir = repo_root / "catalog"
        catalog_dir.mkdir(parents=True)
        (catalog_dir / "packs.json").write_text(
            '{"schema_version": "1.0", "packs": {"complete": {"skills": ["skill-b", "skill-a", "skill-b"]}}}',
            encoding="utf-8",
        )
        skills = get_suite_skills(repo_root)
        self.assertEqual(["skill-a", "skill-b"], skills)

    def test_get_suite_skills_fallback_to_skills_dir(self):
        repo_root = self.output_dir / "repo_dir_fallback"
        skills_dir = repo_root / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "skill-1").mkdir()
        (skills_dir / "skill-1" / "SKILL.md").write_text("# S1", encoding="utf-8")
        (skills_dir / "skill-2").mkdir()
        (skills_dir / "skill-2" / "SKILL.md").write_text("# S2", encoding="utf-8")
        skills = get_suite_skills(repo_root)
        self.assertEqual(["skill-1", "skill-2"], skills)

    def test_get_suite_skills_deduplicates_phases(self):
        repo_root = self.output_dir / "repo_dedup"
        catalog_dir = repo_root / "catalog"
        catalog_dir.mkdir(parents=True)
        (catalog_dir / "suites.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "suites": [
                        {
                            "name": "suite-1",
                            "phases": [
                                {"id": 0, "skills": ["skill-z", "skill-a"]},
                                {"id": 1, "skills": ["skill-a", "skill-m"]},
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        skills = get_suite_skills(repo_root)
        self.assertEqual(["skill-a", "skill-m", "skill-z"], skills)

    def test_package_skills_does_not_contain_create_tar_gz_bytes(self):
        self.assertFalse(hasattr(package_skills_module, "create_tar_gz_bytes"))

    def test_write_tar_entries_normalizes_tarinfo_metadata(self):
        entries = [
            ("b/file.txt", b"second file"),
            ("a/file.txt", b"first file"),
        ]
        buf = io.BytesIO()
        with tarfile.open(mode="w", fileobj=buf, format=tarfile.PAX_FORMAT) as tar:
            _write_tar_entries(tar, entries)

        buf.seek(0)
        with tarfile.open(mode="r", fileobj=buf) as tar:
            members = tar.getmembers()
            self.assertEqual(["a/file.txt", "b/file.txt"], [m.name for m in members])
            for m in members:
                self.assertEqual(0o644, m.mode)
                self.assertEqual(0, m.mtime)
                self.assertEqual(0, m.uid)
                self.assertEqual(0, m.gid)
                self.assertEqual("", m.uname)
                self.assertEqual("", m.gname)
            self.assertEqual(b"first file", tar.extractfile(members[0]).read())
            self.assertEqual(b"second file", tar.extractfile(members[1]).read())

    def test_archive_writers_default_to_0644_on_two_tuple_inputs_and_apply_0755_metadata(
        self,
    ):
        entries = [
            ("b/script.py", b"#!/usr/bin/env python3\nprint('ok')\n"),
            ("a/readme.md", b"# Readme\n"),
        ]
        zip_default = self.output_dir / "default.zip"
        tar_default = self.output_dir / "default.tar"
        _write_zip_archive(zip_default, entries)
        _write_tar_archive(tar_default, entries)
        tgz_default = _create_tar_gz_bytes(entries)

        with zipfile.ZipFile(zip_default, "r") as zf:
            for info in zf.infolist():
                self.assertEqual(0o100644, info.external_attr >> 16)
        with tarfile.open(tar_default, "r") as tf:
            for member in tf.getmembers():
                self.assertEqual(0o644, member.mode)
        with tarfile.open(fileobj=io.BytesIO(tgz_default), mode="r:gz") as tf:
            for member in tf.getmembers():
                self.assertEqual(0o644, member.mode)

        exec_paths = {"b/script.py"}
        zip_exec = self.output_dir / "exec.zip"
        tar_exec = self.output_dir / "exec.tar"
        _write_zip_archive(zip_exec, entries, executable_paths=exec_paths)
        _write_tar_archive(tar_exec, entries, executable_paths=exec_paths)
        tgz_exec = _create_tar_gz_bytes(entries, executable_paths=exec_paths)

        with zipfile.ZipFile(zip_exec, "r") as zf:
            self.assertEqual(0o100644, zf.getinfo("a/readme.md").external_attr >> 16)
            self.assertEqual(0o100755, zf.getinfo("b/script.py").external_attr >> 16)
        for tar_src, mode_str in (
            (tar_exec, "r"),
            (io.BytesIO(tgz_exec), "r:gz"),
        ):
            open_kwargs = (
                {"name": tar_src, "mode": mode_str}
                if isinstance(tar_src, Path)
                else {"fileobj": tar_src, "mode": mode_str}
            )
            with tarfile.open(**open_kwargs) as tf:
                self.assertEqual(0o644, tf.getmember("a/readme.md").mode)
                self.assertEqual(0o755, tf.getmember("b/script.py").mode)

    def test_release_archives_preserve_0755_script_modes_and_shebang_fallback(self):
        expected_scripts = {
            "repo-audit-diagnostics/scripts/triage.py",
            "repo-audit-diagnostics/scripts/validate_audit.py",
            "repo-audit-program/scripts/validate_audit.py",
            "repo-audit-synthesis/scripts/audit_to_sarif.py",
            "repo-audit-synthesis/scripts/validate_audit.py",
        }
        expected_suite_scripts = {
            p.relative_to(ROOT / "skills").as_posix()
            for p in (ROOT / "skills").glob("*/scripts/*.py")
        }
        self.assertGreaterEqual(len(expected_suite_scripts), 53)
        rel_out = self.output_dir / "modes_bundle"
        manifest = releases.build(
            rel_out,
            skills=[
                "repo-audit-diagnostics",
                "repo-audit-program",
                "repo-audit-synthesis",
            ],
            root=ROOT,
        )
        ver = manifest["version"]
        zip_path = rel_out / f"fred-skills-{ver}-selected.zip"
        tar_path = rel_out / f"fred-skills-{ver}-selected.tar"

        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                expected_mode = (
                    0o100755 if info.filename in expected_scripts else 0o100644
                )
                self.assertEqual(
                    expected_mode,
                    info.external_attr >> 16,
                    f"Unexpected ZIP mode for {info.filename}",
                )
            self.assertTrue(expected_scripts.issubset(set(zf.namelist())))

        with tarfile.open(tar_path, "r") as tf:
            for member in tf.getmembers():
                expected_mode = 0o755 if member.name in expected_scripts else 0o644
                self.assertEqual(
                    expected_mode,
                    member.mode,
                    f"Unexpected TAR mode for {member.name}",
                )

        tgz_out = self.output_dir / "standalone_tgz"
        package_skills(tgz_out, root=ROOT)
        with tarfile.open(tgz_out / "fred-skills-suite.tar.gz", "r:gz") as suite_tf:
            suite_names = {m.name for m in suite_tf.getmembers()}
            self.assertTrue(expected_suite_scripts.issubset(suite_names))
            for member in suite_tf.getmembers():
                expected_mode = (
                    0o755 if member.name in expected_suite_scripts else 0o644
                )
                self.assertEqual(
                    expected_mode,
                    member.mode,
                    f"Unexpected suite tar.gz mode for {member.name}",
                )

        # Simulate Windows NTFS checkout lacking execute bits on disk via shebang fallback
        synth_root = self.output_dir / "ntfs_repo"
        synth_skill = synth_root / "skills" / "synth-skill"
        (synth_skill / "scripts").mkdir(parents=True)
        (synth_skill / "SKILL.md").write_text("# Synth\n", encoding="utf-8")
        shebang_script = synth_skill / "scripts" / "tool.py"
        shebang_script.write_text(
            "#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8"
        )
        shebang_script.chmod(0o644)
        (synth_root / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        (synth_root / "catalog").mkdir(parents=True)
        (synth_root / "catalog" / "skills.json").write_text(
            json.dumps({"skills": [{"name": "synth-skill"}]}), encoding="utf-8"
        )
        ntfs_out = self.output_dir / "ntfs_out"
        with patch.object(releases.os, "name", "nt"):
            releases.build(ntfs_out, skills=["synth-skill"], root=synth_root)
        with zipfile.ZipFile(ntfs_out / "fred-skills-0.2.0-selected.zip", "r") as zf:
            self.assertEqual(
                0o100755,
                zf.getinfo("synth-skill/scripts/tool.py").external_attr >> 16,
            )
            self.assertEqual(
                0o100644,
                zf.getinfo("synth-skill/SKILL.md").external_attr >> 16,
            )

        if os.name != "nt":
            bin_script = synth_skill / "scripts" / "runner"
            bin_script.write_text("echo ok\n", encoding="utf-8")
            bin_script.chmod(0o755)
            posix_out = self.output_dir / "posix_out"
            releases.build(posix_out, skills=["synth-skill"], root=synth_root)
            with tarfile.open(posix_out / "fred-skills-0.2.0-selected.tar", "r") as tf:
                self.assertEqual(0o755, tf.getmember("synth-skill/scripts/runner").mode)

    def test_pure_windows_path_sorting_matches_posix_order(self):
        win_root = PureWindowsPath(r"C:\repo\skills\sample")
        win_paths = [
            win_root / "SKILL.md",
            win_root / "assets" / "prompt.md",
            win_root / "References" / "Guide.md",
            win_root / "references" / "method.md",
        ]
        # Raw PureWindowsPath sorting is case-insensitive and diverges from ASCII POSIX order
        raw_win_order = [p.relative_to(win_root).as_posix() for p in sorted(win_paths)]
        self.assertEqual("assets/prompt.md", raw_win_order[0])

        posix_root = PurePosixPath("/repo/skills/sample")
        posix_paths = [
            posix_root / "SKILL.md",
            posix_root / "assets" / "prompt.md",
            posix_root / "References" / "Guide.md",
            posix_root / "references" / "method.md",
        ]
        normalized_win = [
            p.relative_to(win_root).as_posix()
            for p in sorted(win_paths, key=lambda p: p.relative_to(win_root).as_posix())
        ]
        normalized_posix = [
            p.relative_to(posix_root).as_posix()
            for p in sorted(
                posix_paths, key=lambda p: p.relative_to(posix_root).as_posix()
            )
        ]
        self.assertEqual(
            [
                "References/Guide.md",
                "SKILL.md",
                "assets/prompt.md",
                "references/method.md",
            ],
            normalized_win,
        )
        self.assertEqual(normalized_posix, normalized_win)

        # Verify _collect_skill_files and package_files preserve exact ASCII POSIX key order
        # even if rglob yields files in Windows case-insensitive order
        repo_root = self.output_dir / "sort_repo"
        pkg = repo_root / "skills" / "case-skill"
        (pkg / "assets").mkdir(parents=True)
        (pkg / "references").mkdir(parents=True)
        (pkg / "SKILL.md").write_text("# Case\n", encoding="utf-8")
        (pkg / "assets" / "prompt.md").write_text("prompt\n", encoding="utf-8")
        (pkg / "references" / "Z_note.md").write_text("Z\n", encoding="utf-8")
        (pkg / "references" / "a_note.md").write_text("a\n", encoding="utf-8")

        windows_like_yield = [
            pkg / "assets",
            pkg / "assets" / "prompt.md",
            pkg / "references",
            pkg / "references" / "a_note.md",
            pkg / "references" / "Z_note.md",
            pkg / "SKILL.md",
        ]
        expected_order = [
            "SKILL.md",
            "assets/prompt.md",
            "references/Z_note.md",
            "references/a_note.md",
        ]
        with patch.object(Path, "rglob", return_value=iter(windows_like_yield)):
            collected = _collect_skill_files(pkg, root=repo_root)
        self.assertEqual(expected_order, [rel for rel, _ in collected])

        with patch.object(Path, "rglob", return_value=iter(windows_like_yield)):
            pkg_map = package_files(pkg)
        self.assertEqual(expected_order, list(pkg_map.keys()))

    def test_write_zip_archive_uses_level_9_deflate_compression(self):
        payload = (
            ROOT / "skills" / "repo-audit-diagnostics" / "scripts" / "triage.py"
        ).read_bytes()
        comp6 = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
        deflated_lvl6 = comp6.compress(payload) + comp6.flush()
        comp9 = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        deflated_lvl9 = comp9.compress(payload) + comp9.flush()
        self.assertNotEqual(len(deflated_lvl6), len(deflated_lvl9))

        zip_path = self.output_dir / "level9.zip"
        _write_zip_archive(
            zip_path,
            [("repo-audit-diagnostics/scripts/triage.py", payload)],
            executable_paths={"repo-audit-diagnostics/scripts/triage.py"},
        )
        with zipfile.ZipFile(zip_path, "r") as zf:
            info = zf.getinfo("repo-audit-diagnostics/scripts/triage.py")
            self.assertEqual(len(deflated_lvl9), info.compress_size)
            self.assertLess(info.compress_size, len(deflated_lvl6))
            self.assertEqual(payload, zf.read(info.filename))

    def test_collect_skills_entries_namespaces_and_sorts_files(self):
        entries = _collect_skills_entries(["repo-audit-concurrency"], root=ROOT)
        self.assertGreater(len(entries), 0)
        paths = [path for path, _ in entries]
        self.assertIn("repo-audit-concurrency/SKILL.md", paths)
        for path, data in entries:
            self.assertTrue(path.startswith("repo-audit-concurrency/"))
            self.assertGreater(len(data), 0)

        entries_multi = _collect_skills_entries(
            ["repo-audit-concurrency", "repo-apply-fix"], root=ROOT
        )
        prefixes = [p.split("/")[0] for p, _ in entries_multi]
        first_concurrency_idx = prefixes.index("repo-audit-concurrency")
        last_apply_fix_idx = len(prefixes) - 1 - prefixes[::-1].index("repo-apply-fix")
        self.assertLess(last_apply_fix_idx, first_concurrency_idx)


if __name__ == "__main__":
    unittest.main()

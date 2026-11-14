"""Tests for standalone skill packaging, suite bundling, and checksum manifests."""

from __future__ import annotations

import hashlib
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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

    def test_get_suite_skills_returns_36_audit_skills(self):
        skills = get_suite_skills(ROOT)
        self.assertEqual(36, len(skills))
        for skill in skills:
            self.assertTrue(skill.startswith("repo-audit-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

    def test_package_skills_creates_all_archives_and_checksums(self):
        checksums = package_skills(self.output_dir, root=ROOT)
        # 36 individual + 1 consolidated suite = 37 archives
        self.assertEqual(37, len(checksums))
        self.assertIn("fred-skills-suite.tar.gz", checksums)
        self.assertIn("repo-audit-concurrency.tar.gz", checksums)

        manifest_file = self.output_dir / "checksums.sha256"
        self.assertTrue(manifest_file.is_file())

        verified = verify_checksums(self.output_dir)
        self.assertEqual(37, len(verified))
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

    def test_suite_archive_contains_all_36_skills(self):
        package_skills(self.output_dir, root=ROOT)
        suite_path = self.output_dir / "fred-skills-suite.tar.gz"
        with tarfile.open(suite_path, "r:gz") as tar:
            names = set(tar.getnames())
            for skill in get_suite_skills(ROOT):
                self.assertIn(f"{skill}/SKILL.md", names)

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


if __name__ == "__main__":
    unittest.main()

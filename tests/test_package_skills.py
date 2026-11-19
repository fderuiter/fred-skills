"""Tests for standalone skill packaging, suite bundling, and checksum manifests."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import package_skills as package_skills_module
from fredlib.releases import (
    _collect_skills_entries,
    _write_tar_entries,
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
        self.assertEqual(60, len(skills))
        for skill in skills:
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        audit_skills = get_suite_skills(ROOT, suite_name="repository-audits")
        self.assertEqual(36, len(audit_skills))
        for skill in audit_skills:
            self.assertTrue(skill.startswith("repo-audit-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        arch_skills = get_suite_skills(ROOT, suite_name="software-architecture")
        self.assertEqual(6, len(arch_skills))
        for skill in arch_skills:
            self.assertTrue(skill.startswith("arch-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        review_skills = get_suite_skills(ROOT, suite_name="code-review")
        self.assertEqual(6, len(review_skills))
        for skill in review_skills:
            self.assertTrue(skill.startswith("review-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

        test_skills = get_suite_skills(ROOT, suite_name="automated-testing")
        self.assertEqual(6, len(test_skills))
        for skill in test_skills:
            self.assertTrue(skill.startswith("test-"))
            self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

    def test_package_skills_creates_all_archives_and_checksums(self):
        suite_skills = get_suite_skills(ROOT)
        self.assertEqual(60, len(suite_skills))
        checksums = package_skills(self.output_dir, root=ROOT)
        expected_archive_count = len(suite_skills) + 1
        self.assertEqual(61, expected_archive_count)
        self.assertEqual(expected_archive_count, len(checksums))
        self.assertIn("fred-skills-suite.tar.gz", checksums)
        self.assertIn("repo-audit-concurrency.tar.gz", checksums)
        self.assertIn("repo-apply-fix.tar.gz", checksums)
        self.assertIn("arch-decision-record.tar.gz", checksums)
        self.assertIn("arch-data-evolution.tar.gz", checksums)
        self.assertIn("review-diff-analysis.tar.gz", checksums)
        self.assertIn("review-api-compatibility.tar.gz", checksums)
        self.assertIn("test-gap-analysis.tar.gz", checksums)
        self.assertIn("test-flakiness-isolation.tar.gz", checksums)

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

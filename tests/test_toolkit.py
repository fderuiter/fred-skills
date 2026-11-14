import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import reports, repository, runs
from fredlib.common import atomic_json, read_json


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "application"
        self.repo.mkdir()
        (self.repo / "source.py").write_text("value = 1\n")
        self.output = self.root / "run"

    def tearDown(self):
        self.temp.cleanup()

    def initialize(self):
        return runs.initialize(
            self.repo, self.output, "investigate-bug", budget="brief"
        )

    def fixture(self):
        run = self.initialize()
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["run_id"], audit["repository"] = (
            run["run_id"],
            runs.empty_audit(run)["repository"],
        )
        audit["evidence"][0]["path"] = "source.py"
        atomic_json(self.output / "audit.json", audit)
        return audit

    def test_init_preserves_authority_and_validates(self):
        run = self.initialize()
        self.assertEqual(
            {"application_changes": False, "issue_publication": False}, run["authority"]
        )
        self.assertEqual([], runs.validate_run(self.output))
        self.assertIn("Deferred skills:", (self.output / "prompt.md").read_text())
        with self.assertRaises(ValueError):
            self.initialize()

    def test_snapshot_and_resume_detect_changes_without_rewriting_evidence(self):
        audit = self.fixture()
        (self.repo / "source.py").write_text("value = 2\n")
        result = runs.revalidate(self.output)
        self.assertEqual(["source.py"], result["changed_files"])
        self.assertEqual(["F-1"], result["stale_finding_ids"])
        self.assertEqual(["F-1"], result["needs_impact_review"])
        self.assertEqual(audit, read_json(self.output / "audit.json"))

    def test_checkpoint_and_memory_have_provenance(self):
        self.fixture()
        changed = runs.checkpoint(
            self.output, "paused", "Inspect the completion caller next"
        )
        self.assertEqual("paused", changed["state"])
        memory = runs.memory(self.output)
        self.assertEqual(
            changed["snapshot"]["files"]["source.py"],
            memory["evidence"][0]["content_sha256"],
        )
        self.assertTrue(memory["limitations"])

    def test_internal_output_is_excluded_from_snapshot(self):
        self.output = self.repo / "review"
        run = self.initialize()
        self.assertFalse(runs.revalidate(self.output)["changed_files"])
        self.assertIn("review/**", run["snapshot"]["exclude"])

    def test_changes_mode_requires_a_real_base(self):
        with self.assertRaises(ValueError):
            runs.plan(self.repo, "review-pr", mode="changes")
        with self.assertRaises(ValueError):
            runs.plan(self.repo, "review-pr", mode="changes", base="--evil")

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_git_diff_planning_includes_committed_and_dirty_changes(self):
        for argv in [
            ["init", "-q"],
            ["config", "user.name", "Fixture"],
            ["config", "user.email", "fixture@example.invalid"],
            ["add", "."],
            ["commit", "-qm", "fixture"],
        ]:
            subprocess.run(["git", "-C", str(self.repo), *argv], check=True)
        base = repository.git(self.repo, "rev-parse", "HEAD", required=True)
        (self.repo / "source.py").write_text("value = 3\n")
        selected, detected = runs.plan(
            self.repo, "review-pr", mode="changes", base=base
        )
        self.assertEqual(["source.py"], selected["changed_files"])
        self.assertTrue(detected["repository"]["dirty"])

    def test_snapshot_does_not_follow_external_symlinks(self):
        outside = self.root / "outside"
        outside.write_text("private")
        try:
            (self.repo / "linked").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks unavailable")
        snap = repository.snapshot(self.repo)
        self.assertFalse(snap["complete"])
        self.assertNotIn("linked", snap["files"])

    def test_snapshot_limits_are_visible(self):
        (self.repo / "large.bin").write_bytes(b"x" * 100)
        snap = repository.snapshot(self.repo, max_bytes=10)
        self.assertFalse(snap["complete"])
        self.assertEqual("size-limit", snap["skipped"]["large.bin"])

    def test_tampered_snapshot_is_rejected(self):
        run = self.initialize()
        run["snapshot"]["files"]["source.py"] = "invented"
        atomic_json(self.output / "run.json", run)
        self.assertTrue(
            any("content identity" in error for error in runs.validate_run(self.output))
        )

    def test_project_config_cannot_supply_commands_or_authority(self):
        config = self.root / "settings.yml"
        config.write_text('commands: ["do something"]\n')
        with self.assertRaises(ValueError):
            runs.plan(self.repo, "understand-repo", config=config)
        config.write_text(
            'profile: browser-async\ncritical_workflows: ["Camera lifecycle"]\nexclude: ["fixtures/**"]\n'
        )
        selected, detected = runs.plan(self.repo, "understand-repo", config=config)
        self.assertEqual(["Camera lifecycle"], selected["critical_workflows"])
        self.assertIn("repo-audit-cancellation", selected["skills"])
        self.assertIn("fixtures/**", detected["snapshot"]["exclude"])

    def test_compare_uses_identity_not_run_ids_or_line_numbers(self):
        before = read_json(ROOT / "tests/fixtures/audit-valid.json")
        after = copy.deepcopy(before)
        after["findings"][0]["id"] = "F-new"
        after["evidence"][0]["line_start"] = 80
        self.assertEqual(
            [{"before": "F-1", "after": "F-new"}],
            runs.compare(before, after)["persisting"],
        )
        after["findings"] = []
        result = runs.compare(before, after)
        self.assertEqual(["F-1"], result["not_observed"])
        self.assertNotIn("resolved", result)

    def test_ambiguous_fingerprints_are_retained(self):
        before = read_json(ROOT / "tests/fixtures/audit-valid.json")
        after = copy.deepcopy(before)
        after["findings"].append({**copy.deepcopy(after["findings"][0]), "id": "F-2"})
        self.assertEqual(1, len(runs.compare(before, after)["ambiguous"]))

    def test_verified_fix_requires_structured_execution_proof(self):
        audit = self.fixture()
        with self.assertRaises(ValueError):
            runs.reconcile(self.output, "F-1", "verified-fixed", "looks fixed")
        proof = {
            "before_revision": "before",
            "after_revision": "after",
            "original_reproduced": True,
            "regression_passed": True,
            "safe_controls_passed": True,
            "commands": ["python synthetic.py"],
            "result": "Synthetic controls passed",
        }
        atomic_json(self.root / "proof.json", proof)
        record = runs.reconcile(
            self.output, "F-1", "verified-fixed", "reviewed", self.root / "proof.json"
        )
        self.assertEqual("verified-fixed", record["state"])
        self.assertEqual(
            audit["findings"][0]["status"],
            read_json(self.output / "audit.json")["findings"][0]["status"],
        )
        proof["safe_controls_passed"] = False
        atomic_json(self.root / "proof.json", proof)
        with self.assertRaises(ValueError):
            runs.reconcile(
                self.output,
                "F-1",
                "verified-fixed",
                "not verified",
                self.root / "proof.json",
            )

    def test_sarif_import_does_not_promote_scanner_claims_or_external_paths(self):
        sarif = {
            "runs": [
                {
                    "tool": {"driver": {"name": "synthetic"}},
                    "results": [
                        {
                            "ruleId": "R-1",
                            "message": {"text": "reported concern"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "../escape"}
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "source.py"},
                                        "region": {"startLine": 1},
                                    }
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        atomic_json(self.root / "scan.json", sarif)
        result = runs.import_sarif(self.root / "scan.json")
        self.assertEqual(
            [{"path": "source.py", "line": 1}], result["observations"][0]["locations"]
        )
        self.assertNotIn("findings", result)


class ReportTests(unittest.TestCase):
    def test_untrusted_source_is_escaped_and_no_network_is_required(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["findings"][0]["title"] = '<img src=x onerror="window.injected=true">'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            reports.render(audit, path)
            page = path.read_text()
            self.assertIn("&lt;img", page)
            self.assertNotIn("<img src=x", page)
            self.assertIn("connect-src", page)
            self.assertIn("sha256-", page)
            self.assertIn(
                '<tr><td colspan="5" class="empty-state">No coverage recorded.</td></tr>',
                page,
            )
            self.assertIn(
                '<tr><td colspan="4" class="empty-state">No checks recorded.</td></tr>',
                page,
            )
            self.assertIn('class="badge badge-medium medium"', page)
            self.assertIn('<div class="table-container"><table>', page)
            self.assertIn('<div id="empty-state" class="empty-state" hidden>', page)
            self.assertIn('id="reset-filters"', page)
            self.assertIn("--table-border", page)

    def test_mixed_empty_sections_and_badge_severities(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["coverage"] = [
            {
                "topic": "concurrency",
                "scope": "core",
                "status": "inspected",
                "evidence_ids": ["E-1"],
                "notes": "Verified thread safety",
                "next_action": "none",
            }
        ]
        audit["checks"] = []
        audit["findings"][0]["severity"] = "critical"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            reports.render(audit, path)
            page = path.read_text()
            self.assertIn("Verified thread safety", page)
            self.assertNotIn("No coverage recorded.", page)
            self.assertIn(
                '<tr><td colspan="4" class="empty-state">No checks recorded.</td></tr>',
                page,
            )
            self.assertIn('class="badge badge-critical critical"', page)

    def test_issue_drafts_exclude_published_records_and_do_not_overwrite(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["issues"] = [
            {
                "id": "I-1",
                "title": "Correct publication",
                "finding_ids": ["F-1"],
                "depends_on": [],
                "related_issues": [],
                "existing_url": None,
                "publication": "draft",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "issues"
            self.assertEqual(1, len(reports.issue_drafts(audit, output)))
            with self.assertRaises(ValueError):
                reports.issue_drafts(audit, output)


if __name__ == "__main__":
    unittest.main()

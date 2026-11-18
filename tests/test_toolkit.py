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
from fredlib.common import atomic_json, read_json, resolve_audit_path


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
        with self.assertRaises(ValueError):
            runs.plan(self.repo, "review-code-changes", mode="changes")
        with self.assertRaises(ValueError):
            runs.plan(self.repo, "review-code-changes", mode="changes", base="--evil")

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

    def test_sarif_import_resolves_file_uris_via_base_ids_working_dir_and_root(self):
        sarif_base_ids = {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "CodeQL", "semanticVersion": "2.19.0"}},
                    "originalUriBaseIds": {
                        "%SRCROOT%": {"uri": "file:///workspace/repo/"},
                        "SUBPKG": {"uri": "packages/api/", "uriBaseId": "%SRCROOT%"},
                        "EXTERNAL": {"uri": "file:///usr/include/"},
                    },
                    "artifacts": [
                        {
                            "location": {
                                "uri": "file:///workspace/repo/src/from_artifact.py"
                            }
                        }
                    ],
                    "results": [
                        {
                            "ruleId": "py/sql-injection",
                            "message": {"text": "SQL query built from user input"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///workspace/repo/src/db%20pool.py"
                                        },
                                        "region": {"startLine": 10, "endLine": 14},
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "routes.py",
                                            "uriBaseId": "SUBPKG",
                                        },
                                        "region": {"startLine": 5},
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"index": 0},
                                        "region": {"startLine": 3},
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "stdio.h",
                                            "uriBaseId": "EXTERNAL",
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///workspace/other/secret.py"
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///workspace/repo/../secret.py"
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///workspace/repo/%2e%2e/secret.py"
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///workspace/repo/.git/config"
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "https://example.com/workspace/repo/a.py"
                                        }
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file://evil.example/workspace/repo/a.py"
                                        }
                                    }
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        atomic_json(self.root / "base_ids.sarif", sarif_base_ids)
        imported_base = runs.import_sarif(self.root / "base_ids.sarif")
        self.assertEqual("2.19.0", imported_base["observations"][0]["tool_version"])
        self.assertEqual(
            [
                {"path": "src/db pool.py", "line": 10, "end_line": 14},
                {"path": "packages/api/routes.py", "line": 5},
                {"path": "src/from_artifact.py", "line": 3},
            ],
            imported_base["observations"][0]["locations"],
        )

        sarif_workdir = {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "semgrep", "version": "1.90.0"}},
                    "invocations": [
                        {
                            "executionSuccessful": True,
                            "workingDirectory": {"uri": "file:///builds/service"},
                        }
                    ],
                    "results": [
                        {
                            "ruleId": "python.exec",
                            "message": {"text": "avoid exec"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///builds/service/app/runner.py"
                                        },
                                        "region": {"startLine": 22},
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": "file:///builds/service-other/leak.py"
                                        },
                                        "region": {"startLine": 1},
                                    }
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        atomic_json(self.root / "workdir.sarif", sarif_workdir)
        imported_workdir = runs.import_sarif(self.root / "workdir.sarif")
        self.assertEqual(
            [{"path": "app/runner.py", "line": 22}],
            imported_workdir["observations"][0]["locations"],
        )

        outside_file = self.root / "outside.py"
        outside_file.write_text("secret = 1\n")
        sarif_explicit_root = {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "semgrep", "version": "1.90.0"}},
                    "originalUriBaseIds": {
                        "%SRCROOT%": {"uri": self.root.resolve().as_uri() + "/"}
                    },
                    "results": [
                        {
                            "ruleId": "R-root",
                            "message": {"text": "scoped to explicit root"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": (self.repo / "source.py")
                                            .resolve()
                                            .as_uri()
                                        },
                                        "region": {"startLine": 1},
                                    }
                                },
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {
                                            "uri": outside_file.resolve().as_uri()
                                        },
                                        "region": {"startLine": 1},
                                    }
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        atomic_json(self.root / "explicit_root.sarif", sarif_explicit_root)
        imported_root = runs.import_sarif(
            self.root / "explicit_root.sarif", root=self.repo
        )
        self.assertEqual(
            [{"path": "source.py", "line": 1}],
            imported_root["observations"][0]["locations"],
        )

    def test_sarif_import_inherits_rule_levels_and_preserves_metadata(self):
        sarif = {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "CodeQL",
                            "version": "2.18.0",
                            "rules": [
                                {
                                    "id": "driver/high-rule",
                                    "defaultConfiguration": {"level": "error"},
                                    "properties": {"security-severity": "9.1"},
                                }
                            ],
                        },
                        "extensions": [
                            {
                                "name": "codeql/python-queries",
                                "rules": [
                                    {
                                        "id": "ext/note-rule",
                                        "defaultConfiguration": {"level": "note"},
                                        "properties": {"security-severity": "3.5"},
                                    }
                                ],
                            }
                        ],
                    },
                    "logicalLocations": [
                        {"name": "IndexedHandler.handle", "kind": "function"}
                    ],
                    "results": [
                        {
                            "ruleId": "driver/high-rule",
                            "message": {"text": "driver error rule"},
                            "partialFingerprints": {
                                "primaryLocationLineHash": "abc123"
                            },
                            "fingerprints": {"matchBasedId/v1": "def456"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "source.py"},
                                        "region": {"startLine": 4, "endLine": 9},
                                    },
                                    "logicalLocations": [{"name": "Auth.verify"}],
                                }
                            ],
                        },
                        {
                            "ruleId": "ext/note-rule",
                            "rule": {
                                "id": "ext/note-rule",
                                "index": 0,
                                "toolComponent": {"index": 0},
                            },
                            "message": {"text": "extension note rule"},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": "source.py"},
                                        "region": {"startLine": 12, "endLine": 15},
                                    },
                                    "logicalLocations": [{"index": 0}],
                                }
                            ],
                        },
                        {
                            "ruleId": "unlisted/default-rule",
                            "message": {"text": "fallback warning rule"},
                            "locations": [],
                        },
                    ],
                }
            ],
        }
        atomic_json(self.root / "levels.sarif", sarif)
        result = runs.import_sarif(self.root / "levels.sarif")
        obs = result["observations"]
        self.assertEqual(3, len(obs))

        self.assertEqual("error", obs[0]["reported_level"])
        self.assertEqual("9.1", obs[0]["security_severity"])
        self.assertEqual(
            {"primaryLocationLineHash": "abc123"}, obs[0]["partial_fingerprints"]
        )
        self.assertEqual({"matchBasedId/v1": "def456"}, obs[0]["fingerprints"])
        self.assertEqual(
            [
                {
                    "path": "source.py",
                    "line": 4,
                    "end_line": 9,
                    "symbol": "Auth.verify",
                }
            ],
            obs[0]["locations"],
        )
        self.assertEqual(
            "scanner-reported; agent verification not performed",
            obs[0]["evidence_state"],
        )

        self.assertEqual("note", obs[1]["reported_level"])
        self.assertEqual("3.5", obs[1]["security_severity"])
        self.assertEqual(
            [
                {
                    "path": "source.py",
                    "line": 12,
                    "end_line": 15,
                    "symbol": "IndexedHandler.handle",
                }
            ],
            obs[1]["locations"],
        )

        self.assertEqual("warning", obs[2]["reported_level"])
        self.assertNotIn("security_severity", obs[2])
        self.assertNotIn("fingerprints", obs[2])
        self.assertNotIn("partial_fingerprints", obs[2])
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

    def test_empty_audit_report_omits_filter_controls(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["findings"] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            reports.render(audit, path)
            page = path.read_text()
            self.assertIn(
                "<p>No findings recorded. Review coverage before drawing conclusions.</p>",
                page,
            )
            self.assertNotIn('id="query"', page)
            self.assertNotIn('id="status"', page)
            self.assertNotIn('id="count"', page)
            self.assertNotIn('id="empty-state"', page)
            self.assertNotIn('id="reset-filters"', page)
            self.assertNotIn("Search findings", page)
            self.assertNotIn("Evidence status", page)
            self.assertNotIn('article class="finding"', page)

    def test_audit_report_with_findings_retains_filter_controls(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        self.assertTrue(len(audit["findings"]) > 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            reports.render(audit, path)
            page = path.read_text()
            self.assertIn('id="query"', page)
            self.assertIn('id="status"', page)
            self.assertIn('id="count"', page)
            self.assertIn('id="empty-state"', page)
            self.assertIn('id="reset-filters"', page)
            self.assertIn("Search findings", page)
            self.assertIn("Evidence status", page)
            self.assertIn('article class="finding"', page)
            self.assertNotIn(
                "<p>No findings recorded. Review coverage before drawing conclusions.</p>",
                page,
            )

    def test_render_output_path_resolution(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Case 1: existing directory
            existing_dir = tmp_path / "existing_dir"
            existing_dir.mkdir()
            res1 = reports.render(audit, existing_dir)
            self.assertEqual(existing_dir / "report.html", res1)
            self.assertTrue(res1.is_file())

            # Case 2: new directory without suffix
            new_dir = tmp_path / "new_dir"
            res2 = reports.render(audit, new_dir)
            self.assertEqual(new_dir / "report.html", res2)
            self.assertTrue(res2.is_file())

            # Case 3: file path with nested directories
            nested_file = tmp_path / "sub" / "deep" / "custom.html"
            res3 = reports.render(audit, nested_file)
            self.assertEqual(nested_file, res3)
            self.assertTrue(res3.is_file())

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

    def test_issue_drafts_json_format_matches_github_schema_and_preserves_metadata(
        self,
    ):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["issues"] = [
            {
                "id": "I-1",
                "title": "Race condition in cache",
                "finding_ids": ["F-1"],
                "depends_on": [],
                "related_issues": [],
                "existing_url": None,
                "publication": "draft",
                "labels": ["bug", "security"],
                "assignees": ["octocat"],
            },
            {
                "id": "I-2",
                "title": "Missing precondition check",
                "finding_ids": ["F-1"],
                "depends_on": ["I-1"],
                "related_issues": [],
                "existing_url": None,
                "publication": "draft",
            },
            {
                "id": "I-3",
                "title": "Already published issue",
                "finding_ids": ["F-1"],
                "depends_on": [],
                "related_issues": [],
                "existing_url": "https://github.com/example/repo/issues/42",
                "publication": "created",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "issues-json"
            paths = reports.issue_drafts(audit, output, format="json")
            self.assertEqual(2, len(paths))

            p1 = output / "I-1.json"
            self.assertTrue(p1.is_file())
            d1 = read_json(p1)
            self.assertEqual(d1["title"], "Race condition in cache")
            self.assertIn("F-1", d1["body"])
            self.assertIn("Contract and cause", d1["body"])
            self.assertIn("Failure sequence", d1["body"])
            self.assertEqual(d1["labels"], ["bug", "security"])
            self.assertEqual(d1["assignees"], ["octocat"])

            p2 = output / "I-2.json"
            self.assertTrue(p2.is_file())
            d2 = read_json(p2)
            self.assertEqual(d2["title"], "Missing precondition check")
            self.assertIn("F-1", d2["body"])
            self.assertEqual(d2["labels"], [])
            self.assertEqual(d2["assignees"], [])

            # Safety check: non-empty directory cannot be overwritten
            with self.assertRaises(ValueError):
                reports.issue_drafts(audit, output, format="json")

    def test_issue_drafts_unsupported_format_raises_error(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            reports.issue_drafts(audit, Path(tmp) / "out", format="yaml")

    def test_cli_issues_command_supports_format_flag(self):
        audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        audit["issues"] = [
            {
                "id": "I-test",
                "title": "CLI JSON export test",
                "finding_ids": ["F-1"],
                "depends_on": [],
                "related_issues": [],
                "existing_url": None,
                "publication": "draft",
                "labels": ["audit"],
                "assignees": ["tester"],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            atomic_json(audit_path, audit)
            json_out = Path(tmp) / "json-out"
            res = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/fred.py"),
                    "issues",
                    str(audit_path),
                    "--output",
                    str(json_out),
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("I-test.json", res.stdout)
            payload = read_json(json_out / "I-test.json")
            self.assertEqual(payload["title"], "CLI JSON export test")
            self.assertEqual(payload["labels"], ["audit"])
            self.assertEqual(payload["assignees"], ["tester"])

    def test_resolve_audit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_path = tmp_path / "custom_audit.json"
            file_path.write_text("{}", encoding="utf-8")
            self.assertEqual(file_path, resolve_audit_path(file_path))

            # Directory containing audit.json
            audit_file = tmp_path / "audit.json"
            audit_file.write_text("{}", encoding="utf-8")
            self.assertEqual(audit_file, resolve_audit_path(tmp_path))

            # Empty directory lacking audit.json
            empty_dir = tmp_path / "empty"
            empty_dir.mkdir()
            with self.assertRaises(ValueError) as ctx:
                resolve_audit_path(empty_dir)
            self.assertIn(
                "Audit ledger 'audit.json' not found in directory", str(ctx.exception)
            )


if __name__ == "__main__":
    unittest.main()

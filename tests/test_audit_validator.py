"""Artifact consistency tests; no agent or live repository is involved."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills/repo-audit-program"
SCRIPT = PACKAGE / "scripts/validate_audit.py"
spec = importlib.util.spec_from_file_location("audit_validator", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT}")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
SCHEMA = json.loads((PACKAGE / "assets/audit.schema.json").read_text())
BASE = json.loads((ROOT / "tests/fixtures/audit-valid.json").read_text())


class AuditValidatorTests(unittest.TestCase):
    def setUp(self):
        self.audit = copy.deepcopy(BASE)

    def errors(self):
        return validator.validate(self.audit, SCHEMA)

    def issue(self, identifier="I-1", dependencies=None):
        return {
            "id": identifier,
            "title": "Synthetic correction",
            "finding_ids": ["F-1"],
            "depends_on": dependencies or [],
            "related_issues": [],
            "existing_url": None,
            "publication": "draft",
        }

    def test_supported_finding_without_execution_is_valid(self):
        self.assertEqual([], self.errors())

    def test_missing_and_duplicate_evidence_are_rejected(self):
        self.audit["findings"][0]["evidence_ids"] = ["E-missing"]
        errors = self.errors()
        self.assertTrue(
            any(
                "Unknown evidence" in error
                and "F-1" in error
                and "evidence_ids" in error
                and "E-missing" in error
                for error in errors
            )
        )
        self.audit = copy.deepcopy(BASE)
        self.audit["evidence"].append(copy.deepcopy(self.audit["evidence"][0]))
        errors = self.errors()
        self.assertTrue(
            any("Duplicate IDs in evidence: E-1" in error for error in errors)
        )

    def test_invalid_source_locations_are_rejected(self):
        for path in ["/tmp/source.mjs", "../source.mjs"]:
            with self.subTest(path=path):
                self.audit = copy.deepcopy(BASE)
                self.audit["evidence"][0]["path"] = path
                self.assertTrue(
                    any("repository-relative" in error for error in self.errors())
                )
        self.audit = copy.deepcopy(BASE)
        self.audit["evidence"][0].update(line_start=5, line_end=2)
        self.assertTrue(any("Reversed lines" in error for error in self.errors()))

    def test_confirmed_requires_execution_and_command(self):
        finding = self.audit["findings"][0]
        finding["status"] = "confirmed"
        self.assertTrue(any("Confirmed finding" in error for error in self.errors()))
        finding["validation"].update(
            state="executed", commands=["node synthetic-check.mjs"]
        )
        self.assertEqual([], self.errors())

    def test_supported_requires_evidence_and_contract(self):
        for field in ["evidence_ids", "contract_ids"]:
            with self.subTest(field=field):
                self.audit = copy.deepcopy(BASE)
                self.audit["findings"][0][field] = []
                self.assertTrue(
                    any("lacks contract/evidence" in error for error in self.errors())
                )

    def test_refuted_candidate_cannot_be_a_defect_issue(self):
        self.audit["findings"][0].update(status="refuted", severity="info")
        self.audit["issues"] = [self.issue()]
        self.assertTrue(any("refuted finding" in error for error in self.errors()))

    def test_real_blockers_must_be_acyclic(self):
        self.audit["issues"] = [self.issue("I-1", ["I-2"]), self.issue("I-2", ["I-1"])]
        self.assertTrue(any("dependency cycle" in error for error in self.errors()))
        self.audit["issues"][1]["depends_on"] = []
        self.assertEqual([], self.errors())

    def test_missing_blocker_is_rejected(self):
        self.audit["issues"] = [self.issue(dependencies=["I-missing"])]
        self.assertTrue(any("Unknown issues" in error for error in self.errors()))

    def test_publication_requires_a_returned_url(self):
        self.audit["issues"] = [self.issue()]
        self.audit["issues"][0]["publication"] = "created"
        self.assertTrue(any("lacks verified URL" in error for error in self.errors()))

    def test_unknown_status_and_schema_version_are_rejected(self):
        self.audit["findings"][0]["status"] = "probably-fine"
        self.assertTrue(self.errors())
        self.audit = copy.deepcopy(BASE)
        self.audit["schema_version"] = "9"
        self.assertTrue(self.errors())
        self.audit = copy.deepcopy(BASE)
        del self.audit["schema_version"]
        errors = self.errors()
        self.assertTrue(
            any(
                error.startswith("root:")
                and "'schema_version' is a required property" in error
                for error in errors
            )
        )

    def test_empty_record_lists_are_valid(self):
        self.audit["evidence"] = []
        self.audit["contracts"] = []
        self.audit["findings"] = []
        self.audit["issues"] = []
        self.assertEqual([], self.errors())

    def test_issue_labels_and_assignees_schema_validation(self):
        issue = self.issue()
        issue["labels"] = ["security", "concurrency"]
        issue["assignees"] = ["auditor"]
        self.audit["issues"] = [issue]
        self.assertEqual([], self.errors())

        # Invalid label type (e.g. integer) is rejected by schema
        issue["labels"] = [123]
        self.assertTrue(any("issues/0/labels/0" in error for error in self.errors()))

        # Invalid assignee type is rejected by schema
        issue["labels"] = ["valid"]
        issue["assignees"] = [True]
        self.assertTrue(any("issues/0/assignees/0" in error for error in self.errors()))

    def test_cli_accepts_valid_fixture_and_rejects_malformed_json(self):
        fixture = ROOT / "tests/fixtures/audit-valid.json"
        valid = subprocess.run(
            [sys.executable, str(SCRIPT), str(fixture)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.json"
            bad.write_text("{")
            invalid = subprocess.run(
                [sys.executable, str(SCRIPT), str(bad)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, invalid.returncode)
        no_jsonschema = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys, runpy; sys.modules['jsonschema'] = None; "
                    "sys.argv = sys.argv[1:]; "
                    "runpy.run_path(sys.argv[0], run_name='__main__')"
                ),
                str(SCRIPT),
                str(fixture),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, no_jsonschema.returncode, no_jsonschema.stderr)
        self.assertIn("PASS:", no_jsonschema.stdout)

        no_site = subprocess.run(
            [sys.executable, "-S", str(SCRIPT), str(fixture)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, no_site.returncode, no_site.stderr)

    def test_validate_output_matches_draft202012_across_all_11_schemas_and_51_skills(
        self,
    ):
        import jsonschema

        canonical_bytes = (ROOT / "scripts/validate_output.py").read_bytes()
        skill_dirs = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
        self.assertEqual(51, len(skill_dirs))
        for skill_dir in skill_dirs:
            script_path = skill_dir / "scripts/validate_output.py"
            self.assertTrue(script_path.is_file(), f"Missing {script_path}")
            self.assertEqual(canonical_bytes, script_path.read_bytes())

        packs_doc = json.loads(
            (ROOT / "catalog/packs.json").read_text(encoding="utf-8")
        )
        self.assertEqual(11, len(packs_doc["packs"]))
        for pack_name, pack_info in packs_doc["packs"].items():
            for skill_name in pack_info["skills"]:
                self.assertTrue(
                    (
                        ROOT / "skills" / skill_name / "scripts/validate_output.py"
                    ).is_file(),
                    f"Pack {pack_name} skill {skill_name} lacks validate_output.py",
                )

        valid_audit = json.loads(
            (ROOT / "tests/fixtures/audit-valid.json").read_text(encoding="utf-8")
        )
        schema_cases = [
            (
                "repo-audit-concurrency",
                ROOT / "skills/repo-audit-concurrency/assets/audit.schema.json",
                valid_audit,
                [
                    {k: v for k, v in valid_audit.items() if k != "run_id"},
                    {**valid_audit, "extra_field": True},
                    {
                        **valid_audit,
                        "evidence": [{**valid_audit["evidence"][0], "line_start": 0}],
                    },
                    {
                        **valid_audit,
                        "evidence": [
                            {**valid_audit["evidence"][0], "line_start": True}
                        ],
                    },
                ],
            ),
            (
                "arch-decision-record",
                ROOT / "skills/arch-decision-record/assets/architecture.schema.json",
                {
                    "schema_version": "1.0",
                    "title": "Core Architecture",
                    "status": "accepted",
                    "boundaries": [
                        {
                            "name": "API",
                            "type": "trust",
                            "description": "Ingress boundary",
                            "invariants": ["Authenticated caller"],
                        }
                    ],
                    "decisions": [],
                },
                [
                    {
                        "schema_version": "2.0",
                        "title": "Core",
                        "status": "accepted",
                        "boundaries": [],
                        "decisions": [],
                    },
                    {
                        "schema_version": "1.0",
                        "title": "",
                        "status": "accepted",
                        "boundaries": [],
                        "decisions": [],
                    },
                    {
                        "schema_version": "1.0",
                        "title": "Core",
                        "status": "invalid",
                        "boundaries": [],
                        "decisions": [],
                    },
                ],
            ),
            (
                "review-diff-analysis",
                ROOT / "skills/review-diff-analysis/assets/review.schema.json",
                {
                    "schema_version": "1.0",
                    "verdict": "approve",
                    "summary": "Clean change",
                    "findings": [],
                },
                [
                    {
                        "schema_version": "1.0",
                        "verdict": "ship_it",
                        "summary": "ok",
                        "findings": [],
                    },
                    {
                        "schema_version": "1.0",
                        "verdict": "approve",
                        "summary": "ok",
                        "findings": [],
                        "unexpected": 1,
                    },
                ],
            ),
            (
                "test-case-generation",
                ROOT / "skills/test-case-generation/assets/testing.schema.json",
                {
                    "schema_version": "1.0",
                    "component": "billing",
                    "framework": "unittest",
                    "test_cases": [
                        {
                            "id": "TC-1",
                            "name": "test_zero",
                            "description": "Zero amount check",
                            "oracle_type": "state",
                            "verified": True,
                        }
                    ],
                },
                [
                    {
                        "schema_version": "1.0",
                        "component": "billing",
                        "framework": "unittest",
                        "test_cases": [
                            {
                                "id": "TC-1",
                                "name": "t",
                                "description": "d",
                                "oracle_type": "state",
                                "verified": 1,
                            }
                        ],
                    },
                ],
            ),
            (
                "repo-audit-diagnostics",
                ROOT / "skills/repo-audit-diagnostics/assets/bundle.schema.json",
                {
                    "schema_version": "1.0",
                    "session_id": "sess-1",
                    "timestamp": "2026-11-19T00:00:00Z",
                    "failure_classification": {
                        "failure_type": "tool_timeout",
                        "summary": "Command timed out",
                        "line_number": 3,
                    },
                    "correlated_domain": {
                        "specialist_skill": None,
                        "topic": "concurrency",
                        "ledger_references": [],
                    },
                    "environment": {
                        "platform": "Linux",
                        "python_version": "3.12.0",
                        "repository_path": None,
                        "run_id": None,
                    },
                    "recovery": {
                        "last_checkpoint": None,
                        "instructions": ["1. Retry"],
                    },
                    "redaction": {
                        "tier": "evidence",
                        "total_redactions": 0,
                        "credentials_redacted": 0,
                        "tool_outputs_omitted": 0,
                        "by_category": {"api_key": 0},
                    },
                    "scrubbed_transcript": "line 1",
                },
                [
                    {
                        "schema_version": "1.0",
                        "session_id": "sess-1",
                        "timestamp": "2026-11-19T00:00:00Z",
                        "failure_classification": {
                            "failure_type": "tool_timeout",
                            "summary": "Command timed out",
                            "line_number": 3,
                        },
                        "environment": {
                            "platform": "Linux",
                            "python_version": "3.12.0",
                        },
                        "recovery": {"last_checkpoint": None, "instructions": []},
                        "redaction": {
                            "tier": "evidence",
                            "total_redactions": 0,
                            "credentials_redacted": 0,
                            "tool_outputs_omitted": 0,
                            "by_category": {"api_key": -1},
                        },
                        "scrubbed_transcript": "line 1",
                    },
                    {
                        "schema_version": "1.0",
                        "session_id": "sess-1",
                        "timestamp": "2026-11-19T00:00:00Z",
                        "failure_classification": {
                            "failure_type": "tool_timeout",
                            "summary": "Command timed out",
                            "line_number": True,
                        },
                        "environment": {
                            "platform": "Linux",
                            "python_version": "3.12.0",
                        },
                        "recovery": {"last_checkpoint": None, "instructions": []},
                        "scrubbed_transcript": "line 1",
                    },
                ],
            ),
            (
                "repo-verify-finding",
                ROOT
                / "skills/repo-verify-finding/assets/finding-verification.schema.json",
                {
                    "schema_version": "1.0",
                    "finding_id": "F-1",
                    "reproduction_status": "confirmed",
                    "safe_control_outcomes": [
                        {"control": "safe input", "passed": True}
                    ],
                    "confidence_rating": "high",
                    "commands": ["python3 -m unittest"],
                    "result": "Reproduced",
                },
                [
                    {
                        "schema_version": "1.0",
                        "finding_id": "F-1",
                        "reproduction_status": "confirmed",
                        "safe_control_outcomes": [{"control": "", "passed": True}],
                        "confidence_rating": "high",
                    }
                ],
            ),
            (
                "repo-specify-regression",
                ROOT / "skills/repo-specify-regression/assets/regression.schema.json",
                {
                    "schema_version": "1.0",
                    "finding_id": "F-1",
                    "test_file": "tests/test_reg.py",
                    "failure_boundaries": ["stale write"],
                    "test_command_traces": [
                        {"command": "python3 -m unittest", "exit_code": 1}
                    ],
                    "assertion_expectations": ["AssertionError"],
                },
                [
                    {
                        "schema_version": "1.0",
                        "finding_id": "F-1",
                        "failure_boundaries": [],
                        "test_command_traces": ["python3 -m unittest"],
                        "assertion_expectations": ["AssertionError"],
                    },
                    {
                        "schema_version": "1.0",
                        "finding_id": "F-1",
                        "failure_boundaries": ["stale write"],
                        "test_command_traces": [
                            {"command": "python3 -m unittest", "exit_code": True}
                        ],
                        "assertion_expectations": ["AssertionError"],
                    },
                ],
            ),
            (
                "repo-plan-fix",
                ROOT / "skills/repo-plan-fix/assets/plan.schema.json",
                {
                    "schema_version": "1.0",
                    "task_breakdowns": [
                        {
                            "task_id": "T-1",
                            "title": "Add generation token check",
                            "file_scopes": ["session.py"],
                            "acceptance_criteria": ["Unit test passes"],
                        }
                    ],
                    "dependency_order": ["T-1"],
                    "file_scopes": ["session.py"],
                    "acceptance_criteria": ["Unit test passes"],
                },
                [
                    {
                        "schema_version": "1.0",
                        "task_breakdowns": [],
                        "dependency_order": [],
                        "file_scopes": ["session.py"],
                        "acceptance_criteria": ["Unit test passes"],
                    }
                ],
            ),
            (
                "repo-apply-fix",
                ROOT / "skills/repo-apply-fix/assets/fix.schema.json",
                {
                    "schema_version": "1.0",
                    "finding_id": "F-1",
                    "before_revision": "abc1234",
                    "after_revision": "def5678",
                    "file_changes": [
                        {
                            "path": "session.py",
                            "action": "modified",
                            "additions": 3,
                            "deletions": 1,
                        }
                    ],
                    "diff_summaries": ["Added generation token guard"],
                    "execution_results": [
                        {
                            "command": "python3 -m unittest",
                            "exit_code": 0,
                            "passed": True,
                        }
                    ],
                    "compatibility_limits": ["Sync callers unaffected"],
                },
                [
                    {
                        "schema_version": "1.0",
                        "file_changes": [{"path": "session.py", "additions": -1}],
                        "diff_summaries": ["Added guard"],
                        "execution_results": [
                            {
                                "command": "python3 -m unittest",
                                "exit_code": 0,
                                "passed": True,
                            }
                        ],
                        "compatibility_limits": [],
                    }
                ],
            ),
            (
                "repo-verify-fix",
                ROOT / "skills/repo-verify-fix/assets/verification.schema.json",
                {
                    "schema_version": "1.0",
                    "before_revision": "abc1234",
                    "after_revision": "def5678",
                    "original_reproduced": True,
                    "regression_passed": True,
                    "safe_controls_passed": True,
                    "commands": ["python3 -m unittest"],
                    "result": "All checks passed",
                },
                [
                    {
                        "schema_version": "1.0",
                        "before_revision": "abc1234",
                        "after_revision": "def5678",
                        "original_reproduced": True,
                        "regression_passed": True,
                        "safe_controls_passed": True,
                        "commands": [],
                        "result": "Empty commands rejected by minItems",
                    }
                ],
            ),
            (
                "repo-reconcile-findings",
                ROOT
                / "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
                {
                    "schema_version": "1.0",
                    "records": [
                        {
                            "at": "2026-11-19T00:00:00Z",
                            "finding_id": "F-1",
                            "fingerprint": "fp-1",
                            "state": "verified-fixed",
                            "note": "Verified by regression test",
                            "verification": {
                                "schema_version": "1.0",
                                "before_revision": "abc1234",
                                "after_revision": "def5678",
                                "original_reproduced": True,
                                "regression_passed": True,
                                "safe_controls_passed": True,
                                "commands": ["python3 -m unittest"],
                                "result": "Verified",
                            },
                        }
                    ],
                },
                [
                    {
                        "schema_version": "1.0",
                        "records": [
                            {
                                "at": "2026-11-19T00:00:00Z",
                                "finding_id": "F-1",
                                "fingerprint": "fp-1",
                                "state": "verified-fixed",
                                "note": "Missing verification proof for verified-fixed oneOf",
                                "verification": None,
                            }
                        ],
                    }
                ],
            ),
        ]

        distinct_schema_names = {
            schema_path.name for _, schema_path, _, _ in schema_cases
        }
        all_repo_schema_names = {
            p.name for p in (ROOT / "skills").glob("*/assets/*.schema.json")
        }
        self.assertEqual(11, len(distinct_schema_names))
        self.assertEqual(all_repo_schema_names, distinct_schema_names)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for (
                skill_name,
                schema_path,
                valid_payload,
                invalid_payloads,
            ) in schema_cases:
                schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
                ref_validator = jsonschema.Draft202012Validator(schema_doc)
                skill_validator = (
                    ROOT / "skills" / skill_name / "scripts/validate_output.py"
                )

                for idx, candidate in enumerate([valid_payload, *invalid_payloads]):
                    expected_valid = ref_validator.is_valid(candidate)
                    payload_file = tmp_dir / f"{skill_name}-{idx}.json"
                    payload_file.write_text(json.dumps(candidate), encoding="utf-8")
                    proc = subprocess.run(
                        [
                            sys.executable,
                            "-S",
                            str(skill_validator),
                            str(payload_file),
                            "--schema",
                            str(schema_path),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    actual_valid = proc.returncode == 0
                    self.assertEqual(
                        expected_valid,
                        actual_valid,
                        f"Mismatch on {schema_path.name} case {idx}: expected_valid={expected_valid}, stderr={proc.stderr}",
                    )
                    if idx == 0:
                        self.assertTrue(expected_valid)
                        default_proc = subprocess.run(
                            [
                                sys.executable,
                                "-S",
                                str(skill_validator),
                                str(payload_file),
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(
                            0, default_proc.returncode, default_proc.stderr
                        )
                    else:
                        self.assertFalse(expected_valid)

        self.assertEqual([], list((ROOT / "skills").rglob("__pycache__")))

    def test_standalone_generate_issues_script_works_under_python_no_site(self):
        script = ROOT / "skills/repo-audit-synthesis/scripts/generate_issues.py"
        audit = json.loads(
            (ROOT / "tests/fixtures/audit-valid.json").read_text(encoding="utf-8")
        )
        audit["issues"] = [
            {
                "id": "ISS-1",
                "title": "Fence stale session publication",
                "finding_ids": ["F-1"],
                "depends_on": [],
                "related_issues": [],
                "existing_url": None,
                "labels": ["bug", "concurrency"],
                "assignees": ["octocat"],
                "publication": "draft",
            },
            {
                "id": "ISS-2",
                "title": "Already created upstream",
                "finding_ids": ["F-1"],
                "depends_on": ["ISS-1"],
                "related_issues": [],
                "existing_url": "https://github.com/example/repo/issues/2",
                "labels": [],
                "assignees": [],
                "publication": "created",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            isolated = Path(tmp)
            audit_file = isolated / "audit.json"
            audit_file.write_text(json.dumps(audit), encoding="utf-8")
            json_out = isolated / "drafts-json"
            md_out = isolated / "drafts-md"

            proc_json = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(script),
                    str(audit_file),
                    "--output",
                    str(json_out),
                    "--format",
                    "json",
                ],
                cwd=isolated,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc_json.returncode, proc_json.stderr)
            summary = json.loads(proc_json.stdout)
            self.assertEqual(1, len(summary["drafts"]))
            draft_payload = json.loads(
                (json_out / "ISS-1.json").read_text(encoding="utf-8")
            )
            self.assertEqual("Fence stale session publication", draft_payload["title"])
            self.assertEqual(["bug", "concurrency"], draft_payload["labels"])
            self.assertEqual(["octocat"], draft_payload["assignees"])
            self.assertIn("## F-1:", draft_payload["body"])

            # Non-empty directory is rejected
            proc_non_empty = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(script),
                    str(audit_file),
                    "--output",
                    str(json_out),
                    "--format",
                    "json",
                ],
                cwd=isolated,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, proc_non_empty.returncode)

            proc_md = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(script),
                    str(audit_file),
                    "--output",
                    str(md_out),
                    "--format",
                    "markdown",
                ],
                cwd=isolated,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc_md.returncode, proc_md.stderr)
            self.assertTrue((md_out / "ISS-1.md").is_file())


if __name__ == "__main__":
    unittest.main()

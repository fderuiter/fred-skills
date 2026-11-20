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
            [sys.executable, "-B", "-S", str(SCRIPT), str(fixture)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, "-B", "-S", str(SCRIPT), str(bad)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, invalid.returncode)
            bad_schema_audit = Path(temp) / "invalid-audit.json"
            bad_schema_audit.write_text(
                json.dumps({"schema_version": "1.0"}), encoding="utf-8"
            )
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    (
                        "import sys, runpy; sys.modules['jsonschema'] = None; "
                        "sys.argv = sys.argv[1:]; "
                        "runpy.run_path(sys.argv[0], run_name='__main__')"
                    ),
                    str(SCRIPT),
                    str(bad_schema_audit),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, rejected.returncode)
            self.assertNotIn("requirements-dev.txt", rejected.stderr)
        no_jsonschema_valid = subprocess.run(
            [
                sys.executable,
                "-B",
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
        self.assertEqual(0, no_jsonschema_valid.returncode, no_jsonschema_valid.stderr)
        self.assertNotIn("requirements-dev.txt", no_jsonschema_valid.stderr)

    def test_diagnostics_validator_auto_selects_bundle_or_audit_schema(self):
        if str(ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "scripts"))
        from _markdown_utils import extract_fenced_blocks

        diag_pkg = ROOT / "skills/repo-audit-diagnostics"
        diag_script = diag_pkg / "scripts/validate_audit.py"
        examples_text = (diag_pkg / "references/examples.md").read_text(
            encoding="utf-8"
        )
        fenced, _ = extract_fenced_blocks(examples_text)
        blocks = [json.loads(body) for lang, body in fenced if lang == "json"]
        self.assertGreaterEqual(len(blocks), 2)
        bundle_blocks = [b for b in blocks if "session_id" in b]
        audit_blocks = [b for b in blocks if "run_id" in b]
        self.assertTrue(bundle_blocks)
        self.assertTrue(audit_blocks)

        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            for idx, payload in enumerate([*bundle_blocks, *audit_blocks]):
                artifact = tmp / f"example-{idx}.json"
                artifact.write_text(json.dumps(payload), encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, "-B", "-S", str(diag_script), str(artifact)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, proc.returncode, proc.stderr)

            bad_bundle = copy.deepcopy(bundle_blocks[0])
            bad_bundle["redaction"]["by_category"] = {"bearer_token": -1}
            bad_bundle_path = tmp / "bad-bundle.json"
            bad_bundle_path.write_text(json.dumps(bad_bundle), encoding="utf-8")
            bad_bundle_proc = subprocess.run(
                [sys.executable, "-B", "-S", str(diag_script), str(bad_bundle_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, bad_bundle_proc.returncode)

            bad_audit = copy.deepcopy(audit_blocks[0])
            bad_audit["evidence"][0]["path"] = ".git/config"
            bad_audit_path = tmp / "bad-audit.json"
            bad_audit_path.write_text(json.dumps(bad_audit), encoding="utf-8")
            bad_audit_proc = subprocess.run(
                [sys.executable, "-B", "-S", str(diag_script), str(bad_audit_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, bad_audit_proc.returncode)


if __name__ == "__main__":
    unittest.main()

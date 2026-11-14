"""Artifact consistency tests; no agent or live repository is involved."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills/repo-audit-program"
SCRIPT = PACKAGE / "scripts/validate_audit.py"
spec = importlib.util.spec_from_file_location("audit_validator", SCRIPT)
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
        return {"id": identifier, "title": "Synthetic correction", "finding_ids": ["F-1"],
                "depends_on": dependencies or [], "related_issues": [],
                "existing_url": None, "publication": "draft"}

    def test_supported_finding_without_execution_is_valid(self):
        self.assertEqual([], self.errors())

    def test_missing_and_duplicate_evidence_are_rejected(self):
        self.audit["findings"][0]["evidence_ids"] = ["E-missing"]
        errors = self.errors()
        self.assertTrue(any("Unknown evidence" in error and "F-1" in error and "evidence_ids" in error and "E-missing" in error for error in errors))
        self.audit = copy.deepcopy(BASE)
        self.audit["evidence"].append(copy.deepcopy(self.audit["evidence"][0]))
        errors = self.errors()
        self.assertTrue(any("Duplicate IDs in evidence: E-1" in error for error in errors))

    def test_invalid_source_locations_are_rejected(self):
        for path in ["/tmp/source.mjs", "../source.mjs"]:
            with self.subTest(path=path):
                self.audit = copy.deepcopy(BASE)
                self.audit["evidence"][0]["path"] = path
                self.assertTrue(any("repository-relative" in error for error in self.errors()))
        self.audit = copy.deepcopy(BASE)
        self.audit["evidence"][0].update(line_start=5, line_end=2)
        self.assertTrue(any("Reversed lines" in error for error in self.errors()))

    def test_confirmed_requires_execution_and_command(self):
        finding = self.audit["findings"][0]
        finding["status"] = "confirmed"
        self.assertTrue(any("Confirmed finding" in error for error in self.errors()))
        finding["validation"].update(state="executed", commands=["node synthetic-check.mjs"])
        self.assertEqual([], self.errors())

    def test_supported_requires_evidence_and_contract(self):
        for field in ["evidence_ids", "contract_ids"]:
            with self.subTest(field=field):
                self.audit = copy.deepcopy(BASE)
                self.audit["findings"][0][field] = []
                self.assertTrue(any("lacks contract/evidence" in error for error in self.errors()))

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
        self.assertTrue(any(error.startswith("root:") and "'schema_version' is a required property" in error for error in errors))

    def test_empty_record_lists_are_valid(self):
        self.audit["evidence"] = []
        self.audit["contracts"] = []
        self.audit["findings"] = []
        self.audit["issues"] = []
        self.assertEqual([], self.errors())

    def test_cli_accepts_valid_fixture_and_rejects_malformed_json(self):
        fixture = ROOT / "tests/fixtures/audit-valid.json"
        valid = subprocess.run([sys.executable, str(SCRIPT), str(fixture)], capture_output=True, text=True)
        self.assertEqual(0, valid.returncode, valid.stderr)
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.json"
            bad.write_text("{")
            invalid = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
            self.assertEqual(2, invalid.returncode)
        missing_dep = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys, runpy; sys.modules['jsonschema'] = None; "
                    "script, fixture = sys.argv[1], sys.argv[2]; "
                    "sys.argv = [script, fixture]; "
                    "runpy.run_path(script, run_name='__main__')"
                ),
                str(SCRIPT),
                str(fixture),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, missing_dep.returncode)
        self.assertIn("Dependency missing: jsonschema", missing_dep.stderr)
        self.assertIn("pip install", missing_dep.stderr)


if __name__ == "__main__":
    unittest.main()

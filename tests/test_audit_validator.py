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
        self.assertEqual(0, no_jsonschema.returncode, no_jsonschema.stderr)

        isolated = subprocess.run(
            [sys.executable, "-B", "-S", str(SCRIPT), str(fixture)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, isolated.returncode, isolated.stderr)

    def test_diagnostics_validator_auto_selects_bundle_and_audit_schemas(self):
        diag_pkg = ROOT / "skills/repo-audit-diagnostics"
        diag_script = diag_pkg / "scripts/validate_audit.py"
        examples_md = (diag_pkg / "references/examples.md").read_text(encoding="utf-8")
        json_blocks: list[str] = []
        in_json = False
        buf: list[str] = []
        for line in examples_md.splitlines():
            if not in_json and line.strip() == "```json":
                in_json = True
                buf = []
            elif in_json and line.strip() == "```":
                in_json = False
                json_blocks.append("\n".join(buf))
            elif in_json:
                buf.append(line)
        self.assertEqual(2, len(json_blocks))

        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            bundle_json = temp_dir / "bundle.json"
            audit_json = temp_dir / "audit.json"
            bundle_json.write_text(json_blocks[0], encoding="utf-8")
            audit_json.write_text(json_blocks[1], encoding="utf-8")

            for target in (bundle_json, audit_json):
                proc = subprocess.run(
                    [sys.executable, "-B", "-S", str(diag_script), str(target)],
                    cwd=str(diag_pkg),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, proc.returncode, f"{target.name}: {proc.stderr}")

            bad_bundle = json.loads(json_blocks[0])
            del bad_bundle["session_id"]
            bad_bundle_path = temp_dir / "bad-bundle.json"
            bad_bundle_path.write_text(json.dumps(bad_bundle), encoding="utf-8")
            bad_bundle_proc = subprocess.run(
                [sys.executable, "-B", "-S", str(diag_script), str(bad_bundle_path)],
                cwd=str(diag_pkg),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, bad_bundle_proc.returncode)

            override_proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-S",
                    str(diag_script),
                    str(bundle_json),
                    "--schema",
                    str(diag_pkg / "assets/audit.schema.json"),
                ],
                cwd=str(diag_pkg),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, override_proc.returncode)


if __name__ == "__main__":
    unittest.main()

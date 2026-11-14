"""Automated tests for audit to SARIF v2.1.0 converter."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/audit_to_sarif.py"
SKILL_SCRIPT_PATH = ROOT / "skills/repo-audit-synthesis/scripts/audit_to_sarif.py"
FIXTURE_PATH = ROOT / "tests/fixtures/audit-valid.json"
SARIF_SCHEMA_PATH = ROOT / "tests/fixtures/sarif-schema-2.1.0.json"

spec = importlib.util.spec_from_file_location("audit_to_sarif", SCRIPT_PATH)
audit_to_sarif_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_to_sarif_mod)
audit_to_sarif = audit_to_sarif_mod.audit_to_sarif
convert_file = audit_to_sarif_mod.convert_file


class AuditToSarifTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.base_audit = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_converts_valid_audit_fixture_to_valid_sarif(self):
        sarif = audit_to_sarif(self.base_audit)
        self.assertEqual("2.1.0", sarif.get("version"))
        self.assertIn("runs", sarif)
        self.assertEqual(1, len(sarif["runs"]))

        run = sarif["runs"][0]
        self.assertEqual("fred-skills", run["tool"]["driver"]["name"])
        self.assertEqual(1, len(run["results"]))
        self.assertEqual(1, len(run["tool"]["driver"]["rules"]))

        result = run["results"][0]
        self.assertEqual("F-1", result["ruleId"])
        self.assertEqual(0, result["ruleIndex"])
        self.assertEqual("warning", result["level"])
        self.assertIn("Old result publishes", result["message"]["text"])
        self.assertIn("Fence publication", result["message"]["text"])

        # Validate against SARIF v2.1.0 schema if jsonschema and schema fixture are available
        if SARIF_SCHEMA_PATH.exists():
            try:
                import jsonschema

                schema = json.loads(SARIF_SCHEMA_PATH.read_text(encoding="utf-8"))
                validator = jsonschema.Draft202012Validator(schema)
                errors = list(validator.iter_errors(sarif))
                self.assertEqual([], errors, [e.message for e in errors])
            except ImportError:
                pass

    def test_severity_alert_level_mapping(self):
        """Criterion 4: Automated tests verify conversion accuracy across critical, high, medium, and low severity findings."""
        cases = [
            ("critical", "error"),
            ("high", "error"),
            ("medium", "warning"),
            ("low", "note"),
            ("info", "note"),
        ]
        audit = copy.deepcopy(self.base_audit)
        audit["findings"] = []
        for i, (sev, expected_level) in enumerate(cases):
            f = copy.deepcopy(self.base_audit["findings"][0])
            f["id"] = f"F-{i + 1}"
            f["severity"] = sev
            audit["findings"].append(f)

        sarif = audit_to_sarif(audit)
        run = sarif["runs"][0]
        self.assertEqual(len(cases), len(run["results"]))

        for i, (sev, expected_level) in enumerate(cases):
            result = run["results"][i]
            rule = run["tool"]["driver"]["rules"][i]
            self.assertEqual(
                expected_level,
                result["level"],
                f"Finding severity {sev} should map to SARIF level {expected_level}",
            )
            self.assertEqual(
                expected_level,
                rule["defaultConfiguration"]["level"],
                f"Rule default configuration for {sev} should be {expected_level}",
            )
            self.assertEqual(sev, rule["properties"]["severity"])
            self.assertEqual(sev, result["properties"]["severity"])

    def test_evidence_mapping_to_physical_locations_and_regions(self):
        """Criterion 2: Each finding with valid evidence maps to a result containing URI and line number information."""
        audit = copy.deepcopy(self.base_audit)
        audit["evidence"] = [
            {
                "id": "E-1",
                "path": "src/core/auth.py",
                "line_start": 42,
                "line_end": 55,
                "symbol": "authenticate_user",
                "context": "Token signature verification skipped",
            },
            {
                "id": "E-2",
                "path": "config/settings.yaml",
                "line_start": 10,
                "line_end": 10,
                "symbol": "debug_mode",
                "context": "Debug enabled in production",
            },
            {
                "id": "E-3",
                "path": r"\src\auth\provider.py",
                "line_start": 1,
                "line_end": 5,
                "symbol": "auth_provider",
                "context": "Windows path normalization",
            },
        ]
        audit["findings"][0]["evidence_ids"] = ["E-1", "E-2", "E-3"]

        sarif = audit_to_sarif(audit)
        result = sarif["runs"][0]["results"][0]
        self.assertEqual(3, len(result["locations"]))

        loc1 = result["locations"][0]
        self.assertEqual(
            "src/core/auth.py", loc1["physicalLocation"]["artifactLocation"]["uri"]
        )
        self.assertEqual(
            "%SRCROOT%", loc1["physicalLocation"]["artifactLocation"]["uriBaseId"]
        )
        self.assertEqual(42, loc1["physicalLocation"]["region"]["startLine"])
        self.assertEqual(55, loc1["physicalLocation"]["region"]["endLine"])
        self.assertEqual([{"name": "authenticate_user"}], loc1["logicalLocations"])
        self.assertEqual(
            {"text": "Token signature verification skipped"}, loc1["message"]
        )

        loc2 = result["locations"][1]
        self.assertEqual(
            "config/settings.yaml", loc2["physicalLocation"]["artifactLocation"]["uri"]
        )
        self.assertEqual(10, loc2["physicalLocation"]["region"]["startLine"])
        self.assertEqual(10, loc2["physicalLocation"]["region"]["endLine"])

        loc3 = result["locations"][2]
        self.assertEqual(
            "src/auth/provider.py", loc3["physicalLocation"]["artifactLocation"]["uri"]
        )
        self.assertEqual(1, loc3["physicalLocation"]["region"]["startLine"])
        self.assertEqual(5, loc3["physicalLocation"]["region"]["endLine"])

    def test_single_line_and_reversed_line_evidence_normalization(self):
        audit = copy.deepcopy(self.base_audit)
        audit["evidence"] = [
            {
                "id": "E-single",
                "path": "app.py",
                "line_start": 15,
                "line_end": 15,
                "symbol": "main",
                "context": "Single line",
            },
            {
                "id": "E-reversed",
                "path": "util.py",
                "line_start": 20,
                "line_end": 10,
                "symbol": "helper",
                "context": "Reversed line range",
            },
        ]
        audit["findings"][0]["evidence_ids"] = ["E-single", "E-reversed"]

        sarif = audit_to_sarif(audit)
        locations = sarif["runs"][0]["results"][0]["locations"]
        self.assertEqual(15, locations[0]["physicalLocation"]["region"]["startLine"])
        self.assertEqual(15, locations[0]["physicalLocation"]["region"]["endLine"])
        # Reversed line_end < line_start should be normalized to startLine
        self.assertEqual(20, locations[1]["physicalLocation"]["region"]["startLine"])
        self.assertEqual(20, locations[1]["physicalLocation"]["region"]["endLine"])

    def test_finding_without_evidence_has_empty_locations(self):
        audit = copy.deepcopy(self.base_audit)
        audit["findings"][0]["evidence_ids"] = []
        sarif = audit_to_sarif(audit)
        result = sarif["runs"][0]["results"][0]
        self.assertEqual([], result["locations"])

    def test_rule_metadata_includes_identifiers_titles_topics_and_remedies(self):
        """Requirement 4: Rule metadata such as finding identifiers, titles, topics, and remedies."""
        sarif = audit_to_sarif(self.base_audit)
        rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]

        self.assertEqual("F-1", rule["id"])
        self.assertEqual("concurrency", rule["name"])
        self.assertEqual("Old result publishes", rule["shortDescription"]["text"])
        self.assertIn("Fence publication", rule["help"]["text"])
        self.assertIn("Fence publication", rule["help"]["markdown"])
        self.assertIn("concurrency", rule["help"]["text"])
        self.assertEqual("F-1", rule["properties"]["id"])
        self.assertEqual("concurrency", rule["properties"]["topic"])
        self.assertEqual("Fence publication", rule["properties"]["remedy"])
        self.assertIn("cancellation", rule["properties"]["tags"])
        self.assertIn("concurrency", rule["properties"]["tags"])

    def test_filter_refuted_flag(self):
        """Requirement 5: Filter out refuted findings."""
        audit = copy.deepcopy(self.base_audit)
        f_refuted = copy.deepcopy(audit["findings"][0])
        f_refuted["id"] = "F-refuted"
        f_refuted["status"] = "refuted"
        f_refuted["severity"] = "info"
        audit["findings"].append(f_refuted)

        # Without filtering: both findings included
        sarif_unfiltered = audit_to_sarif(audit, filter_refuted=False)
        self.assertEqual(2, len(sarif_unfiltered["runs"][0]["results"]))

        # With filtering: refuted finding excluded
        sarif_filtered = audit_to_sarif(audit, filter_refuted=True)
        results = sarif_filtered["runs"][0]["results"]
        self.assertEqual(1, len(results))
        self.assertEqual("F-1", results[0]["ruleId"])

    def test_filter_info_flag(self):
        """Requirement 5: Filter out info findings."""
        audit = copy.deepcopy(self.base_audit)
        f_info = copy.deepcopy(audit["findings"][0])
        f_info["id"] = "F-info"
        f_info["severity"] = "info"
        f_info["status"] = "supported"
        audit["findings"].append(f_info)

        # Without filtering
        sarif_unfiltered = audit_to_sarif(audit, filter_info=False)
        self.assertEqual(2, len(sarif_unfiltered["runs"][0]["results"]))

        # With filtering
        sarif_filtered = audit_to_sarif(audit, filter_info=True)
        results = sarif_filtered["runs"][0]["results"]
        self.assertEqual(1, len(results))
        self.assertEqual("F-1", results[0]["ruleId"])

    def test_filter_refuted_or_info_flag(self):
        audit = copy.deepcopy(self.base_audit)
        f_refuted = copy.deepcopy(audit["findings"][0])
        f_refuted["id"] = "F-refuted"
        f_refuted["status"] = "refuted"
        f_refuted["severity"] = "low"

        f_info = copy.deepcopy(audit["findings"][0])
        f_info["id"] = "F-info"
        f_info["severity"] = "info"
        f_info["status"] = "supported"

        audit["findings"].extend([f_refuted, f_info])

        sarif = audit_to_sarif(audit, filter_refuted=True, filter_info=True)
        results = sarif["runs"][0]["results"]
        self.assertEqual(1, len(results))
        self.assertEqual("F-1", results[0]["ruleId"])

    def test_input_file_is_not_modified(self):
        """Constraint: The script must not modify the input audit.json file."""
        input_path = self.temp_path / "audit.json"
        content = json.dumps(self.base_audit, indent=2)
        input_path.write_text(content, encoding="utf-8")
        before_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()

        output_path = self.temp_path / "audit.sarif"
        convert_file(input_path, output_path)

        after_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
        self.assertEqual(before_hash, after_hash)

    def test_cli_execution_with_file_output(self):
        """Criterion 3: Exit code zero on success."""
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "output.sarif"

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        str(input_path),
                        "-o",
                        str(output_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertTrue(output_path.is_file())
                data = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual("2.1.0", data["version"])
                output_path.unlink()

    def test_cli_execution_with_positional_output(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "positional.sarif"

        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(input_path), str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(output_path.is_file())
        data = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual("2.1.0", data["version"])

    def test_cli_execution_stdout_redirection(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(input_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual("2.1.0", data["version"])

    def test_cli_error_on_missing_input_file(self):
        """Criterion 3: Exit code non-zero with clear errors on invalid input."""
        missing = self.temp_path / "does_not_exist.json"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(missing)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode)
        self.assertIn("Cannot read input file", proc.stderr)

    def test_cli_error_on_malformed_json(self):
        """Criterion 3: Exit code non-zero with clear errors on malformed JSON."""
        bad_json = self.temp_path / "bad.json"
        bad_json.write_text("{ not json }", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(bad_json)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode)
        self.assertIn("Invalid JSON", proc.stderr)

    def test_cli_error_on_non_object_json(self):
        array_json = self.temp_path / "array.json"
        array_json.write_text("[1, 2, 3]", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(array_json)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, proc.returncode)
        self.assertIn("must contain a JSON object", proc.stderr)

    def test_cli_filter_flags(self):
        audit = copy.deepcopy(self.base_audit)
        audit["findings"].append(
            {
                "id": "F-refuted-cli",
                "topic": "cache",
                "title": "Refuted claim",
                "status": "refuted",
                "severity": "info",
                "confidence": "low",
                "contract_ids": [],
                "evidence_ids": [],
                "preconditions": "none",
                "sequence": ["check"],
                "expected": "ok",
                "actual": "ok",
                "impact": "none",
                "root_cause": "none",
                "counterevidence": "Refuted by test",
                "remedy": "None needed",
                "acceptance": ["pass"],
                "validation": {"state": "not-run", "commands": [], "result": "none"},
                "related_findings": [],
            }
        )
        input_path = self.temp_path / "audit_with_refuted.json"
        input_path.write_text(json.dumps(audit), encoding="utf-8")

        # Test --filter-refuted
        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(input_path), "--filter-refuted"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        results = data["runs"][0]["results"]
        self.assertEqual(1, len(results))
        self.assertEqual("F-1", results[0]["ruleId"])

        # Test --filter-refuted-or-info
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                str(input_path),
                "--filter-refuted-or-info",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(1, len(data["runs"][0]["results"]))

    def test_fred_cli_export_sarif_command(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "fred_exported.sarif"

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/fred.py"),
                "export-sarif",
                str(input_path),
                "--output",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(output_path.is_file())
        data = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual("2.1.0", data["version"])
        self.assertEqual(1, len(data["runs"][0]["results"]))

    def test_large_audit_performance(self):
        """Metric 3: The conversion script converts complex audit ledgers in less than two seconds."""
        audit = copy.deepcopy(self.base_audit)
        audit["findings"] = []
        audit["evidence"] = []

        # Create 500 evidence items and 500 findings across multiple severities
        severities = ["critical", "high", "medium", "low", "info"]
        for i in range(500):
            ev_id = f"E-{i + 1}"
            audit["evidence"].append(
                {
                    "id": ev_id,
                    "path": f"src/module_{i % 20}/file_{i}.py",
                    "line_start": (i * 10) + 1,
                    "line_end": (i * 10) + 5,
                    "symbol": f"func_{i}",
                    "context": f"Audit context {i}",
                }
            )
            audit["findings"].append(
                {
                    "id": f"F-{i + 1}",
                    "topic": f"topic_{i % 10}",
                    "tags": [f"tag_{i % 5}"],
                    "title": f"Finding title {i}",
                    "status": "supported",
                    "severity": severities[i % len(severities)],
                    "confidence": "high",
                    "contract_ids": ["C-1"],
                    "evidence_ids": [ev_id],
                    "preconditions": f"Precondition {i}",
                    "sequence": [f"Step {i}"],
                    "expected": f"Expected behavior {i}",
                    "actual": f"Actual behavior {i}",
                    "impact": f"Impact description {i}",
                    "root_cause": f"Root cause description {i}",
                    "counterevidence": f"Counterevidence {i}",
                    "remedy": f"Fix instructions {i}",
                    "acceptance": [f"Acceptance test {i}"],
                    "validation": {
                        "state": "not-run",
                        "commands": [],
                        "result": "none",
                    },
                    "related_findings": [],
                }
            )

        start = time.perf_counter()
        sarif = audit_to_sarif(audit)
        duration = time.perf_counter() - start

        self.assertLess(
            duration, 2.0, f"Conversion took {duration:.3f}s, expected < 2.0s"
        )
        self.assertEqual(500, len(sarif["runs"][0]["results"]))
        self.assertEqual(500, len(sarif["runs"][0]["tool"]["driver"]["rules"]))


if __name__ == "__main__":
    unittest.main()

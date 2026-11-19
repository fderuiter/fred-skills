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
sys.path.insert(0, str(ROOT / "scripts"))
from fredlib import runs as fred_runs
from sync_audit_resources import _safe_relative_path, sync

SCRIPT_PATH = ROOT / "scripts/audit_to_sarif.py"
SKILL_SCRIPT_PATH = ROOT / "skills/repo-audit-synthesis/scripts/audit_to_sarif.py"
FIXTURE_PATH = ROOT / "tests/fixtures/audit-valid.json"
SARIF_SCHEMA_PATH = ROOT / "tests/fixtures/sarif-schema-2.1.0.json"

spec = importlib.util.spec_from_file_location("audit_to_sarif", SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT_PATH}")
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
        expected_fp = fred_runs.fingerprint(
            self.base_audit["findings"][0], self.base_audit
        )
        expected_rule_id = f"fred/concurrency/{expected_fp[:12]}"
        self.assertEqual(expected_rule_id, result["ruleId"])
        self.assertEqual("F-1", result["properties"]["findingId"])
        self.assertEqual(0, result["ruleIndex"])
        self.assertEqual("warning", result["level"])
        self.assertEqual({"fred-skills/v1": expected_fp}, result["partialFingerprints"])
        self.assertEqual({"fred-skills/v1": expected_fp}, result["fingerprints"])
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
        for i, (sev, _expected_level) in enumerate(cases):
            f = copy.deepcopy(self.base_audit["findings"][0])
            f["id"] = f"F-{i + 1}"
            f["severity"] = sev
            f["root_cause"] = f"No publication fence ({sev})"
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
        expected_fp = fred_runs.fingerprint(
            self.base_audit["findings"][0], self.base_audit
        )
        expected_rule_id = f"fred/concurrency/{expected_fp[:12]}"

        self.assertEqual(expected_rule_id, rule["id"])
        self.assertEqual("concurrency", rule["name"])
        self.assertEqual("Old result publishes", rule["shortDescription"]["text"])
        self.assertIn("Fence publication", rule["help"]["text"])
        self.assertIn("Fence publication", rule["help"]["markdown"])
        self.assertIn("concurrency", rule["help"]["text"])
        self.assertEqual(expected_rule_id, rule["properties"]["id"])
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
        self.assertEqual("F-1", results[0]["properties"]["findingId"])
        self.assertTrue(results[0]["ruleId"].startswith("fred/concurrency/"))

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
        self.assertEqual("F-1", results[0]["properties"]["findingId"])
        self.assertTrue(results[0]["ruleId"].startswith("fred/concurrency/"))

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
        self.assertEqual("F-1", results[0]["properties"]["findingId"])
        self.assertTrue(results[0]["ruleId"].startswith("fred/concurrency/"))

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
        self.assertEqual(
            SCRIPT_PATH.read_bytes(),
            SKILL_SCRIPT_PATH.read_bytes(),
            "Canonical script and skill script copy must have identical contents",
        )
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

    def test_convert_file_with_directory(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "from_dir.sarif"

        data = convert_file(self.temp_path, output_path)
        self.assertEqual("2.1.0", data["version"])
        self.assertTrue(output_path.is_file())

    def test_convert_file_with_directory_missing_audit_raises_error(self):
        empty_dir = self.temp_path / "empty_dir"
        empty_dir.mkdir()
        with self.assertRaises(ValueError) as ctx:
            convert_file(empty_dir)
        self.assertIn(
            "Audit ledger 'audit.json' not found in directory", str(ctx.exception)
        )

    def test_cli_execution_with_directory_input(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "dir_output.sarif"

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        str(self.temp_path),
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

    def test_cli_execution_omitting_audit_defaults_to_cwd(self):
        input_path = self.temp_path / "audit.json"
        input_path.write_text(json.dumps(self.base_audit), encoding="utf-8")
        output_path = self.temp_path / "cwd_output.sarif"

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "-o",
                        str(output_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    cwd=str(self.temp_path),
                )
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertTrue(output_path.is_file())
                data = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual("2.1.0", data["version"])
                output_path.unlink()

    def test_cli_error_on_directory_missing_audit(self):
        empty_dir = self.temp_path / "empty_dir"
        empty_dir.mkdir()

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [sys.executable, str(script), str(empty_dir)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(2, proc.returncode)
                self.assertIn(
                    "Audit ledger 'audit.json' not found in directory", proc.stderr
                )

    def test_cli_error_omitting_audit_outside_audit_dir(self):
        empty_dir = self.temp_path / "empty_dir"
        empty_dir.mkdir()

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            with self.subTest(script=script.name):
                proc = subprocess.run(
                    [sys.executable, str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    cwd=str(empty_dir),
                )
                self.assertEqual(2, proc.returncode)
                self.assertIn(
                    "Audit ledger 'audit.json' not found in directory", proc.stderr
                )

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
        self.assertEqual("F-1", results[0]["properties"]["findingId"])
        self.assertTrue(results[0]["ruleId"].startswith("fred/concurrency/"))

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

    def test_id_shift_from_f1_to_f2_preserves_rule_id_and_fingerprints(self):
        sarif_before = audit_to_sarif(self.base_audit)
        res_before = sarif_before["runs"][0]["results"][0]
        expected_fp = fred_runs.fingerprint(
            self.base_audit["findings"][0], self.base_audit
        )

        shifted_audit = copy.deepcopy(self.base_audit)
        shifted_finding = copy.deepcopy(shifted_audit["findings"][0])
        shifted_finding["id"] = "F-2"
        shifted_finding["evidence_ids"] = ["E-9"]
        shifted_audit["evidence"][0]["id"] = "E-9"
        shifted_audit["evidence"][0]["line_start"] = 80
        shifted_audit["evidence"][0]["line_end"] = 92

        new_first = copy.deepcopy(self.base_audit["findings"][0])
        new_first["id"] = "F-1"
        new_first["topic"] = "security"
        new_first["title"] = "Unvalidated redirect target"
        new_first["root_cause"] = "Missing allowlist check on redirect URL"
        new_first["evidence_ids"] = ["E-new"]
        shifted_audit["evidence"].insert(
            0,
            {
                "id": "E-new",
                "path": "router.mjs",
                "line_start": 10,
                "line_end": 12,
                "symbol": "Router.redirect",
                "context": "Redirect handler",
            },
        )
        shifted_audit["findings"] = [new_first, shifted_finding]

        sarif_after = audit_to_sarif(shifted_audit)
        res_after = sarif_after["runs"][0]["results"][1]

        self.assertEqual(res_before["ruleId"], res_after["ruleId"])
        self.assertEqual(
            res_before["partialFingerprints"], res_after["partialFingerprints"]
        )
        self.assertEqual(res_before["fingerprints"], res_after["fingerprints"])
        self.assertEqual(
            {"fred-skills/v1": expected_fp}, res_after["partialFingerprints"]
        )
        self.assertEqual({"fred-skills/v1": expected_fp}, res_after["fingerprints"])
        self.assertEqual("F-1", res_before["properties"]["findingId"])
        self.assertEqual("F-2", res_after["properties"]["findingId"])

    def test_deduplicates_rule_descriptors_per_deterministic_rule_id(self):
        audit = copy.deepcopy(self.base_audit)
        duplicate = copy.deepcopy(audit["findings"][0])
        duplicate["id"] = "F-2"
        audit["findings"].append(duplicate)

        sarif = audit_to_sarif(audit)
        run = sarif["runs"][0]
        self.assertEqual(1, len(run["tool"]["driver"]["rules"]))
        self.assertEqual(2, len(run["results"]))
        self.assertEqual(0, run["results"][0]["ruleIndex"])
        self.assertEqual(0, run["results"][1]["ruleIndex"])
        self.assertEqual(run["results"][0]["ruleId"], run["results"][1]["ruleId"])
        self.assertEqual("F-1", run["results"][0]["properties"]["findingId"])
        self.assertEqual("F-2", run["results"][1]["properties"]["findingId"])

        if SARIF_SCHEMA_PATH.exists():
            import jsonschema

            schema = json.loads(SARIF_SCHEMA_PATH.read_text(encoding="utf-8"))
            errors = list(jsonschema.Draft202012Validator(schema).iter_errors(sarif))
            self.assertEqual([], errors, [e.message for e in errors])

    def test_standalone_packaging_has_no_fredlib_dependency(self):
        import ast
        import os

        for script in [SCRIPT_PATH, SKILL_SCRIPT_PATH]:
            tree = ast.parse(script.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name.startswith(("fredlib", "skill_tools"))
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith(("fredlib", "skill_tools")))

        isolated_dir = self.temp_path / "isolated_skill"
        isolated_dir.mkdir()
        isolated_script = isolated_dir / "audit_to_sarif.py"
        isolated_script.write_bytes(SKILL_SCRIPT_PATH.read_bytes())
        audit_file = isolated_dir / "audit.json"
        audit_file.write_text(json.dumps(self.base_audit), encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, str(isolated_script), str(audit_file)],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(isolated_dir),
            env=env,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        exported = json.loads(proc.stdout)
        self.assertIn("partialFingerprints", exported["runs"][0]["results"][0])

    def test_sarif_roundtrip_export_then_import(self):
        audit = copy.deepcopy(self.base_audit)
        audit["findings"][0]["severity"] = "critical"
        sarif_path = self.temp_path / "roundtrip.sarif"
        convert_file(FIXTURE_PATH, sarif_path)

        sarif_data = audit_to_sarif(audit)
        sarif_path.write_text(json.dumps(sarif_data), encoding="utf-8")
        imported = fred_runs.import_sarif(sarif_path)
        obs = imported["observations"][0]

        expected_fp = fred_runs.fingerprint(audit["findings"][0], audit)
        self.assertEqual(f"fred/concurrency/{expected_fp[:12]}", obs["rule"])
        self.assertEqual("error", obs["reported_level"])
        self.assertEqual(
            [
                {
                    "path": "session.mjs",
                    "line": 4,
                    "end_line": 8,
                    "symbol": "Session.start",
                }
            ],
            obs["locations"],
        )
        self.assertEqual({"fred-skills/v1": expected_fp}, obs["partial_fingerprints"])
        self.assertEqual({"fred-skills/v1": expected_fp}, obs["fingerprints"])

    def test_domain_artifacts_rejected_with_clear_audit_guidance(self):
        payloads = {
            "architecture.json": {
                "schema_version": "1.0",
                "title": "System Architecture",
                "status": "draft",
            },
            "review.json": {
                "schema_version": "1.0",
                "verdict": "comment",
                "summary": "Review summary",
                "findings": [],
            },
            "testing.json": {
                "schema_version": "1.0",
                "component": "core",
                "framework": "unittest",
                "test_cases": [],
            },
            "finding-verification.json": {
                "finding_id": "F-1",
                "reproduction_status": "supported",
                "safe_control_outcomes": [{"control": "ok", "passed": True}],
                "confidence_rating": "high",
            },
            "regression.json": {
                "finding_id": "F-1",
                "failure_boundaries": ["race"],
                "test_command_traces": ["pytest"],
                "assertion_expectations": ["assert ok"],
            },
            "plan.json": {
                "task_breakdowns": [
                    {
                        "task_id": "T-1",
                        "title": "Fix",
                        "file_scopes": ["app.py"],
                        "acceptance_criteria": ["ok"],
                    }
                ],
                "dependency_order": ["T-1"],
                "file_scopes": ["app.py"],
                "acceptance_criteria": ["ok"],
            },
            "fix.json": {
                "file_changes": ["app.py"],
                "diff_summaries": ["fix"],
                "execution_results": [
                    {"command": "pytest", "exit_code": 0, "passed": True}
                ],
                "compatibility_limits": [],
            },
            "verification.json": {
                "before_revision": "aaa",
                "after_revision": "bbb",
                "original_reproduced": True,
                "regression_passed": True,
                "safe_controls_passed": True,
                "commands": ["pytest"],
                "result": "passed",
            },
            "reconciliation.json": {
                "run_id": "run-123",
                "finding_fingerprint_matches": [],
                "remediation_state_transitions": [],
                "unmapped_records": [],
            },
            "remediation.json": {
                "schema_version": "1.0",
                "records": [],
            },
            "run.json": {
                "schema_version": "1.0",
                "run_id": "run-123",
                "plan": {"task": "review-pr"},
            },
        }
        for filename, payload in payloads.items():
            with self.subTest(artifact=filename):
                with self.assertRaises(ValueError) as ctx:
                    audit_to_sarif(payload)
                self.assertIn("audit.json", str(ctx.exception))
                self.assertIn(filename, str(ctx.exception))

                dom_dir = self.temp_path / f"dom_{filename}"
                dom_dir.mkdir()
                explicit_file = dom_dir / filename
                explicit_file.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError) as file_ctx:
                    convert_file(explicit_file)
                self.assertIn("audit.json", str(file_ctx.exception))
                self.assertIn(filename, str(file_ctx.exception))

                with self.assertRaises(ValueError) as dir_ctx:
                    convert_file(dom_dir)
                self.assertIn("audit.json", str(dir_ctx.exception))
                self.assertIn(filename, str(dir_ctx.exception))

        with self.assertRaises(ValueError) as empty_ctx:
            audit_to_sarif({})
        self.assertIn("audit.json", str(empty_ctx.exception))

    def test_domain_artifact_tables_and_detector_match_common_and_catalog(
        self,
    ) -> None:
        import audit_to_sarif as sarif_mod
        from fredlib import common

        expected_markers = tuple(
            (f"{kind}.json", markers) for kind, markers in common._DOMAIN_KIND_KEY_SETS
        )
        self.assertEqual(expected_markers, sarif_mod._NON_AUDIT_ARTIFACT_MARKERS)

        expected_names = (
            set(common.DOMAIN_DELIVERABLE_SCHEMAS)
            | set(common.WORKFLOW_DELIVERABLE_SCHEMAS)
            | {"run.json"}
        )
        self.assertEqual(expected_names, set(sarif_mod._NON_AUDIT_ARTIFACT_NAMES))
        self.assertEqual(
            len(sarif_mod._NON_AUDIT_ARTIFACT_NAMES),
            len(set(sarif_mod._NON_AUDIT_ARTIFACT_NAMES)),
        )

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        samples: list[tuple[str, object]] = [
            ("none", None),
            ("list", []),
            ("empty_dict", {}),
            (
                "audit_with_summary",
                {
                    "schema_version": "1.0",
                    "summary": "Audit summary",
                    "findings": [],
                    "evidence": [],
                },
            ),
            ("audit_with_run_id", {"run_id": "r-1", "findings": []}),
            ("run_minimal", {"run_id": "r-1"}),
            ("review_minimal", {"summary": "Review", "findings": []}),
            ("testing_minimal", {"component": "api", "framework": "pytest"}),
            ("arch_minimal", {"title": "Arch", "status": "draft"}),
            (
                "finding_verification",
                {"reproduction_status": "supported"},
            ),
            ("regression", {"failure_boundary": "edge"}),
            ("plan", {"plan_id": "P-1"}),
            ("fix", {"diff_summary": "patched"}),
            ("verification", {"safe_controls_passed": True}),
            ("reconciliation", {"state_transitions": []}),
            ("remediation", {"records": []}),
        ]
        for suite in suites_doc["suites"]:
            samples.append(
                (
                    f"starter_{suite['name']}",
                    copy.deepcopy(suite["artifact"]["starter"]),
                )
            )

        for label, sample in samples:
            with self.subTest(sample=label):
                self.assertEqual(
                    common._detect_domain_artifact(sample),
                    sarif_mod._detect_domain_artifact(sample),
                )


class AuditToSarifSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_repo_scripts_are_synchronized(self):
        self.assertEqual(
            SCRIPT_PATH.read_bytes(),
            SKILL_SCRIPT_PATH.read_bytes(),
            "scripts/audit_to_sarif.py and skills/repo-audit-synthesis/scripts/audit_to_sarif.py must be identical",
        )
        problems = sync(ROOT, check=True)
        script_problems = [p for p in problems if "scripts/audit_to_sarif.py" in p]
        self.assertEqual([], script_problems)

    def test_sync_detects_and_repairs_script_drift(self):
        (self.temp_root / "catalog").mkdir(parents=True)
        document = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": "repository-audits",
                    "phases": [
                        {
                            "id": 4,
                            "title": "Findings and issue planning",
                            "skills": ["repo-audit-synthesis", "repo-audit-other"],
                        }
                    ],
                }
            ],
        }
        (self.temp_root / "catalog/suites.json").write_text(json.dumps(document))

        for skill_name in ["repo-audit-synthesis", "repo-audit-other"]:
            skill_dir = self.temp_root / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: Test skill\n---\n# Test\n"
            )

        shared_dir = self.temp_root / "shared/repo-audit"
        shared_dir.mkdir(parents=True)
        (shared_dir / "manifest.json").write_text(
            json.dumps({"files": [], "scripts": ["scripts/audit_to_sarif.py"]})
        )

        (self.temp_root / "scripts").mkdir(parents=True)
        canonical_script = self.temp_root / "scripts/audit_to_sarif.py"
        canonical_script.write_text("print('canonical')\n")

        # 1. Target does not exist yet -> drift detected with check=True
        problems = sync(self.temp_root, check=True)
        self.assertTrue(
            any("repo-audit-synthesis/scripts/audit_to_sarif.py" in p for p in problems)
        )

        # 2. Sync repairs and writes to target safely creating missing directories
        target_script = (
            self.temp_root / "skills/repo-audit-synthesis/scripts/audit_to_sarif.py"
        )
        self.assertFalse(target_script.exists())
        self.assertEqual([], sync(self.temp_root, check=False))
        self.assertTrue(target_script.is_file())
        self.assertEqual(canonical_script.read_bytes(), target_script.read_bytes())

        # 3. Check passes now
        self.assertEqual([], sync(self.temp_root, check=True))

        # 4. Non-synthesis audit skill did NOT receive the script
        other_script = (
            self.temp_root / "skills/repo-audit-other/scripts/audit_to_sarif.py"
        )
        self.assertFalse(other_script.exists())

        # 5. Modifying skill script causes drift detection
        target_script.write_text("print('divergent')\n")
        problems = sync(self.temp_root, check=True)
        self.assertTrue(
            any("repo-audit-synthesis/scripts/audit_to_sarif.py" in p for p in problems)
        )

        # 6. Running sync repairs drift again
        self.assertEqual([], sync(self.temp_root, check=False))
        self.assertEqual(canonical_script.read_bytes(), target_script.read_bytes())
        self.assertEqual([], sync(self.temp_root, check=True))

        # 7. If non-synthesis skill has stray script, check detects it and sync removes it
        other_script.parent.mkdir(parents=True, exist_ok=True)
        other_script.write_text("print('stray')\n")
        problems = sync(self.temp_root, check=True)
        self.assertTrue(any("repo-audit-other" in p for p in problems))
        self.assertEqual([], sync(self.temp_root, check=False))
        self.assertFalse(other_script.exists())

    def test_safe_relative_path_valid(self):
        valid_paths = [
            "scripts/audit_to_sarif.py",
            "nested/dir/resource.json",
            "schemas/v1/schema.json",
            "simple.txt",
        ]
        for p in valid_paths:
            with self.subTest(path=p):
                result = _safe_relative_path(p)
                self.assertIsInstance(result, Path)
                self.assertEqual(Path(p), result)

    def test_safe_relative_path_rejects_invalid_inputs(self):
        invalid_paths = [
            "",
            "   ",
            "/absolute/path/file.py",
            "/etc/passwd",
            "C:/windows/file.py",
            "../parent.py",
            "dir/../../escaped.py",
            "nested/../escape.py",
            "scripts/..",
            r"windows\path\file.py",
            r"dir\file.py",
        ]
        for p in invalid_paths:
            with self.subTest(path=p):
                with self.assertRaises(ValueError) as ctx:
                    _safe_relative_path(p)
                self.assertIn(f"Invalid shared resource path: {p}", str(ctx.exception))

    def test_sync_manifest_with_invalid_paths_raises_error(self):
        (self.temp_root / "catalog").mkdir(parents=True)
        document = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": "repository-audits",
                    "phases": [
                        {
                            "id": 4,
                            "title": "Findings and issue planning",
                            "skills": ["repo-audit-synthesis"],
                        }
                    ],
                }
            ],
        }
        (self.temp_root / "catalog/suites.json").write_text(json.dumps(document))

        skill_dir = self.temp_root / "skills/repo-audit-synthesis"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: repo-audit-synthesis\ndescription: Test skill\n---\n# Test\n"
        )

        shared_dir = self.temp_root / "shared/repo-audit"
        shared_dir.mkdir(parents=True)

        invalid_manifest_cases = [
            {"files": ["/absolute/path.py"]},
            {"files": ["../escaped.py"]},
            {"files": [r"windows\path.py"]},
            {"files": [""]},
            {"scripts": {"../outside.py": ["repo-audit-synthesis"]}},
            {"scripts": ["../outside.py"]},
            {
                "scripts": [
                    {"path": "../outside.py", "skills": ["repo-audit-synthesis"]}
                ]
            },
        ]
        manifest_file = shared_dir / "manifest.json"
        for case in invalid_manifest_cases:
            manifest_file.write_text(json.dumps(case))
            with self.subTest(case=case):
                with self.assertRaises(ValueError) as ctx:
                    sync(self.temp_root, check=True)
                self.assertIn("Invalid shared resource path:", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

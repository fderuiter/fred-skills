"""Tests for repo-audit-diagnostics triage, secret scrubbing, and bundle generation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills/repo-audit-diagnostics"
SCRIPT = PACKAGE / "scripts/triage.py"

spec = importlib.util.spec_from_file_location("diagnostics_triage", SCRIPT)
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)

SCHEMA = json.loads((PACKAGE / "assets/bundle.schema.json").read_text(encoding="utf-8"))


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_detect_tool_timeout(self):
        transcript = """[2026-11-13T20:01:00Z] Coordinator: Launching Pass 2 concurrency audit
[2026-11-13T20:01:05Z] Running specialist repo-audit-concurrency
[2026-11-13T20:01:10Z] Executing command: ripgrep --multiline "(async|await)" src/
[2026-11-13T20:01:40Z] Command 'ripgrep' timed out after 30.0 seconds
[2026-11-13T20:01:41Z] Subprocess failed with exit status 124
"""
        failure = triage.detect_failures(transcript)
        self.assertEqual(failure["failure_type"], "tool_timeout")
        self.assertEqual(failure["line_number"], 4)
        self.assertIn("timed out after 30.0 seconds", failure["matched_line"])
        self.assertIn("Tool execution timed out", failure["summary"])

    def test_detect_schema_validation_error(self):
        transcript = """[2026-11-13T20:10:00Z] Validating ledger output against schema
[2026-11-13T20:10:02Z] Running validate_audit.py on audit.json
[2026-11-13T20:10:03Z] jsonschema.exceptions.ValidationError: 'schema_version' is a required property
[2026-11-13T20:10:04Z] Validator exited with return code 1
"""
        failure = triage.detect_failures(transcript)
        self.assertEqual(failure["failure_type"], "schema_validation_error")
        self.assertEqual(failure["line_number"], 3)
        self.assertIn(
            "'schema_version' is a required property", failure["matched_line"]
        )

    def test_detect_context_window_limit(self):
        transcript = """[2026-11-13T20:15:00Z] Sending prompt to agent model...
[2026-11-13T20:15:05Z] Error: context_length_exceeded: maximum context length is 128000 tokens
[2026-11-13T20:15:06Z] Request dropped by provider API
"""
        failure = triage.detect_failures(transcript)
        self.assertEqual(failure["failure_type"], "context_limit")
        self.assertEqual(failure["line_number"], 2)
        self.assertIn("context_length_exceeded", failure["matched_line"])

    def test_correlate_specialist_domain_and_ledger(self):
        transcript = """Starting audit pass with repo-audit-concurrency specialist
Inspecting shared state mutations in event loop
Recorded candidate finding F-1 backed by evidence E-1 and contract C-1
Execution failed during verification
"""
        audit_data = {
            "evidence": [
                {"id": "E-1", "path": "src/queue.py", "line_start": 10, "line_end": 20}
            ],
            "contracts": [{"id": "C-1", "name": "FIFO order", "evidence_ids": ["E-1"]}],
            "findings": [
                {
                    "id": "F-1",
                    "topic": "concurrency",
                    "evidence_ids": ["E-1"],
                    "contract_ids": ["C-1"],
                }
            ],
        }
        correlation = triage.correlate_audit_domain(transcript, audit_data)
        self.assertEqual(correlation["specialist_skill"], "repo-audit-concurrency")
        self.assertEqual(correlation["topic"], "concurrency")
        self.assertIn("findings:F-1", correlation["ledger_references"])
        self.assertIn("evidence:E-1", correlation["ledger_references"])
        self.assertIn("contracts:C-1", correlation["ledger_references"])

    def test_scrub_sensitive_credentials(self):
        sensitive_text = """
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature12345
Bearer token_abc1234567890abcdef
OpenAI Key: sk-proj-1234567890abcdef1234567890abcdef
GitHub: ghp_1234567890abcdef1234567890abcdef1234
AWS: AKIAIOSFODNN7EXAMPLE
Private key:
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Y1+abcdefghijklmnopqrstuvwxyz1234567890
-----END RSA PRIVATE KEY-----
Config: password="SuperSecretPassword123"
Token: api_key='secret_key_value_999'
"""
        scrubbed = triage.scrub_credentials(sensitive_text)

        # None of the real secret values should be present
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", scrubbed)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890abcdef", scrubbed)
        self.assertNotIn("ghp_1234567890abcdef1234567890abcdef1234", scrubbed)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", scrubbed)
        self.assertNotIn(
            "MIIEowIBAAKCAQEA0Y1+abcdefghijklmnopqrstuvwxyz1234567890", scrubbed
        )
        self.assertNotIn("SuperSecretPassword123", scrubbed)
        self.assertNotIn("secret_key_value_999", scrubbed)

        # Redaction placeholders should be present
        self.assertIn("[REDACTED_AUTH_TOKEN]", scrubbed)
        self.assertIn("[REDACTED_API_KEY]", scrubbed)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", scrubbed)
        self.assertIn("[REDACTED_AWS_KEY]", scrubbed)
        self.assertIn("[REDACTED_PRIVATE_KEY]", scrubbed)
        self.assertIn("[REDACTED_SECRET]", scrubbed)

    def test_bundle_conforms_to_schema(self):
        import jsonschema

        transcript_file = self.temp_path / "session.log"
        transcript_file.write_text(
            """Running repo-audit-failure-modes
Executing verification test
Command '['pytest', 'tests/test_failure.py']' timed out after 60 seconds
Traceback (most recent call last):
  File "runner.py", line 42, in run
TimeoutError: Process timed out
Bearer sk-proj-1234567890abcdef1234567890
""",
            encoding="utf-8",
        )

        workspace_dir = self.temp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "run.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "run-test-123",
                    "repository": {"path": "/app/test-repo"},
                    "checkpoints": [
                        {
                            "at": "2026-11-13T20:00:00Z",
                            "state": "running",
                            "note": "Pass 1 complete",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        bundle = triage.generate_diagnostic_bundle(transcript_file, workspace_dir)

        # Validate bundle against bundle.schema.json
        validator = jsonschema.Draft202012Validator(SCHEMA)
        errors = list(validator.iter_errors(bundle))
        self.assertEqual([], errors, [e.message for e in errors])

        self.assertEqual(bundle["session_id"], "run-test-123")
        self.assertEqual(
            bundle["failure_classification"]["failure_type"], "tool_timeout"
        )
        self.assertEqual(
            bundle["correlated_domain"]["specialist_skill"], "repo-audit-failure-modes"
        )
        self.assertIn("running: Pass 1 complete", bundle["recovery"]["last_checkpoint"])
        self.assertTrue(
            any("checkpoint" in inst for inst in bundle["recovery"]["instructions"])
        )
        self.assertNotIn("sk-proj", bundle["scrubbed_transcript"])

    def test_export_bundle_generates_json_and_markdown(self):
        transcript_file = self.temp_path / "transcript.txt"
        transcript_file.write_text(
            "Tool execution timed out after 15 seconds\n", encoding="utf-8"
        )

        bundle = triage.generate_diagnostic_bundle(
            transcript_file, session_id="test-export-session"
        )
        output_dir = self.temp_path / "bundle_export"

        json_path, report_path = triage.export_bundle(bundle, output_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(report_path.is_file())

        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["session_id"], "test-export-session")
        self.assertEqual(
            loaded["failure_classification"]["failure_type"], "tool_timeout"
        )

        report_content = report_path.read_text(encoding="utf-8")
        self.assertIn("# Diagnostic Triage Report", report_content)
        self.assertIn("test-export-session", report_content)
        self.assertIn("tool_timeout", report_content)

    def test_cli_execution_and_schema_check(self):
        transcript_file = self.temp_path / "session_cli.txt"
        transcript_file.write_text(
            "Starting repo-audit-trust-boundaries\njsonschema.exceptions.ValidationError: Reversed lines: E-1\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "cli_output"

        cmd = [
            sys.executable,
            str(SCRIPT),
            str(transcript_file),
            "--output-dir",
            str(export_dir),
            "--session-id",
            "cli-test-session",
            "--check-schema",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((export_dir / "diagnostic_bundle.json").is_file())
        self.assertTrue((export_dir / "diagnostic_report.md").is_file())


if __name__ == "__main__":
    unittest.main()

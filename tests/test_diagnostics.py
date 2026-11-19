"""Tests for repo-audit-diagnostics triage, secret scrubbing, and bundle generation."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

pty: ModuleType | None
try:
    import pty as _pty

    pty = _pty
except ImportError:
    pty = None

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PACKAGE = ROOT / "skills/repo-audit-diagnostics"
SCRIPT = PACKAGE / "scripts/triage.py"

spec = importlib.util.spec_from_file_location("diagnostics_triage", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT}")
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

        # triage.validate_bundle succeeds on valid bundle
        triage.validate_bundle(bundle)

    def test_validate_bundle_schema_validation_error_chains_cause(self):
        import jsonschema

        with self.assertRaises(ValueError) as ctx:
            triage.validate_bundle({"schema_version": "invalid-schema"})
        self.assertIn("Schema validation error", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, jsonschema.ValidationError)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_validate_bundle_missing_jsonschema_suppresses_context(self):
        with patch.dict("sys.modules", {"jsonschema": None}):
            with self.assertRaises(ValueError) as ctx:
                triage.validate_bundle({})
            self.assertIn("jsonschema not installed", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)
            self.assertTrue(ctx.exception.__suppress_context__)

    def test_generate_bundle_suppresses_corrupted_workspace_state(self):
        transcript_file = self.temp_path / "transcript_corrupt.txt"
        transcript_file.write_text("Tool execution timed out\n", encoding="utf-8")
        corrupt_workspace = self.temp_path / "corrupt_workspace"
        corrupt_workspace.mkdir()
        (corrupt_workspace / "run.json").write_text("invalid json {", encoding="utf-8")
        (corrupt_workspace / "audit.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        bundle = triage.generate_diagnostic_bundle(transcript_file, corrupt_workspace)
        self.assertEqual(bundle["session_id"], transcript_file.stem)

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

    def test_fred_cli_triage_prints_json_summary(self):
        transcript_file = self.temp_path / "fred_cli_transcript.txt"
        transcript_file.write_text(
            "[2026-11-13T20:01:40Z] Command 'ripgrep' timed out after 30.0 seconds\n",
            encoding="utf-8",
        )
        fred_cli = ROOT / "scripts/fred.py"
        cmd = [sys.executable, str(fred_cli), "triage", str(transcript_file)]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["schema_version"], "1.0")
        self.assertEqual(
            summary["failure_classification"]["failure_type"], "tool_timeout"
        )
        self.assertIn("recovery", summary)
        self.assertIn("environment", summary)

    def test_fred_cli_triage_output_directory_and_check_schema(self):
        transcript_file = self.temp_path / "fred_cli_schema.txt"
        transcript_file.write_text(
            "Starting repo-audit-trust-boundaries\njsonschema.exceptions.ValidationError: Duplicate IDs in findings: F-1\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "fred_cli_output"
        fred_cli = ROOT / "scripts/fred.py"
        cmd = [
            sys.executable,
            str(fred_cli),
            "triage",
            str(transcript_file),
            "--output",
            str(export_dir),
            "--check-schema",
            "--session-id",
            "session-fred-test",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bundle_file = export_dir / "diagnostic_bundle.json"
        report_file = export_dir / "diagnostic_report.md"
        self.assertTrue(bundle_file.is_file())
        self.assertTrue(report_file.is_file())

        bundle = json.loads(bundle_file.read_text(encoding="utf-8"))
        self.assertEqual(bundle["session_id"], "session-fred-test")
        self.assertEqual(
            bundle["failure_classification"]["failure_type"],
            "schema_validation_error",
        )
        report_text = report_file.read_text(encoding="utf-8")
        self.assertIn("# Diagnostic Triage Report", report_text)
        self.assertIn("session-fred-test", report_text)

    def test_fred_cli_triage_scrubs_credentials(self):
        transcript_file = self.temp_path / "sensitive_fred.txt"
        transcript_file.write_text(
            "Authorization: Bearer supersecrettokenvalue123456\nsk-proj-9999999999abcdef9999999999\npassword: 'myplaintextpassword'\nCommand timed out after 10 seconds\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "scrub_output"
        fred_cli = ROOT / "scripts/fred.py"
        cmd = [
            sys.executable,
            str(fred_cli),
            "triage",
            str(transcript_file),
            "--output",
            str(export_dir),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        self.assertNotIn("supersecrettokenvalue123456", proc.stdout)
        self.assertNotIn("sk-proj-9999999999abcdef9999999999", proc.stdout)
        self.assertNotIn("myplaintextpassword", proc.stdout)

        bundle_content = (export_dir / "diagnostic_bundle.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("supersecrettokenvalue123456", bundle_content)
        self.assertNotIn("sk-proj-9999999999abcdef9999999999", bundle_content)
        self.assertNotIn("myplaintextpassword", bundle_content)
        self.assertIn("[REDACTED_AUTH_TOKEN]", bundle_content)
        self.assertIn("[REDACTED_API_KEY]", bundle_content)

    def _run_fred_pty(self, *args: str) -> subprocess.CompletedProcess[str]:
        if pty is None:
            self.skipTest("pty module is not available on this platform")
        fred_cli = ROOT / "scripts/fred.py"
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, str(fred_cli), *args],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            except OSError:
                break
        os.close(master)
        returncode = proc.wait()
        return subprocess.CompletedProcess(
            args=[sys.executable, str(fred_cli), *args],
            returncode=returncode,
            stdout=b"".join(chunks).decode("utf-8", errors="replace"),
            stderr="",
        )

    def test_fred_cli_triage_help_suppresses_legacy_aliases_while_preserving_runtime(
        self,
    ):
        fred_cli = ROOT / "scripts/fred.py"
        help_proc = subprocess.run(
            [sys.executable, str(fred_cli), "triage", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_proc.returncode, 0, help_proc.stderr)
        self.assertIn("--workspace", help_proc.stdout)
        self.assertIn("--output", help_proc.stdout)
        self.assertNotIn("--transcript", help_proc.stdout)
        self.assertNotIn("--workspace-dir", help_proc.stdout)
        self.assertNotIn("--output-dir", help_proc.stdout)

        # Legacy aliases remain active at runtime
        ws_dir = self.temp_path / "legacy_ws"
        ws_dir.mkdir()
        (ws_dir / "run.json").write_text(
            json.dumps({"run_id": "run-legacy-alias"}), encoding="utf-8"
        )
        transcript_file = self.temp_path / "legacy_transcript.txt"
        transcript_file.write_text(
            "Command 'ripgrep' timed out after 30.0 seconds\n", encoding="utf-8"
        )
        out_dir = self.temp_path / "legacy_out"
        runtime_proc = subprocess.run(
            [
                sys.executable,
                str(fred_cli),
                "triage",
                "--transcript",
                str(transcript_file),
                "--workspace-dir",
                str(ws_dir),
                "--output-dir",
                str(out_dir),
                "--check-schema",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(runtime_proc.returncode, 0, runtime_proc.stderr)
        self.assertTrue((out_dir / "diagnostic_bundle.json").is_file())
        self.assertTrue((out_dir / "diagnostic_report.md").is_file())
        bundle = json.loads(runtime_proc.stdout)
        self.assertEqual(bundle["session_id"], "run-legacy-alias")

    def test_fred_cli_triage_tty_suppresses_transcript_dump_and_respects_json_flag(
        self,
    ):
        import argparse

        import fred

        transcript_file = self.temp_path / "tty_triage_session.txt"
        transcript_marker = "UNIQUE_TRANSCRIPT_LINE_MARKER_DO_NOT_DUMP_TO_TTY"
        transcript_file.write_text(
            f"Starting repo-audit-concurrency\n{transcript_marker}\nCommand 'ripgrep' timed out after 30.0 seconds\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "tty_triage_out"
        bundle = triage.generate_diagnostic_bundle(transcript_file)

        rendered_with_out = fred.render_tty_output(
            "triage",
            bundle,
            argparse.Namespace(command="triage", output=export_dir, output_dir=None),
        )
        self.assertIn(
            f"Exported diagnostic bundle: {export_dir / 'diagnostic_bundle.json'}",
            rendered_with_out,
        )
        self.assertIn(
            f"Exported diagnostic report: {export_dir / 'diagnostic_report.md'}",
            rendered_with_out,
        )
        self.assertIn("tool_timeout", rendered_with_out)
        self.assertNotIn(transcript_marker, rendered_with_out)

        rendered_no_out = fred.render_tty_output(
            "triage",
            bundle,
            argparse.Namespace(command="triage", output=None, output_dir=None),
        )
        self.assertIn("Failure classification: tool_timeout", rendered_no_out)
        self.assertIn("Recovery steps:", rendered_no_out)
        self.assertNotIn(transcript_marker, rendered_no_out)

        # 1. TTY with --output: prints exported paths and failure type, no transcript dump
        tty_with_out = self._run_fred_pty(
            "triage", str(transcript_file), "--output", str(export_dir)
        )
        self.assertEqual(tty_with_out.returncode, 0, tty_with_out.stdout)
        self.assertIn(
            f"Exported diagnostic bundle: {export_dir / 'diagnostic_bundle.json'}",
            tty_with_out.stdout,
        )
        self.assertIn(
            f"Exported diagnostic report: {export_dir / 'diagnostic_report.md'}",
            tty_with_out.stdout,
        )
        self.assertIn("tool_timeout", tty_with_out.stdout)
        self.assertNotIn("scrubbed_transcript", tty_with_out.stdout)
        self.assertNotIn(transcript_marker, tty_with_out.stdout)

        # 2. TTY without --output: prints failure classification and recovery steps, no scrubbed_transcript
        tty_no_out = self._run_fred_pty("triage", str(transcript_file))
        self.assertEqual(tty_no_out.returncode, 0, tty_no_out.stdout)
        self.assertIn("Failure classification: tool_timeout", tty_no_out.stdout)
        self.assertIn("Recovery steps:", tty_no_out.stdout)
        self.assertNotIn("scrubbed_transcript", tty_no_out.stdout)
        self.assertNotIn(transcript_marker, tty_no_out.stdout)

        # 3. TTY with --json: emits full raw JSON bundle
        tty_json = self._run_fred_pty("triage", str(transcript_file), "--json")
        self.assertEqual(tty_json.returncode, 0, tty_json.stdout)
        parsed = json.loads(tty_json.stdout)
        self.assertEqual(
            parsed["failure_classification"]["failure_type"], "tool_timeout"
        )
        self.assertIn(transcript_marker, parsed["scrubbed_transcript"])

    def test_recover_audit_recipe_in_tasks_and_planning(self):
        fred_cli = ROOT / "scripts/fred.py"
        proc = subprocess.run(
            [sys.executable, str(fred_cli), "tasks"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tasks_data = json.loads(proc.stdout)
        recipes = tasks_data.get("recipes", {})
        self.assertIn("recover-audit", recipes)
        self.assertIn("repo-audit-diagnostics", recipes["recover-audit"]["skills"])

        from fredlib import runs

        # Standard comprehensive planning should not include repo-audit-diagnostics
        plan_standard, _ = runs.plan(ROOT, task="understand-repo", mode="comprehensive")
        self.assertNotIn("repo-audit-diagnostics", plan_standard["skills"])
        self.assertNotIn("repo-audit-diagnostics", plan_standard["deferred_skills"])

        # Planning with recover-audit task includes repo-audit-diagnostics
        plan_recovery, _ = runs.plan(ROOT, task="recover-audit")
        self.assertIn("repo-audit-diagnostics", plan_recovery["skills"])

        # Comprehensive mode on recover-audit also preserves repo-audit-diagnostics
        plan_recovery_comp, _ = runs.plan(
            ROOT, task="recover-audit", mode="comprehensive"
        )
        self.assertIn("repo-audit-diagnostics", plan_recovery_comp["skills"])

    def test_discover_transcript_single_candidate(self):
        workspace = self.temp_path / "ws_single"
        workspace.mkdir()
        transcript = workspace / "session-transcript.jsonl"
        transcript.write_text(
            "Command 'ripgrep' timed out after 30.0 seconds\n", encoding="utf-8"
        )

        discovered = triage.discover_transcript(workspace)
        self.assertEqual(discovered, transcript)

    def test_discover_transcript_in_logs_subdirectory(self):
        workspace = self.temp_path / "ws_logs"
        logs = workspace / "logs"
        logs.mkdir(parents=True)
        transcript = logs / "session.json"
        transcript.write_text(
            "Command 'ripgrep' timed out after 30.0 seconds\n", encoding="utf-8"
        )

        discovered = triage.discover_transcript(workspace)
        self.assertEqual(discovered, transcript)

    def test_discover_transcript_newest_by_mtime(self):
        import os

        workspace = self.temp_path / "ws_multi"
        workspace.mkdir()
        t1 = workspace / "transcript.json"
        t1.write_text("old failure\n", encoding="utf-8")
        os.utime(t1, (1000, 1000))

        t2 = workspace / "session-transcript.jsonl"
        t2.write_text("new failure\n", encoding="utf-8")
        os.utime(t2, (2000, 2000))

        discovered = triage.discover_transcript(workspace)
        self.assertEqual(discovered, t2)

    def test_discover_transcript_naming_priority_tiebreak(self):
        import os

        workspace = self.temp_path / "ws_tie"
        workspace.mkdir()
        t_json = workspace / "transcript.json"
        t_json.write_text("failure json\n", encoding="utf-8")
        os.utime(t_json, (1500, 1500))

        t_txt = workspace / "transcript.txt"
        t_txt.write_text("failure txt\n", encoding="utf-8")
        os.utime(t_txt, (1500, 1500))

        discovered = triage.discover_transcript(workspace)
        self.assertEqual(discovered, t_json)

    def test_discover_transcript_missing_raises_informative_error(self):
        workspace = self.temp_path / "ws_empty"
        workspace.mkdir()

        with self.assertRaises(triage.TranscriptNotFoundError) as cm:
            triage.discover_transcript(workspace)

        msg = str(cm.exception)
        for pattern in (
            "transcript.jsonl",
            "transcript.json",
            "session-transcript.jsonl",
            "transcript.txt",
            "session.json",
        ):
            self.assertIn(pattern, msg)

    def test_fred_cli_triage_automatic_discovery(self):
        workspace = self.temp_path / "ws_fred_discovery"
        workspace.mkdir()
        transcript = workspace / "session-transcript.jsonl"
        transcript.write_text(
            "[2026-11-13T20:01:40Z] Command 'ripgrep' timed out after 30.0 seconds\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "fred_auto_output"

        fred_cli = ROOT / "scripts/fred.py"
        cmd = [
            sys.executable,
            str(fred_cli),
            "triage",
            "--workspace",
            str(workspace),
            "--output",
            str(export_dir),
            "--check-schema",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Discovered transcript:", proc.stderr)
        self.assertIn(str(transcript), proc.stderr)

        summary = json.loads(proc.stdout)
        self.assertEqual(
            summary["failure_classification"]["failure_type"], "tool_timeout"
        )
        self.assertTrue((export_dir / "diagnostic_bundle.json").is_file())

    def test_fred_cli_triage_explicit_transcript_bypasses_discovery(self):
        workspace = self.temp_path / "ws_bypass"
        workspace.mkdir()
        auto_transcript = workspace / "transcript.jsonl"
        auto_transcript.write_text("auto failure\n", encoding="utf-8")

        explicit_transcript = self.temp_path / "explicit.txt"
        explicit_transcript.write_text(
            "[2026-11-13T20:01:40Z] Command 'ripgrep' timed out after 30.0 seconds\n",
            encoding="utf-8",
        )

        fred_cli = ROOT / "scripts/fred.py"
        cmd = [
            sys.executable,
            str(fred_cli),
            "triage",
            str(explicit_transcript),
            "--workspace",
            str(workspace),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Discovered transcript:", proc.stderr)

    def test_triage_script_cli_automatic_discovery(self):
        workspace = self.temp_path / "ws_triage_script"
        workspace.mkdir()
        transcript = workspace / "session-transcript.jsonl"
        transcript.write_text(
            "Starting repo-audit-trust-boundaries\njsonschema.exceptions.ValidationError: Reversed lines: E-1\n",
            encoding="utf-8",
        )
        export_dir = self.temp_path / "script_auto_output"

        cmd = [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(workspace),
            "--output-dir",
            str(export_dir),
            "--check-schema",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Discovered transcript:", proc.stderr)
        self.assertTrue((export_dir / "diagnostic_bundle.json").is_file())

    def test_triage_script_cli_missing_candidate_lists_patterns(self):
        workspace = self.temp_path / "ws_triage_empty"
        workspace.mkdir()

        cmd = [sys.executable, str(SCRIPT), "--workspace", str(workspace)]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        for pattern in (
            "transcript.jsonl",
            "transcript.json",
            "session-transcript.jsonl",
            "transcript.txt",
            "session.json",
        ):
            self.assertIn(pattern, proc.stderr)

    def test_fred_cli_triage_missing_candidate_lists_patterns(self):
        workspace = self.temp_path / "ws_fred_empty"
        workspace.mkdir()

        fred_cli = ROOT / "scripts/fred.py"
        cmd = [sys.executable, str(fred_cli), "triage", "--workspace", str(workspace)]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No candidate transcript found in workspace", proc.stderr)
        for pattern in (
            "transcript.jsonl",
            "transcript.json",
            "session-transcript.jsonl",
            "transcript.txt",
            "session.json",
        ):
            self.assertIn(pattern, proc.stderr)

    def test_fred_cli_triage_missing_transcript_and_workspace_errors(self):
        fred_cli = ROOT / "scripts/fred.py"
        cmd = [sys.executable, str(fred_cli), "triage"]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Transcript file path is required", proc.stderr)

    def test_discovered_transcript_preserves_ledgers_and_scrubs_secrets(self):
        workspace = self.temp_path / "ws_security"
        workspace.mkdir()
        ledger_run = workspace / "run.json"
        ledger_run.write_text(
            '{"run_id": "test-run", "status": "active"}\n', encoding="utf-8"
        )
        ledger_audit = workspace / "audit.json"
        ledger_audit.write_text('{"findings": []}\n', encoding="utf-8")

        initial_run_content = ledger_run.read_text(encoding="utf-8")
        initial_audit_content = ledger_audit.read_text(encoding="utf-8")

        transcript = workspace / "session-transcript.jsonl"
        transcript.write_text(
            "Authorization: Bearer supersecretvalue1234567890\nCommand 'ripgrep' timed out after 30.0 seconds\n",
            encoding="utf-8",
        )

        fred_cli = ROOT / "scripts/fred.py"
        cmd = [sys.executable, str(fred_cli), "triage", "--workspace", str(workspace)]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        # Invariant: workspace ledgers must not be modified
        self.assertEqual(ledger_run.read_text(encoding="utf-8"), initial_run_content)
        self.assertEqual(
            ledger_audit.read_text(encoding="utf-8"), initial_audit_content
        )

        # Invariant: credentials must be scrubbed
        self.assertNotIn("supersecretvalue1234567890", proc.stdout)
        self.assertIn("[REDACTED_AUTH_TOKEN]", proc.stdout)

    def _isolated_home_env(self, home_dir: Path) -> dict[str, str]:
        import os

        env = dict(os.environ)
        env["HOME"] = str(home_dir)
        env["USERPROFILE"] = str(home_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def test_fred_cli_discovers_claude_code_transcript_from_run_json(self):
        import os

        from fredlib import runs

        repo_dir = self.temp_path / "sample_repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")

        workspace = self.temp_path / "stalled_claude_ws"
        run_info = runs.initialize(repo_dir, workspace, task="recover-audit")
        run_id = run_info["run_id"]

        initial_run_bytes = (workspace / "run.json").read_bytes()
        initial_audit_bytes = (workspace / "audit.json").read_bytes()

        fake_home = self.temp_path / "fake_home_claude"
        slug = (
            str(repo_dir.resolve())
            .replace("\\", "-")
            .replace("/", "-")
            .replace(":", "-")
        )
        claude_proj_dir = fake_home / ".claude" / "projects" / slug
        claude_proj_dir.mkdir(parents=True)

        # Unrelated project transcript (should be rejected)
        other_proj_dir = fake_home / ".claude" / "projects" / "-tmp-unrelated-repo"
        other_proj_dir.mkdir(parents=True)
        unrelated_jsonl = other_proj_dir / "unrelated-session.jsonl"
        unrelated_jsonl.write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "unrelated-session",
                    "cwd": "/tmp/unrelated-repo",
                    "message": {"role": "user", "content": "Unrelated audit"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(unrelated_jsonl, (5000, 5000))

        # Subagent sidechain transcript (should be rejected in favor of primary session)
        subagents_dir = claude_proj_dir / "claude-sess-1" / "subagents"
        subagents_dir.mkdir(parents=True)
        sidechain_jsonl = subagents_dir / "agent-sub1.jsonl"
        sidechain_jsonl.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "claude-sess-1",
                    "isSidechain": True,
                    "cwd": str(repo_dir.resolve()),
                    "message": {
                        "role": "assistant",
                        "content": f"Subagent for {run_id}",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(sidechain_jsonl, (4000, 4000))

        # Matching primary Claude Code session transcript
        matching_jsonl = claude_proj_dir / "claude-sess-1.jsonl"
        matching_lines = [
            {
                "type": "user",
                "sessionId": "claude-sess-1",
                "cwd": str(repo_dir.resolve()),
                "message": {
                    "role": "user",
                    "content": f"Execute audit run {run_id} in {workspace}",
                },
            },
            {
                "type": "assistant",
                "sessionId": "claude-sess-1",
                "cwd": str(repo_dir.resolve()),
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Running repo-audit-concurrency pass now.",
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "sessionId": "claude-sess-1",
                "isApiErrorMessage": True,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Prompt is too long: 215000 tokens > 200000 maximum",
                        }
                    ],
                },
            },
        ]
        matching_jsonl.write_text(
            "\n".join(json.dumps(rec) for rec in matching_lines) + "\n",
            encoding="utf-8",
        )
        os.utime(matching_jsonl, (3000, 3000))

        export_dir = self.temp_path / "claude_diag_out"
        fred_cli = ROOT / "scripts/fred.py"
        proc = subprocess.run(
            [
                sys.executable,
                str(fred_cli),
                "triage",
                "--workspace",
                str(workspace),
                "--output",
                str(export_dir),
                "--check-schema",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=self._isolated_home_env(fake_home),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(matching_jsonl), proc.stderr)

        bundle = json.loads(
            (export_dir / "diagnostic_bundle.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            bundle["failure_classification"]["failure_type"], "context_limit"
        )
        self.assertEqual(
            bundle["correlated_domain"]["specialist_skill"], "repo-audit-concurrency"
        )
        disc = bundle["transcript_discovery"]
        self.assertEqual(disc["source"], "claude-code")
        self.assertEqual(Path(disc["selected_transcript"]), matching_jsonl)
        self.assertIn(run_id, disc["selection_reason"])
        rejected_paths = {Path(item["path"]) for item in disc["rejected_candidates"]}
        self.assertIn(unrelated_jsonl, rejected_paths)
        self.assertIn(sidechain_jsonl, rejected_paths)

        report_md = (export_dir / "diagnostic_report.md").read_text(encoding="utf-8")
        self.assertIn("## Transcript discovery", report_md)
        self.assertIn("claude-code", report_md)
        self.assertIn("unrelated-session.jsonl", report_md)

        # Workspace ledgers must remain strictly unmodified
        self.assertEqual((workspace / "run.json").read_bytes(), initial_run_bytes)
        self.assertEqual((workspace / "audit.json").read_bytes(), initial_audit_bytes)

    def test_fred_cli_discovers_codex_transcript_via_run_json_and_session_id(self):
        import os

        from fredlib import runs

        repo_dir = self.temp_path / "codex_repo"
        repo_dir.mkdir()
        (repo_dir / "main.py").write_text("x = 1\n", encoding="utf-8")

        workspace = self.temp_path / "stalled_codex_ws"
        runs.initialize(repo_dir, workspace, task="recover-audit")

        fake_home = self.temp_path / "fake_home_codex"
        codex_day_dir = fake_home / ".codex" / "sessions" / "2026" / "11" / "18"
        codex_day_dir.mkdir(parents=True)

        target_sid = "019a9000-1111-7222-8333-444455556666"
        other_sid = "019a9000-9999-7222-8333-444455557777"

        target_rollout = (
            codex_day_dir / f"rollout-2026-11-18T10-00-00-{target_sid}.jsonl"
        )
        target_records = [
            {
                "type": "session_meta",
                "payload": {"id": target_sid, "cwd": str(repo_dir.resolve())},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Executing repo-audit-transactions checks.",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": json.dumps(
                        {
                            "output": "Command 'pytest' timed out after 60.0 seconds",
                            "metadata": {"exit_code": 124, "timed_out": True},
                        }
                    ),
                },
            },
        ]
        target_rollout.write_text(
            "\n".join(json.dumps(r) for r in target_records) + "\n",
            encoding="utf-8",
        )
        os.utime(target_rollout, (1000, 1000))

        # Newer session in a different repo (rejected by cwd when discovering by workspace,
        # and rejected by session_id when --session-id is supplied)
        other_rollout = codex_day_dir / f"rollout-2026-11-18T11-00-00-{other_sid}.jsonl"
        other_rollout.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": other_sid, "cwd": "/tmp/some-other-project"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(other_rollout, (2000, 2000))

        fred_cli = ROOT / "scripts/fred.py"
        env = self._isolated_home_env(fake_home)

        # 1. Discover automatically from workspace run.json repository.path
        proc_ws = subprocess.run(
            [
                sys.executable,
                str(fred_cli),
                "triage",
                "--workspace",
                str(workspace),
                "--check-schema",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc_ws.returncode, 0, proc_ws.stderr)
        bundle_ws = json.loads(proc_ws.stdout)
        self.assertEqual(bundle_ws["transcript_discovery"]["source"], "codex")
        self.assertEqual(
            Path(bundle_ws["transcript_discovery"]["selected_transcript"]),
            target_rollout,
        )
        self.assertEqual(
            bundle_ws["failure_classification"]["failure_type"], "tool_timeout"
        )
        self.assertEqual(
            bundle_ws["correlated_domain"]["specialist_skill"],
            "repo-audit-transactions",
        )

        # 2. Forward --session-id explicitly even when another newer transcript shares the repo
        newer_same_repo = (
            codex_day_dir / "rollout-2026-11-18T12-00-00-newer-same-repo.jsonl"
        )
        newer_same_repo.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "newer-same-repo",
                        "cwd": str(repo_dir.resolve()),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(newer_same_repo, (3000, 3000))

        proc_sid = subprocess.run(
            [
                sys.executable,
                str(fred_cli),
                "triage",
                "--workspace",
                str(workspace),
                "--session-id",
                target_sid,
                "--check-schema",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc_sid.returncode, 0, proc_sid.stderr)
        bundle_sid = json.loads(proc_sid.stdout)
        self.assertEqual(bundle_sid["session_id"], target_sid)
        self.assertEqual(
            Path(bundle_sid["transcript_discovery"]["selected_transcript"]),
            target_rollout,
        )
        rejected_reasons = {
            Path(item["path"]): item["reason"]
            for item in bundle_sid["transcript_discovery"]["rejected_candidates"]
        }
        self.assertIn(newer_same_repo, rejected_reasons)
        self.assertIn("session_id mismatch", rejected_reasons[newer_same_repo])

    def test_discover_transcript_host_mismatch_preserves_error_and_records_rejected(
        self,
    ):
        fake_home = self.temp_path / "fake_home_mismatch"
        codex_dir = fake_home / ".codex" / "sessions"
        codex_dir.mkdir(parents=True)
        wrong_host = codex_dir / "rollout-wrong.jsonl"
        wrong_host.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": "wrong-id", "cwd": "/tmp/other-repo"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        workspace = self.temp_path / "ws_no_match"
        workspace.mkdir()
        (workspace / "run.json").write_text(
            json.dumps(
                {
                    "run_id": "run-expected-999",
                    "repository": {"path": "/tmp/expected-repo"},
                }
            ),
            encoding="utf-8",
        )

        with (
            patch.dict(
                "os.environ",
                {"HOME": str(fake_home), "USERPROFILE": str(fake_home)},
            ),
            self.assertRaises(triage.TranscriptNotFoundError) as cm,
        ):
            triage.discover_transcript(workspace)

        msg = str(cm.exception)
        for pattern in triage.CANDIDATE_TRANSCRIPTS:
            self.assertIn(pattern, msg)
        self.assertEqual(len(cm.exception.rejected_candidates), 1)
        self.assertEqual(Path(cm.exception.rejected_candidates[0]["path"]), wrong_host)

    def test_detect_failures_claude_multiturn_prompt_too_long_over_earlier_timeout_prose(
        self,
    ):
        records = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Inspect TimeoutError handling with repo-audit-failure-modes.",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Earlier logs mentioned Command 'rg' timed out after 30 seconds and TimeoutError: worker stalled.",
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01",
                            "is_error": False,
                            "content": "def handle(): raise TimeoutError('simulated')",
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Now switching to repo-audit-data-integrity for ledger verification.",
                        }
                    ],
                },
            },
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long: 204800 tokens > 200000 maximum",
                },
            },
        ]
        transcript_text = "\n".join(json.dumps(r) for r in records) + "\n"

        failure = triage.detect_failures(transcript_text)
        self.assertEqual(failure["failure_type"], "context_limit")
        self.assertEqual(failure["line_number"], 5)
        self.assertIn("prompt is too long", failure["matched_line"].lower())

        correlated = triage.correlate_audit_domain(
            transcript_text, failure_line=failure["line_number"]
        )
        self.assertEqual(correlated["specialist_skill"], "repo-audit-data-integrity")

    def test_detect_failures_ignores_error_substring_in_hyphenated_skill_names_on_codex_jsonl(
        self,
    ):
        codex_records = [
            {
                "type": "session_meta",
                "payload": {"id": "sess-codex-err-sem", "cwd": "/app/project"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Run $repo-audit-error-semantics on src/handlers.py",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Loaded repo-audit-error-semantics and error-semantics checklist.",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_ok",
                    "output": "Checkedskills/repo-audit-error-semantics/SKILL.md successfully",
                },
            },
        ]
        stalled_jsonl = "\n".join(json.dumps(r) for r in codex_records) + "\n"
        failure = triage.detect_failures(stalled_jsonl)
        self.assertEqual(failure["failure_type"], "unclassified_failure")
        self.assertEqual(
            failure["summary"],
            "Session ended unexpectedly without an explicit error signature",
        )
        self.assertEqual(failure["line_number"], 4)

        # Plain-text transcript mentioning repo-audit-error-semantics also ignores hyphenated 'error'
        plain_stalled = (
            "[2026-11-18T10:00:00Z] Starting specialist repo-audit-error-semantics\n"
            "[2026-11-18T10:00:05Z] Completed pass for error-semantics\n"
        )
        plain_failure = triage.detect_failures(plain_stalled)
        self.assertEqual(
            plain_failure["summary"],
            "Session ended unexpectedly without an explicit error signature",
        )

    def test_correlate_audit_domain_prefers_latest_specialist_before_failure_line(self):
        # repo-audit-behavioral-contracts appears first in KNOWN_SPECIALISTS,
        # repo-audit-observability is active right before failure line 4,
        # and repo-audit-synthesis appears only after the failure line.
        transcript = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "Use these skills in order: repo-audit-behavioral-contracts, repo-audit-invariants, repo-audit-observability",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": "Completed repo-audit-behavioral-contracts and repo-audit-invariants.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": "Starting specialist repo-audit-observability on telemetry module.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "t9",
                                    "is_error": True,
                                    "content": "Command 'pytest' timed out after 30 seconds",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": "Deferred repo-audit-synthesis until timeout is resolved.",
                        },
                    }
                ),
            ]
        )
        correlated = triage.correlate_audit_domain(transcript)
        self.assertEqual(correlated["specialist_skill"], "repo-audit-observability")
        self.assertEqual(correlated["topic"], "observability")

    def test_scrub_credentials_json_escaped_tool_results_uri_and_tiers(self):
        import jsonschema

        escaped_payload = json.dumps(
            {
                "api_key": "json-field-secret-value-123",
                "client_secret": "oauth-client-secret-456",
                "dsn": "postgresql://dbadmin:MyUriP@ssw0rd!@db.internal.example:5432/auditdb",
                "cache_url": "redis://:RedisSecret999@cache.internal:6379/0",
                "PROPRIETARY_CODE": "class BillingEngine: SECRET_MULTIPLIER = 42",
            }
        )
        records = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Diagnose repo https://deploy_user:GitPatSecret789@github.com/org/private.git",
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_non_cited",
                            "is_error": False,
                            "content": escaped_payload,
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "usage": {"input_tokens": 4096, "output_tokens": 512},
                    "content": [
                        {
                            "type": "text",
                            "text": "Running repo-audit-configuration verification with E-1.",
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_cited_failure",
                            "is_error": True,
                            "content": "Command timed out after 45 seconds while connecting to mysql://svc:CitedDbSecret!@127.0.0.1:3306/prod",
                        }
                    ],
                },
            },
        ]
        raw_jsonl = "\n".join(json.dumps(r) for r in records) + "\n"
        transcript_file = self.temp_path / "tiered_session.jsonl"
        transcript_file.write_text(raw_jsonl, encoding="utf-8")

        secrets = (
            "json-field-secret-value-123",
            "oauth-client-secret-456",
            "MyUriP@ssw0rd!",
            "RedisSecret999",
            "GitPatSecret789",
            "CitedDbSecret!",
        )

        # 1. Full tier: scrubs all secrets, preserves both tool outputs and numeric token usage
        scrubbed_full = triage.scrub_credentials(raw_jsonl, tier="full")
        for secret in secrets:
            self.assertNotIn(secret, scrubbed_full)
        self.assertIn("PROPRIETARY_CODE", scrubbed_full)
        self.assertIn("Command timed out after 45 seconds", scrubbed_full)
        self.assertIn('"input_tokens": 4096', scrubbed_full)

        # 2. Evidence tier: strips non-cited tool_result (line 2), keeps cited failure tool_result (line 4) with secrets scrubbed
        scrubbed_evidence = triage.scrub_credentials(raw_jsonl, tier="evidence")
        for secret in secrets:
            self.assertNotIn(secret, scrubbed_evidence)
        self.assertNotIn("PROPRIETARY_CODE", scrubbed_evidence)
        self.assertIn("[OMITTED_TOOL_OUTPUT]", scrubbed_evidence)
        self.assertIn("Command timed out after 45 seconds", scrubbed_evidence)
        self.assertIn("[REDACTED_URI_PASSWORD]", scrubbed_evidence)

        # 3. Skeleton tier: strips ALL tool_result bodies and scrubs remaining secrets
        scrubbed_skeleton = triage.scrub_credentials(raw_jsonl, tier="skeleton")
        for secret in secrets:
            self.assertNotIn(secret, scrubbed_skeleton)
        self.assertNotIn("PROPRIETARY_CODE", scrubbed_skeleton)
        self.assertNotIn("Command timed out after 45 seconds", scrubbed_skeleton)

        # 4. End-to-end CLI export with --redaction-tier evidence and skeleton + schema validation
        validator = jsonschema.Draft202012Validator(SCHEMA)
        fred_cli = ROOT / "scripts/fred.py"
        for tier_name, expected_omitted in (
            ("full", 0),
            ("evidence", 1),
            ("skeleton", 2),
        ):
            out_dir = self.temp_path / f"export_{tier_name}"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(fred_cli),
                    "triage",
                    str(transcript_file),
                    "--output",
                    str(out_dir),
                    "--redaction-tier",
                    tier_name,
                    "--check-schema",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            bundle = json.loads(
                (out_dir / "diagnostic_bundle.json").read_text(encoding="utf-8")
            )
            errors = list(validator.iter_errors(bundle))
            self.assertEqual([], errors, [e.message for e in errors])
            self.assertEqual(bundle["redaction"]["tier"], tier_name)
            self.assertEqual(
                bundle["redaction"]["tool_outputs_omitted"], expected_omitted
            )
            self.assertGreaterEqual(bundle["redaction"]["credentials_redacted"], 1)

            for secret in secrets:
                self.assertNotIn(secret, json.dumps(bundle, ensure_ascii=False))

            report_md = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")
            self.assertIn(f"**Redaction Tier:** {tier_name}", report_md)
            self.assertIn(f"**Tool Outputs Omitted:** {expected_omitted}", report_md)
            self.assertIn("**Credentials Redacted:**", report_md)
            self.assertIn("**Total Redactions:**", report_md)

    def test_known_specialists_cover_all_51_catalog_skills_without_false_audit_matches(
        self,
    ):
        catalog_skills = json.loads(
            (ROOT / "catalog/skills.json").read_text(encoding="utf-8")
        )["skills"]
        expected_names = {item["name"] for item in catalog_skills}
        self.assertEqual(57, len(expected_names))
        self.assertEqual(expected_names, set(triage.KNOWN_SPECIALISTS))

        # Every single catalog skill correlates cleanly before any keyword fallback
        for item in catalog_skills:
            skill_name = item["name"]
            with self.subTest(skill=skill_name):
                transcript = (
                    f"[2026-11-18T12:00:00Z] Activating skill {skill_name} on repository architecture and test suite\n"
                    f"[2026-11-18T12:00:05Z] Command 'pytest' timed out after 30.0 seconds\n"
                )
                corr = triage.correlate_audit_domain(transcript)
                self.assertEqual(skill_name, corr["specialist_skill"])

        # arch-decision-record transcripts (structured JSONL and plain text) never false-match repo-audit-architecture
        adr_records = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Design system architecture using arch-decision-record and write architecture.json.",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "Inspecting architecture boundaries and recording ADR-001 in architecture.json against architecture.schema.json.",
                },
            },
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long: 210000 tokens > 200000 maximum",
                },
            },
        ]
        adr_jsonl = "\n".join(json.dumps(r) for r in adr_records) + "\n"
        corr_jsonl = triage.correlate_audit_domain(adr_jsonl)
        self.assertEqual("arch-decision-record", corr_jsonl["specialist_skill"])
        self.assertNotEqual("repo-audit-architecture", corr_jsonl["specialist_skill"])
        self.assertIn("ADR-001", corr_jsonl["ledger_references"])
        corr_jsonl_with_doc = triage.correlate_audit_domain(
            adr_jsonl,
            audit_data={
                "schema_version": "1.0",
                "title": "Core Architecture",
                "status": "proposed",
                "boundaries": [],
                "decisions": [
                    {
                        "id": "ADR-001",
                        "title": "Boundary split",
                        "context": "Isolation",
                        "decision": "Split",
                        "consequences": ["Safe"],
                    }
                ],
            },
        )
        self.assertIn("decisions:ADR-001", corr_jsonl_with_doc["ledger_references"])

        # Even without an explicit skill name, a software-architecture workspace does not false-match repo-audit-architecture
        corr_no_skill = triage.correlate_audit_domain(
            "Drafting architecture.json against architecture.schema.json for ADR-002\n",
            audit_data={
                "schema_version": "1.0",
                "title": "Core Architecture",
                "status": "proposed",
                "boundaries": [],
                "decisions": [],
            },
        )
        self.assertIsNone(corr_no_skill["specialist_skill"])
        self.assertEqual("architecture", corr_no_skill["topic"])

    def test_fred_cli_triage_domain_suites_correlate_ids_and_emit_suite_recovery(self):
        import shutil

        from fredlib import repository, runs

        if not shutil.which("git"):
            self.skipTest("Git unavailable")
        repo_dir = self.temp_path / "domain_triage_repo"
        repo_dir.mkdir()
        (repo_dir / "service.py").write_text("def handle(x):\n    return x\n")
        for argv in [
            ["init", "-q"],
            ["config", "user.name", "Fixture"],
            ["config", "user.email", "fixture@example.invalid"],
            ["add", "."],
            ["commit", "-qm", "initial"],
        ]:
            subprocess.run(["git", "-C", str(repo_dir), *argv], check=True)
        base_rev = repository.git(repo_dir, "rev-parse", "HEAD", required=True)

        cases = [
            (
                "design-architecture",
                None,
                "arch-decision-record",
                "architecture.json",
                "shared/software-architecture/resources/assets/architecture.schema.json",
                {
                    "schema_version": "1.0",
                    "title": "Order Service Architecture",
                    "status": "proposed",
                    "boundaries": [
                        {
                            "name": "PaymentGateway",
                            "type": "trust",
                            "description": "External settlement boundary",
                            "invariants": ["Idempotent charge keys"],
                        }
                    ],
                    "decisions": [
                        {
                            "id": "ADR-001",
                            "title": "Outbox event relay",
                            "context": "Avoid dual writes",
                            "decision": "Transactional outbox table",
                            "consequences": ["At-least-once delivery"],
                        }
                    ],
                },
                "Update ADR-001 across PaymentGateway in architecture.json\nAdditional properties are not allowed ('run_id' was unexpected)\n",
                ["decisions:ADR-001", "boundaries:PaymentGateway"],
            ),
            (
                "review-code-changes",
                base_rev,
                "review-diff-analysis",
                "review.json",
                "shared/code-review/resources/assets/review.schema.json",
                {
                    "schema_version": "1.0",
                    "verdict": "request_changes",
                    "summary": "Diff introduces unchecked cast",
                    "findings": [
                        {
                            "id": "REV-1",
                            "title": "Unchecked cast in handler",
                            "severity": "high",
                            "file": "service.py",
                            "line": 1,
                            "description": "Raises TypeError on None",
                        }
                    ],
                },
                "Recording REV-1 in review.json\n'verdict' is a required property\n",
                ["findings:REV-1"],
            ),
            (
                "expand-automated-tests",
                None,
                "test-case-generation",
                "testing.json",
                "shared/automated-testing/resources/assets/testing.schema.json",
                {
                    "schema_version": "1.0",
                    "component": "service.handle",
                    "framework": "pytest",
                    "test_cases": [
                        {
                            "id": "TC-1",
                            "name": "test_handle_none",
                            "description": "Verifies None input behavior",
                            "oracle_type": "return_value",
                            "verified": False,
                        }
                    ],
                },
                "Generating TC-1 in testing.json\n'oracle_type' is a required property\n",
                ["test_cases:TC-1"],
            ),
            (
                "specify-feature",
                None,
                "spec-authoring",
                "spec.json",
                "shared/spec-driven-development/resources/assets/spec.schema.json",
                {
                    "schema_version": "1.0",
                    "title": "Streaming Export Spec",
                    "status": "draft",
                    "summary": "Spec summary",
                    "interview_decisions": [
                        {
                            "id": "DEC-1",
                            "category": "architecture",
                            "question": "Pagination?",
                            "decision": "Cursor",
                            "rationale": "Bounded memory",
                        }
                    ],
                    "requirements": [
                        {
                            "id": "REQ-1",
                            "description": "Stream batches",
                            "priority": "must",
                        }
                    ],
                    "new_file_scopes": [],
                    "out_of_scope": [],
                    "verification_commands": [],
                    "convergence_checks": [],
                },
                "Authoring REQ-1 in spec.json after DEC-1\n'description' is a required property\n",
                ["interview_decisions:DEC-1", "requirements:REQ-1"],
            ),
        ]

        fred_cli = ROOT / "scripts/fred.py"
        for (
            task,
            base_arg,
            skill_name,
            filename,
            schema_path,
            payload,
            error_lines,
            expected_refs,
        ) in cases:
            with self.subTest(task=task):
                ws = self.temp_path / f"ws_{task}"
                runs.initialize(repo_dir, ws, task=task, base=base_arg)
                artifact_file = ws / filename
                artifact_file.write_text(
                    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
                )
                before_bytes = artifact_file.read_bytes()

                transcript = ws / "session-transcript.jsonl"
                transcript.write_text(
                    f"Starting skill {skill_name}\n{error_lines}",
                    encoding="utf-8",
                )
                out_dir = self.temp_path / f"diag_{task}"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(fred_cli),
                        "triage",
                        "--workspace",
                        str(ws),
                        "--output",
                        str(out_dir),
                        "--check-schema",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual(before_bytes, artifact_file.read_bytes())

                bundle = json.loads(
                    (out_dir / "diagnostic_bundle.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    "schema_validation_error",
                    bundle["failure_classification"]["failure_type"],
                )
                self.assertEqual(
                    skill_name, bundle["correlated_domain"]["specialist_skill"]
                )
                for ref in expected_refs:
                    self.assertIn(ref, bundle["correlated_domain"]["ledger_references"])

                instructions_blob = "\n".join(bundle["recovery"]["instructions"])
                self.assertIn("validate-run", instructions_blob)
                self.assertIn(filename, instructions_blob)
                self.assertIn(schema_path, instructions_blob)
                self.assertNotIn("validate_audit.py", instructions_blob)
                self.assertNotIn("audit.json", instructions_blob)
                if task == "expand-automated-tests":
                    self.assertIn("--allow-fixes", instructions_blob)

    def test_domain_schema_patterns_topic_stripping_and_recovery_edge_cases(self):
        # 1. SCHEMA_PATTERNS matches required-property, enum, unexpected-property, duplicate-ID, and path errors
        schema_error_lines = [
            "'custom_field' is a required property",
            "'proposed' is not one of ['draft', 'accepted', 'superseded', 'deprecated']",
            "'return_value' is not one of ['state', 'exception', 'output', 'boundary']",
            "'2.0' was expected; '1.0' was expected",
            "Additional properties are not allowed ('run_id', 'extra' were unexpected)",
            "Duplicate IDs in decisions: ADR-001",
            "Duplicate IDs in findings: REV-1",
            "Duplicate IDs in test_cases: TC-1",
            "Finding file path must be repository-relative (REV-1): Unsafe relative path: '../secret.py'",
            "Finding file path must be repository-relative (REV-2): Expected a nonempty repository-relative POSIX path",
        ]
        for err_line in schema_error_lines:
            with self.subTest(error_line=err_line):
                failure = triage.detect_failures(
                    f"Validating deliverable\n{err_line}\n"
                )
                self.assertEqual("schema_validation_error", failure["failure_type"])

        # 2. Suite-prefix stripping for topic across all 6 suites
        expected_topics = {
            "repo-audit-concurrency": "concurrency",
            "repo-verify-finding": "verify-finding",
            "arch-decision-record": "decision-record",
            "arch-boundary-specification": "boundary-specification",
            "review-diff-analysis": "diff-analysis",
            "review-actionable-feedback": "actionable-feedback",
            "test-gap-analysis": "gap-analysis",
            "test-suite-verification": "suite-verification",
            "spec-authoring": "authoring",
            "spec-convergence": "convergence",
        }
        for skill_name, expected_topic in expected_topics.items():
            with self.subTest(skill=skill_name):
                corr = triage.correlate_audit_domain(
                    f"Executing {skill_name}\nTimeoutError: timed out\n"
                )
                self.assertEqual(skill_name, corr["specialist_skill"])
                self.assertEqual(expected_topic, corr["topic"])

        # 3. Keyword fallback is scoped to audit runs even when audit_data is None or malformed
        for domain_transcript, expected_topic in [
            (
                "Inspecting system architecture boundaries and writing architecture.json\n",
                "architecture",
            ),
            (
                "Checking diff invariants and compatibility in review.json\n",
                "code-review",
            ),
            (
                "Checking test-oracles and nondeterminism in testing.json\n",
                "automated-testing",
            ),
            (
                "Authoring requirements and interview decisions in spec.json\n",
                "spec",
            ),
        ]:
            corr_domain = triage.correlate_audit_domain(domain_transcript)
            self.assertIsNone(corr_domain["specialist_skill"])
            self.assertEqual(expected_topic, corr_domain["topic"])

        # 4. Token-boundary ID matching prevents ADR-1/REV-1/TC-1 from false-matching ADR-10/REV-10/TC-10
        corr_prefix = triage.correlate_audit_domain(
            "Updated ADR-10 and TC-10 after reviewing REV-10\n",
            audit_data={
                "schema_version": "1.0",
                "title": "Arch",
                "status": "draft",
                "decisions": [
                    {"id": "ADR-1", "title": "First", "decision": "Keep"},
                    {"id": "ADR-10", "title": "Tenth", "decision": "Split"},
                ],
            },
        )
        self.assertEqual(["decisions:ADR-10"], corr_prefix["ledger_references"])

        # 5. Recovery keeps audit.json commands unchanged when workspace_dir is omitted or contains audit.json
        schema_failure = {
            "failure_type": "schema_validation_error",
            "line_number": 2,
            "matched_line": "'verdict' is a required property",
        }
        no_ws_rec = triage.formulate_recovery_instructions(
            schema_failure,
            run_data=None,
            workspace_dir=None,
            specialist_skill="review-diff-analysis",
        )
        no_ws_blob = "\n".join(no_ws_rec["instructions"])
        self.assertIn("validate_audit.py <audit-run-dir>/audit.json", no_ws_blob)
        self.assertNotIn("validate-run", no_ws_blob)

        audit_ws = self.temp_path / "ws_has_audit_json"
        audit_ws.mkdir()
        (audit_ws / "audit.json").write_text('{"findings": []}\n', encoding="utf-8")
        audit_ws_rec = triage.formulate_recovery_instructions(
            schema_failure,
            run_data=None,
            workspace_dir=audit_ws,
            specialist_skill="arch-decision-record",
        )
        audit_ws_blob = "\n".join(audit_ws_rec["instructions"])
        self.assertIn(f"validate_audit.py {audit_ws}/audit.json", audit_ws_blob)
        self.assertNotIn("architecture.json", audit_ws_blob)

        # 6. Workspace-file-only resolution (no run.json) resolves active domain deliverable
        for fname in ("architecture.json", "review.json", "testing.json", "spec.json"):
            file_only_ws = self.temp_path / f"ws_file_only_{fname}"
            file_only_ws.mkdir()
            (file_only_ws / fname).write_text("{}\n", encoding="utf-8")
            rec = triage.formulate_recovery_instructions(
                schema_failure,
                run_data=None,
                workspace_dir=file_only_ws,
                specialist_skill=None,
            )
            blob = "\n".join(rec["instructions"])
            self.assertIn(f"python scripts/fred.py validate-run {file_only_ws}", blob)
            self.assertIn(fname, blob)
            self.assertNotIn("validate_audit.py", blob)

    def test_recovery_shell_quotes_workspace_paths_and_report_expands_fences(self):
        import shlex

        spaced_ws = self.temp_path / "workspace with spaces & $vars"
        spaced_ws.mkdir()
        (spaced_ws / "audit.json").write_text('{"findings": []}\n', encoding="utf-8")

        breakout_line = (
            "ValidationError: bad payload ```\n# Injected Heading\n``` trailing"
        )
        failure = {
            "failure_type": "schema_validation_error",
            "summary": "Unclassified execution failure: ```\n# Injected Summary",
            "line_number": 9,
            "matched_line": breakout_line,
            "details": {},
        }
        rec = triage.formulate_recovery_instructions(
            failure, run_data=None, workspace_dir=spaced_ws
        )
        expected_ws_quoted = shlex.quote(str(spaced_ws))
        expected_audit_quoted = shlex.quote(f"{spaced_ws}/audit.json")
        joined_steps = "\n".join(rec["instructions"])
        self.assertIn(f"validate_audit.py {expected_audit_quoted}", joined_steps)
        self.assertIn(
            f"python scripts/fred.py resume {expected_ws_quoted}", joined_steps
        )
        # Step 1 wraps matched_line containing ``` inside a 4-backtick single-line code span
        self.assertIn(
            r"````ValidationError: bad payload ```\n# Injected Heading\n``` trailing````",
            rec["instructions"][0],
        )

        bundle = {
            "schema_version": "1.0",
            "session_id": "sess-fence",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "failure_classification": failure,
            "correlated_domain": {
                "specialist_skill": "repo-audit-concurrency",
                "topic": "concurrency",
                "ledger_references": [],
            },
            "recovery": rec,
            "redaction": {
                "tier": "full",
                "credentials_redacted": 0,
                "tool_outputs_omitted": 0,
                "total_redactions": 0,
            },
            "transcript_discovery": {
                "source": "explicit",
                "selected_transcript": "/tmp/log`tick`/session.jsonl",
                "selection_reason": "explicit path",
                "rejected_candidates": [],
            },
        }
        report_md = triage.render_report(bundle)
        self.assertIn(f"````\n{breakout_line}\n````", report_md)
        self.assertIn("``/tmp/log`tick`/session.jsonl``", report_md)
        self.assertNotIn("\n# Injected Summary", report_md)

        # Benign Windows runner temp paths (drive colon, backslashes, 8.3 '~') stay unquoted,
        # while Windows paths containing spaces or shell metacharacters are quoted via shlex.quote.
        from pathlib import PurePosixPath, PureWindowsPath

        win_clean_ws = PureWindowsPath(
            r"C:\Users\RUNNER~1\AppData\Local\Temp\tmp1234\ws_has_audit_json"
        )
        self.assertEqual(
            f"{win_clean_ws}/audit.json",
            triage._format_workspace_arg(win_clean_ws, "<audit-run-dir>", "audit.json"),
        )
        win_spaced_ws = PureWindowsPath(
            r"C:\Users\RUNNER~1\AppData\Local\Temp\workspace with spaces & $vars"
        )
        self.assertEqual(
            shlex.quote(f"{win_spaced_ws}/audit.json"),
            triage._format_workspace_arg(
                win_spaced_ws, "<audit-run-dir>", "audit.json"
            ),
        )
        posix_backslash_ws = PurePosixPath("/tmp/dir\\with\\backslash")
        self.assertEqual(
            shlex.quote(f"{posix_backslash_ws}/audit.json"),
            triage._format_workspace_arg(
                posix_backslash_ws, "<audit-run-dir>", "audit.json"
            ),
        )
        win_rec = triage.formulate_recovery_instructions(
            failure,
            run_data=None,
            workspace_dir=win_clean_ws,
        )
        win_steps = "\n".join(win_rec["instructions"])
        self.assertIn(f"validate_audit.py {win_clean_ws}/audit.json", win_steps)
        self.assertIn(f"python scripts/fred.py resume {win_clean_ws}", win_steps)

    def test_triage_catalog_and_payload_parity_with_common_and_standalone_fallback(
        self,
    ) -> None:
        import ast

        import skill_tools
        from fredlib import common

        # 1. Standalone triage.py must not import fredlib or skill_tools
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name.startswith(("fredlib", "skill_tools")),
                        f"Forbidden import in triage.py: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith(("fredlib", "skill_tools")),
                    f"Forbidden import in triage.py: {node.module}",
                )

        # 2. Suite skills, schema paths, and task maps match catalog/*.json and fredlib.common
        expected_suite_skills = skill_tools.suite_skills_by_suite(ROOT)
        self.assertIsNotNone(expected_suite_skills)
        assert expected_suite_skills is not None
        self.assertEqual(6, len(expected_suite_skills))
        self.assertEqual(expected_suite_skills, triage._DEFAULT_SUITE_SKILLS)
        self.assertEqual(expected_suite_skills, triage._SUITE_SKILLS)
        self.assertEqual(common._skill_to_suite_map(ROOT), triage._SKILL_TO_SUITE)

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        expected_suite_artifacts = {
            s["name"]: (s["artifact"]["filename"], s["artifact"]["schema"])
            for s in suites_doc["suites"]
        }
        self.assertEqual(6, len(expected_suite_artifacts))
        self.assertEqual(expected_suite_artifacts, common.SUITE_DELIVERABLES)
        self.assertEqual(expected_suite_artifacts, triage._DEFAULT_SUITE_ARTIFACTS)
        self.assertEqual(expected_suite_artifacts, triage._SUITE_ARTIFACTS)
        self.assertEqual(
            common.DOMAIN_DELIVERABLE_SCHEMAS,
            {
                fname: schema_rel
                for fname, (_suite, schema_rel) in triage._DOMAIN_ARTIFACTS.items()
            },
        )

        expected_tasks = common._task_to_suite_map(ROOT)
        self.assertEqual(18, len(expected_tasks))
        self.assertEqual(expected_tasks, triage._DEFAULT_TASK_TO_SUITE)
        self.assertEqual(expected_tasks, triage._TASK_TO_SUITE)

        for task_name, expected_suite in expected_tasks.items():
            with self.subTest(task=task_name):
                run_doc = {"plan": {"task": task_name}}
                self.assertEqual(
                    expected_suite,
                    common._suite_for_run(run_doc, root=ROOT),
                )
                self.assertEqual(
                    expected_suite,
                    triage._suite_from_run_data(run_doc),
                )

        # 3. _detect_artifact_suite matches fredlib.common._detect_artifact_suite across payloads
        representative_payloads: list[tuple[str, object, str | None]] = [
            ("none", None, None),
            ("empty", {}, None),
            (
                "audit_starter",
                suites_doc["suites"][0]["artifact"]["starter"],
                None,
            ),
            (
                "audit_with_summary_and_evidence",
                {
                    "schema_version": "1.0",
                    "summary": "Audit summary",
                    "findings": [],
                    "evidence": [],
                },
                None,
            ),
            (
                "audit_with_run_id",
                {"run_id": "run-1", "findings": []},
                None,
            ),
            ("run_json", {"run_id": "run-1", "plan": {"task": "review-pr"}}, None),
            (
                "arch_starter",
                suites_doc["suites"][2]["artifact"]["starter"],
                "software-architecture",
            ),
            (
                "arch_minimal",
                {"title": "System Arch", "status": "draft"},
                "software-architecture",
            ),
            (
                "review_starter",
                suites_doc["suites"][3]["artifact"]["starter"],
                "code-review",
            ),
            (
                "review_minimal",
                {"summary": "Diff review", "findings": []},
                "code-review",
            ),
            (
                "testing_starter",
                suites_doc["suites"][4]["artifact"]["starter"],
                "automated-testing",
            ),
            (
                "testing_minimal",
                {"component": "auth", "framework": "unittest"},
                "automated-testing",
            ),
            (
                "spec_starter",
                suites_doc["suites"][5]["artifact"]["starter"],
                "spec-driven-development",
            ),
            (
                "spec_minimal",
                {"interview_decisions": [], "new_file_scopes": []},
                "spec-driven-development",
            ),
            (
                "finding_verification",
                {
                    "reproduction_status": "supported",
                    "safe_control_outcomes": [],
                    "confidence_rating": "high",
                },
                "repository-workflows",
            ),
            (
                "regression",
                {"failure_boundaries": ["race"]},
                "repository-workflows",
            ),
            (
                "plan",
                {"task_breakdowns": [], "dependency_order": []},
                "repository-workflows",
            ),
            (
                "fix",
                {"file_changes": ["a.py"], "diff_summaries": ["fix"]},
                "repository-workflows",
            ),
            (
                "verification",
                {"original_reproduced": True, "regression_passed": True},
                "repository-workflows",
            ),
            (
                "reconciliation",
                {"run_id": "run-1", "finding_fingerprint_matches": []},
                "repository-workflows",
            ),
            (
                "remediation",
                {"records": []},
                "repository-workflows",
            ),
        ]
        self.assertEqual(common._DOMAIN_KIND_KEY_SETS, triage._DOMAIN_KIND_KEY_SETS)
        for label, payload, expected_suite_or_none in representative_payloads:
            with self.subTest(payload=label):
                common_suite = common._detect_artifact_suite(payload)
                triage_suite = triage._detect_artifact_suite(payload)
                self.assertEqual(expected_suite_or_none, common_suite)
                self.assertEqual(common_suite, triage_suite)

    def test_triage_live_catalog_aliases_and_standalone_claude_skills_execution(
        self,
    ) -> None:
        from fredlib import common

        # 1. Live catalog loading resolves custom recipe aliases identically to fredlib.common
        synthetic_root = self.temp_path / "synthetic_repo"
        catalog_dir = synthetic_root / "catalog"
        catalog_dir.mkdir(parents=True)
        (catalog_dir / "suites.json").write_bytes(
            (ROOT / "catalog/suites.json").read_bytes()
        )
        recipes_doc = json.loads(
            (ROOT / "catalog/recipes.json").read_text(encoding="utf-8")
        )
        recipes_doc["recipes"]["review-code-changes"]["aliases"] = ["review-changes"]
        recipes_doc["recipes"]["expand-automated-tests"]["aliases"] = [
            "strengthen-tests"
        ]
        (catalog_dir / "recipes.json").write_text(
            json.dumps(recipes_doc, indent=2) + "\n", encoding="utf-8"
        )

        common_tasks = common._task_to_suite_map(synthetic_root)
        _, live_artifacts, live_tasks = triage._load_catalog_metadata(catalog_dir)
        self.assertEqual(common_tasks, live_tasks)
        self.assertEqual("code-review", live_tasks["review-changes"])
        self.assertEqual("automated-testing", live_tasks["strengthen-tests"])
        self.assertEqual(
            "code-review",
            common._suite_for_run(
                {"plan": {"task": "review-changes"}}, root=synthetic_root
            ),
        )
        self.assertEqual(
            "automated-testing",
            common._suite_for_run(
                {"plan": {"task": "strengthen-tests"}}, root=synthetic_root
            ),
        )
        self.assertEqual(triage._DEFAULT_SUITE_ARTIFACTS, live_artifacts)

        # Missing or malformed catalog falls back cleanly to _DEFAULT_*
        fb_skills, fb_artifacts, fb_tasks = triage._load_catalog_metadata(
            self.temp_path / "nonexistent_catalog"
        )
        self.assertEqual(triage._DEFAULT_SUITE_SKILLS, fb_skills)
        self.assertEqual(triage._DEFAULT_SUITE_ARTIFACTS, fb_artifacts)
        self.assertEqual(triage._DEFAULT_TASK_TO_SUITE, fb_tasks)

        # 2. Standalone execution inside an installed .claude/skills/ copy (no catalog/ or PYTHONPATH)
        installed_script = (
            self.temp_path / ".claude/skills/repo-audit-diagnostics/scripts/triage.py"
        )
        installed_script.parent.mkdir(parents=True)
        installed_script.write_bytes(SCRIPT.read_bytes())

        ws_dir = self.temp_path / "standalone_review_ws"
        ws_dir.mkdir()
        (ws_dir / "run.json").write_text(
            json.dumps(
                {"run_id": "run-standalone", "plan": {"task": "review-code-changes"}}
            ),
            encoding="utf-8",
        )
        transcript = ws_dir / "transcript.log"
        transcript.write_text(
            "Validating review.json\n'verdict' is a required property\n",
            encoding="utf-8",
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [
                sys.executable,
                str(installed_script),
                str(transcript),
                "--workspace",
                str(ws_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(self.temp_path),
            env=env,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        bundle = json.loads(proc.stdout)
        instructions_blob = "\n".join(bundle["recovery"]["instructions"])
        self.assertIn(
            "shared/code-review/resources/assets/review.schema.json",
            instructions_blob,
        )

    def test_scripts_delegate_suites_json_walking_to_skill_tools(self) -> None:
        delegated_files = (
            ROOT / "scripts/fredlib/common.py",
            ROOT / "scripts/fredlib/evaluations.py",
            ROOT / "scripts/fredlib/releases.py",
            ROOT / "scripts/fredlib/runs.py",
            ROOT / "scripts/validate_skills.py",
        )
        for path in delegated_files:
            with self.subTest(script=path.relative_to(ROOT).as_posix()):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn('for phase in suite.get("phases"', content)
                self.assertNotIn('for phase in suite["phases"]', content)
                self.assertNotIn('sorted(suite["phases"]', content)

        self.assertNotIn(
            "def _skill_to_suite_map(",
            (ROOT / "scripts/fredlib/evaluations.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "def _infer_suite_name(",
            (ROOT / "scripts/scaffold_skill.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "suite_skills_by_suite",
            (ROOT / "scripts/scaffold_skill.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "def _fallback_suite_name(",
            (ROOT / "scripts/validate_skills.py").read_text(encoding="utf-8"),
        )

    def test_triage_surfaces_warnings_for_corrupted_workspace_json_and_unreadable_transcripts(
        self,
    ) -> None:
        import io

        # 1. Truncated run.json and audit.json emit scrubbed stderr warnings and schema-valid recovery note
        crashed_ws = self.temp_path / "crashed_ws"
        crashed_ws.mkdir()
        (crashed_ws / "run.json").write_text(
            '{\n  "run_id": "run-crash",\n  "api_key": "sk-secret1234567890abcdef12345"\n',
            encoding="utf-8",
        )
        (crashed_ws / "audit.json").write_text(
            '{\n  "schema_version": "1.0",\n  "findings": [\n',
            encoding="utf-8",
        )
        transcript = crashed_ws / "transcript.txt"
        transcript.write_text(
            "Executing repo-audit-concurrency\nTimeoutError: query timed out\n",
            encoding="utf-8",
        )

        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            bundle = triage.generate_diagnostic_bundle(transcript, crashed_ws)
        stderr_out = err_buf.getvalue()
        self.assertIn("Warning: malformed JSON in workspace run.json", stderr_out)
        self.assertIn("Warning: malformed JSON in workspace audit.json", stderr_out)
        self.assertNotIn("sk-secret1234567890abcdef12345", stderr_out)
        triage.validate_bundle(bundle)
        instructions_blob = "\n".join(bundle["recovery"]["instructions"])
        self.assertIn("Repair corrupted workspace metadata", instructions_blob)
        self.assertIn("malformed JSON in workspace run.json", instructions_blob)

        # 2. Unreadable candidate transcript logs stderr warning and records rejected_candidates reason
        disc_ws = self.temp_path / "unreadable_disc_ws"
        (disc_ws / "logs").mkdir(parents=True)
        unreadable_cand = disc_ws / "transcript.jsonl"
        unreadable_cand.write_text(
            '{"role": "user", "content": "hi"}\n', encoding="utf-8"
        )
        readable_fallback = disc_ws / "logs" / "transcript.txt"
        readable_fallback.write_text(
            "TimeoutError: fallback transcript\n", encoding="utf-8"
        )

        orig_read_text = Path.read_text

        def fake_read_text(p: Path, *args: Any, **kwargs: Any) -> str:
            if p == unreadable_cand:
                raise PermissionError("Permission denied")
            return orig_read_text(p, *args, **kwargs)

        fake_home = self.temp_path / "empty_home"
        fake_home.mkdir()
        disc_err = io.StringIO()
        with (
            patch.object(Path, "read_text", autospec=True, side_effect=fake_read_text),
            contextlib.redirect_stderr(disc_err),
        ):
            details = triage.discover_transcript_details(
                workspace_dir=disc_ws, home_dir=fake_home
            )
        self.assertEqual(readable_fallback, details["selected_transcript"])
        self.assertIn(
            "Warning: unable to read candidate transcript", disc_err.getvalue()
        )
        self.assertIn(str(unreadable_cand), disc_err.getvalue())
        rejected_by_path = {
            Path(item["path"]): item["reason"]
            for item in details["rejected_candidates"]
        }
        self.assertIn(unreadable_cand, rejected_by_path)
        self.assertIn("unreadable transcript", rejected_by_path[unreadable_cand])

        # Also verify real chmod(0o000) behavior on non-root POSIX hosts
        if sys.platform != "win32" and os.geteuid() != 0:
            only_unreadable_ws = self.temp_path / "only_unreadable_ws"
            only_unreadable_ws.mkdir()
            locked_file = only_unreadable_ws / "transcript.jsonl"
            locked_file.write_text("locked\n", encoding="utf-8")
            locked_file.chmod(0o000)
            try:
                posix_err = io.StringIO()
                with (
                    contextlib.redirect_stderr(posix_err),
                    self.assertRaises(triage.TranscriptNotFoundError) as nf_ctx,
                ):
                    triage.discover_transcript_details(
                        workspace_dir=only_unreadable_ws, home_dir=fake_home
                    )
                self.assertIn(
                    "Warning: unable to read candidate transcript",
                    posix_err.getvalue(),
                )
                self.assertEqual(1, len(nf_ctx.exception.rejected_candidates))
                self.assertIn(
                    "unreadable transcript",
                    nf_ctx.exception.rejected_candidates[0]["reason"],
                )
            finally:
                locked_file.chmod(0o644)

        # 3. Corrupted workspace run.json surfaces parse reason in host rejected_candidates
        host_home = self.temp_path / "host_home_corrupt_run"
        codex_sessions = host_home / ".codex" / "sessions"
        codex_sessions.mkdir(parents=True)
        unrelated_host = codex_sessions / "rollout-unrelated.jsonl"
        unrelated_host.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "other-session"}})
            + "\n",
            encoding="utf-8",
        )
        corrupt_run_ws = self.temp_path / "corrupt_run_host_ws"
        corrupt_run_ws.mkdir()
        (corrupt_run_ws / "run.json").write_text("{truncated", encoding="utf-8")

        host_err = io.StringIO()
        with (
            contextlib.redirect_stderr(host_err),
            self.assertRaises(triage.TranscriptNotFoundError) as host_nf,
        ):
            triage.discover_transcript_details(
                workspace_dir=corrupt_run_ws, home_dir=host_home
            )
        self.assertIn(
            "Warning: malformed JSON in workspace run.json", host_err.getvalue()
        )
        self.assertEqual(1, len(host_nf.exception.rejected_candidates))
        self.assertIn(
            "workspace run.json could not be parsed",
            host_nf.exception.rejected_candidates[0]["reason"],
        )

    def test_triage_cyclomatic_complexity_nesting_and_candidate_score_model(
        self,
    ) -> None:
        import ast

        def mccabe_complexity(node: ast.AST) -> int:
            complexity = 1
            for child in ast.walk(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.ExceptHandler,
                        ast.IfExp,
                    ),
                ):
                    complexity += 1
                elif isinstance(child, ast.comprehension):
                    complexity += len(child.ifs)
                elif isinstance(child, ast.match_case) and (
                    child.guard is not None
                    or not isinstance(child.pattern, ast.MatchAs)
                    or child.pattern.name is not None
                ):
                    complexity += 1
            return complexity

        def strict_ast_complexity(node: ast.AST) -> int:
            complexity = 1
            for child in ast.walk(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.ExceptHandler,
                        ast.With,
                        ast.AsyncWith,
                        ast.IfExp,
                    ),
                ):
                    complexity += 1
                elif isinstance(child, ast.BoolOp):
                    complexity += len(child.values) - 1
                elif isinstance(child, ast.comprehension):
                    complexity += len(child.ifs)
            return complexity

        def max_nesting_depth(stmts: list[ast.stmt], depth: int = 0) -> int:
            deepest = depth
            for stmt in stmts:
                if isinstance(
                    stmt,
                    (
                        ast.If,
                        ast.For,
                        ast.AsyncFor,
                        ast.While,
                        ast.With,
                        ast.AsyncWith,
                        ast.Try,
                        ast.TryStar,
                    ),
                ):
                    orelse = getattr(stmt, "orelse", [])
                    branches: list[tuple[list[ast.stmt], int]] = [
                        (stmt.body, depth + 1),
                        (getattr(stmt, "finalbody", []), depth + 1),
                    ]
                    for handler in getattr(stmt, "handlers", []):
                        branches.append((handler.body, depth + 1))
                    if orelse:
                        is_elif = len(orelse) == 1 and isinstance(orelse[0], ast.If)
                        branches.append((orelse, depth if is_elif else depth + 1))
                    for branch_body, next_depth in branches:
                        if branch_body:
                            deepest = max(
                                deepest, max_nesting_depth(branch_body, next_depth)
                            )
            return deepest

        source_text = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source_text)

        # Ensure zero @dataclass decorators so spec_from_file_location without sys.modules works
        for cls_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for dec in cls_node.decorator_list:
                dec_name = (
                    dec.id
                    if isinstance(dec, ast.Name)
                    else (dec.attr if isinstance(dec, ast.Attribute) else "")
                )
                self.assertNotEqual(
                    "dataclass",
                    dec_name,
                    f"Class {cls_node.name} uses @dataclass in standalone triage.py",
                )

        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[item.name] = item
                for sub in ast.walk(item):
                    if sub is not item and isinstance(
                        sub, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        functions[f"{item.name}.{sub.name}"] = sub
            elif isinstance(item, ast.ClassDef):
                for method in item.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions[f"{item.name}.{method.name}"] = method

        self.assertGreater(len(functions), 0)
        for name, func_node in functions.items():
            c901 = mccabe_complexity(func_node)
            self.assertLessEqual(
                c901,
                10,
                f"Function {name} exceeds C901 McCabe complexity limit of 10 (score: {c901})",
            )
            strict_score = strict_ast_complexity(func_node)
            self.assertLessEqual(
                strict_score,
                10,
                f"Function {name} exceeds AST complexity limit of 10 (score: {strict_score})",
            )
            nesting = max_nesting_depth(func_node.body)
            self.assertLessEqual(
                nesting,
                3,
                f"Function {name} exceeds control-flow nesting limit of 3 (depth: {nesting})",
            )

        # Verify CandidateScore NamedTuple attributes and unindexed loading
        fresh_spec = importlib.util.spec_from_file_location(
            "_ephemeral_unregistered_triage", SCRIPT
        )
        assert fresh_spec is not None and fresh_spec.loader is not None
        fresh_mod = importlib.util.module_from_spec(fresh_spec)
        self.assertNotIn("_ephemeral_unregistered_triage", sys.modules)
        fresh_spec.loader.exec_module(fresh_mod)

        score_primary = fresh_mod.CandidateScore(
            match_tier=1,
            is_workspace=0,
            run_or_session_match=1,
            workspace_or_cwd_match=1,
            repo_or_slug_match=1,
            primary_session=1,
            mtime=200.0,
            name_rank=0,
            root_rank=0,
        )
        score_sidechain = score_primary._replace(primary_session=0, mtime=300.0)
        self.assertIsInstance(score_primary, tuple)
        self.assertGreater(score_primary, score_sidechain)
        self.assertEqual(1, score_primary.primary_session)
        self.assertEqual(200.0, score_primary.mtime)

        discover_node = functions["discover_transcript_details"]
        for sub_node in ast.walk(discover_node):
            if isinstance(sub_node, ast.Subscript) and isinstance(
                sub_node.slice, ast.Constant
            ):
                self.assertNotIn(
                    sub_node.slice.value,
                    (5, 6),
                    "discover_transcript_details must use CandidateScore attributes instead of [5]/[6]",
                )


if __name__ == "__main__":
    unittest.main()

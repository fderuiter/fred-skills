"""Tests for scripts/fred.py CLI lifecycle grouping, parameter guidance, and table formatting."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import pty
except ImportError:
    pty = None

ROOT = Path(__file__).resolve().parents[1]
FRED_PY = ROOT / "scripts/fred.py"


class FredCLITests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        isatty: bool = False,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not isatty:
            return subprocess.run(
                [sys.executable, str(FRED_PY), *args],
                check=False,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
            )
        if pty is None:
            self.skipTest("pty module is not available on this platform")
        master, slave = pty.openpty()
        proc = subprocess.run(
            [sys.executable, str(FRED_PY), *args],
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            check=False,
            cwd=cwd,
            env=env,
        )
        os.close(slave)
        output = b""
        while True:
            try:
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                output += chunk
            except OSError:
                break
        os.close(master)
        return subprocess.CompletedProcess(
            args=[sys.executable, str(FRED_PY), *args],
            returncode=proc.returncode,
            stdout=output.decode("utf-8", errors="replace"),
            stderr="",
        )

    def test_root_help_lifecycle_groups(self):
        proc = self.run_cli("--help")
        self.assertEqual(0, proc.returncode, proc.stderr)
        stdout = proc.stdout

        # Verify all 5 lifecycle section headers are present
        self.assertIn("Discovery:", stdout)
        self.assertIn("Planning:", stdout)
        self.assertIn("Run Management:", stdout)
        self.assertIn("Installation:", stdout)
        self.assertIn("Release:", stdout)

        # Verify all 25 subcommands appear under their respective sections
        discovery_subcmds = ["tasks", "packs", "hosts", "search"]
        planning_subcmds = ["preflight", "plan", "init"]
        run_mgmt_subcmds = [
            "validate-run",
            "resume",
            "memory",
            "checkpoint",
            "report",
            "issues",
            "compare",
            "reconcile",
            "import-sarif",
            "export-sarif",
            "triage",
        ]
        install_subcmds = [
            "installed",
            "install",
            "update",
            "remove",
            "rollback",
            "recover-install",
        ]
        release_subcmds = ["evaluate", "release", "verify-release"]

        for cmd in discovery_subcmds:
            self.assertIn(cmd, stdout)
        for cmd in planning_subcmds:
            self.assertIn(cmd, stdout)
        for cmd in run_mgmt_subcmds:
            self.assertIn(cmd, stdout)
        for cmd in install_subcmds:
            self.assertIn(cmd, stdout)
        for cmd in release_subcmds:
            self.assertIn(cmd, stdout)

        # Verify order of lifecycle sections in help output
        disc_pos = stdout.find("Discovery:")
        plan_pos = stdout.find("Planning:")
        run_pos = stdout.find("Run Management:")
        inst_pos = stdout.find("Installation:")
        rel_pos = stdout.find("Release:")
        self.assertTrue(disc_pos < plan_pos < run_pos < inst_pos < rel_pos)

    def test_plan_and_init_parameter_help(self):
        for cmd in ["preflight", "plan", "init"]:
            proc = self.run_cli(cmd, "--help")
            self.assertEqual(0, proc.returncode, proc.stderr)
            stdout = proc.stdout

            self.assertIn("repo", stdout)
            self.assertIn(
                "default: current working directory", " ".join(stdout.split())
            )
            if cmd in ["plan", "init"]:
                self.assertIn("--task", stdout)
                self.assertIn("--mode", stdout)
                self.assertIn("--budget", stdout)
                self.assertIn("--base", stdout)
                self.assertIn("--profile", stdout)
                self.assertIn("--exclude", stdout)
                self.assertIn("--config", stdout)
                self.assertIn("--full", stdout)

                # Check that choices are listed
                self.assertIn("investigate-bug", stdout)
                self.assertIn("review-pr", stdout)
                self.assertIn("explore", stdout)
                self.assertIn("comprehensive", stdout)
                self.assertIn("brief", stdout)
                self.assertIn("balanced", stdout)
                self.assertIn("deep", stdout)

        init_proc = self.run_cli("init", "--help")
        self.assertIn("--output", init_proc.stdout)
        self.assertIn("--allow-fixes", init_proc.stdout)
        self.assertIn("--allow-issue-publication", init_proc.stdout)

    def test_preflight_defaults_to_cwd(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            proc = self.run_cli("preflight", "--json", cwd=tmp_path)
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(str(tmp_path), data["repository"]["path"])
            self.assertEqual("unversioned", data["repository"]["revision"])

        proc_root = self.run_cli("preflight", "--json", cwd=ROOT)
        self.assertEqual(0, proc_root.returncode, proc_root.stderr)
        data_root = json.loads(proc_root.stdout)
        self.assertEqual(str(ROOT.resolve()), data_root["repository"]["path"])

    def test_preflight_explicit_path(self):
        proc = self.run_cli("preflight", str(ROOT), "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(str(ROOT.resolve()), data["repository"]["path"])

    def test_plan_defaults_to_cwd(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            proc = self.run_cli(
                "plan", "--task", "investigate-bug", "--json", cwd=tmp_path
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertIn("plan", data)
            self.assertIn("preflight", data)
            self.assertEqual(str(tmp_path), data["preflight"]["repository"]["path"])
            self.assertEqual("investigate-bug", data["plan"]["task"])

    def test_plan_explicit_path(self):
        proc1 = self.run_cli("plan", str(ROOT), "--task", "investigate-bug", "--json")
        self.assertEqual(0, proc1.returncode, proc1.stderr)
        data1 = json.loads(proc1.stdout)
        self.assertEqual(str(ROOT.resolve()), data1["preflight"]["repository"]["path"])

        proc2 = self.run_cli("plan", "--task", "investigate-bug", str(ROOT), "--json")
        self.assertEqual(0, proc2.returncode, proc2.stderr)
        data2 = json.loads(proc2.stdout)
        self.assertEqual(str(ROOT.resolve()), data2["preflight"]["repository"]["path"])

    def test_init_defaults_to_cwd(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            run_out = tmp_path / "audit-run"
            proc = self.run_cli(
                "init",
                "--task",
                "investigate-bug",
                "--output",
                str(run_out),
                "--json",
                cwd=tmp_path,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(str(run_out), data["output"])
            self.assertEqual(str(tmp_path), data["run"]["repository"]["path"])
            self.assertTrue(run_out.is_dir())
            self.assertTrue((run_out / "run.json").is_file())

    def test_init_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            run_out = tmp_path / "audit-run-explicit"
            proc1 = self.run_cli(
                "init",
                str(ROOT),
                "--task",
                "investigate-bug",
                "--output",
                str(run_out),
                "--json",
            )
            self.assertEqual(0, proc1.returncode, proc1.stderr)
            data1 = json.loads(proc1.stdout)
            self.assertEqual(str(ROOT.resolve()), data1["run"]["repository"]["path"])
            self.assertTrue((run_out / "run.json").is_file())

            run_out2 = tmp_path / "audit-run-explicit-2"
            proc2 = self.run_cli(
                "init",
                "--task",
                "investigate-bug",
                str(ROOT),
                "--output",
                str(run_out2),
                "--json",
            )
            self.assertEqual(0, proc2.returncode, proc2.stderr)
            data2 = json.loads(proc2.stdout)
            self.assertEqual(str(ROOT.resolve()), data2["run"]["repository"]["path"])
            self.assertTrue((run_out2 / "run.json").is_file())

    def test_install_parameter_help_and_choices(self):
        proc = self.run_cli("install", "--help")
        self.assertEqual(0, proc.returncode, proc.stderr)
        stdout = proc.stdout

        self.assertIn("--destination", stdout)
        self.assertIn("--host", stdout)
        self.assertIn("--scope", stdout)
        self.assertIn("--skill", stdout)
        self.assertIn("--dry-run", stdout)
        self.assertIn("--pack", stdout)

        # Check host choices
        for host in [
            "codex",
            "claude-code",
            "antigravity",
            "gemini-cli",
            "cursor",
            "copilot",
            "opencode",
            "windsurf",
        ]:
            self.assertIn(host, stdout)

    def test_host_choice_validation_fails_with_exit_code_2(self):
        proc = self.run_cli("install", "--host", "invalid", "--destination", "/tmp")
        self.assertEqual(2, proc.returncode)
        self.assertIn("invalid choice: 'invalid'", proc.stderr)
        for host in [
            "codex",
            "claude-code",
            "antigravity",
            "gemini-cli",
            "cursor",
            "copilot",
            "opencode",
            "windsurf",
        ]:
            self.assertIn(host, proc.stderr)

        # Also verify without --destination (destination is optional)
        proc2 = self.run_cli("install", "--host", "invalid")
        self.assertEqual(2, proc2.returncode)
        self.assertIn("invalid choice: 'invalid'", proc2.stderr)

    def test_install_without_destination_reports_resolved_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            proc = self.run_cli("install", cwd=tmp_dir)
            self.assertEqual(1, proc.returncode)
            self.assertIn("No host markers (", proc.stderr)
            for marker in [
                ".claude",
                ".cursor",
                ".agents",
                ".gemini",
                ".opencode",
                ".windsurf",
            ]:
                self.assertIn(marker, proc.stderr)
            self.assertIn(str(Path(tmp_dir).resolve()), proc.stderr)
            self.assertIn("--host", proc.stderr)

    def test_cli_install_succeeds_for_new_hosts_in_project_and_user_scopes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            for host in ["cursor", "copilot", "opencode", "windsurf"]:
                for scope in ["project", "user"]:
                    dest = base / f"{host}-{scope}"
                    proc = self.run_cli(
                        "install",
                        "--destination",
                        str(dest),
                        "--host",
                        host,
                        "--scope",
                        scope,
                        "--pack",
                        "starter",
                    )
                    self.assertEqual(0, proc.returncode, proc.stderr)

    def test_task_choice_validation_fails_with_exit_code_2(self):
        proc = self.run_cli("plan", "/tmp", "--task", "invalid-task")
        self.assertEqual(2, proc.returncode)
        self.assertIn("invalid choice: 'invalid-task'", proc.stderr)
        self.assertIn("review-pr", proc.stderr)

    def test_syntax_errors_exit_code_2(self):
        # Missing required argument
        proc = self.run_cli("plan")
        self.assertEqual(2, proc.returncode)

        # Unknown subcommand
        proc = self.run_cli("nonexistent-command")
        self.assertEqual(2, proc.returncode)

        # Invalid evaluate JSON argument
        proc = self.run_cli(
            "evaluate", "--output", "/tmp", "--adapter-argv", "not-valid-json"
        )
        self.assertEqual(2, proc.returncode)

    def test_operational_failures_exit_code_1(self):
        # Nonexistent manifest file
        proc = self.run_cli("verify-release", "/tmp/does-not-exist-manifest.json")
        self.assertEqual(1, proc.returncode)
        self.assertIn("fred-skills:", proc.stderr)
        self.assertIn("does-not-exist-manifest.json", proc.stderr)

        # Nonexistent audit file for report
        proc = self.run_cli(
            "report", "/tmp/does-not-exist-audit.json", "--output", "/tmp/out"
        )
        self.assertEqual(1, proc.returncode)
        self.assertIn("fred-skills:", proc.stderr)
        self.assertIn("does-not-exist-audit.json", proc.stderr)

    def test_discovery_hosts_table_in_terminal(self):
        proc = self.run_cli("hosts", isatty=True)
        self.assertEqual(0, proc.returncode)
        stdout = proc.stdout

        # Verify table header columns
        self.assertIn("Host", stdout)
        self.assertIn("Project Path", stdout)
        self.assertIn("Invocation", stdout)

        # Verify host entries
        for host in [
            "codex",
            "claude-code",
            "antigravity",
            "gemini-cli",
            "cursor",
            "copilot",
            "opencode",
            "windsurf",
        ]:
            self.assertIn(host, stdout)
        self.assertIn(".agents/skills", stdout)
        self.assertIn(".claude/skills", stdout)
        self.assertIn(".cursor/skills", stdout)
        self.assertIn(".github/skills", stdout)
        self.assertIn(".opencode/skills", stdout)
        self.assertIn(".windsurf/skills", stdout)

    def test_discovery_packs_and_tasks_table_in_terminal(self):
        packs_proc = self.run_cli("packs", isatty=True)
        self.assertEqual(0, packs_proc.returncode)
        self.assertIn("Pack", packs_proc.stdout)
        self.assertIn("Skills", packs_proc.stdout)
        self.assertIn("Description", packs_proc.stdout)
        self.assertIn("starter", packs_proc.stdout)

        tasks_proc = self.run_cli("tasks", isatty=True)
        self.assertEqual(0, tasks_proc.returncode)
        self.assertIn("Task", tasks_proc.stdout)
        self.assertIn("Outcome", tasks_proc.stdout)
        self.assertIn("review-pr", tasks_proc.stdout)
        self.assertIn("design-architecture", tasks_proc.stdout)
        self.assertIn("review-code-changes", tasks_proc.stdout)
        self.assertIn("expand-automated-tests", tasks_proc.stdout)

        search_proc = self.run_cli("search", "contracts", isatty=True)
        self.assertEqual(0, search_proc.returncode)
        self.assertIn("Skill", search_proc.stdout)
        self.assertIn("Title", search_proc.stdout)
        self.assertIn("Description", search_proc.stdout)

    def test_discovery_emits_raw_json_when_piped(self):
        for cmd in ["hosts", "packs", "tasks"]:
            proc = self.run_cli(cmd, isatty=False)
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertIsInstance(data, dict)
            self.assertIn("schema_version", data)

        search_proc = self.run_cli("search", "concurrency", isatty=False)
        self.assertEqual(0, search_proc.returncode, search_proc.stderr)
        data = json.loads(search_proc.stdout)
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)

    def test_json_flag_forces_raw_json_even_in_terminal(self):
        # hosts --json
        proc = self.run_cli("hosts", "--json", isatty=True)
        self.assertEqual(0, proc.returncode)
        data = json.loads(proc.stdout)
        self.assertIn("hosts", data)

        # --json hosts
        proc2 = self.run_cli("--json", "hosts", isatty=True)
        self.assertEqual(0, proc2.returncode)
        data2 = json.loads(proc2.stdout)
        self.assertIn("hosts", data2)

    def test_release_all_cli_command_and_verify(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "release_bundle"
            proc = self.run_cli("release", "--all", "--output", str(out_dir))
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue((out_dir / "manifest.json").is_file())
            self.assertTrue((out_dir / "SHA256SUMS").is_file())
            self.assertTrue((out_dir / "checksums.sha256").is_file())

            verify_proc = self.run_cli("verify-release", str(out_dir / "manifest.json"))
            self.assertEqual(0, verify_proc.returncode, verify_proc.stderr)

    def test_report_help_describes_html_report_and_output_options(self):
        root_proc = self.run_cli("--help")
        self.assertEqual(0, root_proc.returncode, root_proc.stderr)
        self.assertIn(
            "Render human-readable HTML report from an audit", root_proc.stdout
        )
        self.assertNotIn("Markdown summary reports", root_proc.stdout)

        sub_proc = self.run_cli("report", "--help")
        self.assertEqual(0, sub_proc.returncode, sub_proc.stderr)
        self.assertIn(
            "Output directory or file path for rendered report", sub_proc.stdout
        )

    def test_report_with_existing_directory(self):
        import tempfile

        audit_path = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            proc = self.run_cli("report", str(audit_path), "--output", tmp_dir)
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            expected_report = Path(tmp_dir) / "report.html"
            self.assertEqual(str(expected_report), data["output"])
            self.assertTrue(expected_report.is_file())
            self.assertIn(
                "<!doctype html>", expected_report.read_text(encoding="utf-8")
            )

    def test_report_with_new_directory(self):
        import tempfile

        audit_path = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            new_dir = Path(tmp_dir) / "new_report_dir"
            proc = self.run_cli("report", str(audit_path), "--output", str(new_dir))
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            expected_report = new_dir / "report.html"
            self.assertEqual(str(expected_report), data["output"])
            self.assertTrue(expected_report.is_file())
            self.assertIn(
                "<!doctype html>", expected_report.read_text(encoding="utf-8")
            )

    def test_report_with_file_path(self):
        import tempfile

        audit_path = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_file = Path(tmp_dir) / "nested" / "custom_report.html"
            proc = self.run_cli("report", str(audit_path), "--output", str(custom_file))
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(str(custom_file), data["output"])
            self.assertTrue(custom_file.is_file())
            self.assertIn("<!doctype html>", custom_file.read_text(encoding="utf-8"))

    def test_report_with_directory_target(self):
        import shutil
        import tempfile

        audit_fixture = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "audit_run"
            run_dir.mkdir()
            shutil.copyfile(audit_fixture, run_dir / "audit.json")
            out_file = Path(tmp_dir) / "report.html"

            proc = self.run_cli("report", str(run_dir), "--output", str(out_file))
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(out_file.is_file())
            self.assertIn("<!doctype html>", out_file.read_text(encoding="utf-8"))

    def test_report_omitting_audit_argument_uses_cwd(self):
        import shutil
        import tempfile

        audit_fixture = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "audit_run"
            run_dir.mkdir()
            shutil.copyfile(audit_fixture, run_dir / "audit.json")
            out_file = Path(tmp_dir) / "report.html"

            proc = self.run_cli("report", "--output", str(out_file), cwd=str(run_dir))
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(out_file.is_file())

    def test_report_with_directory_missing_audit_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_dir = Path(tmp_dir) / "empty"
            empty_dir.mkdir()
            out_file = Path(tmp_dir) / "report.html"

            proc = self.run_cli("report", str(empty_dir), "--output", str(out_file))
            self.assertEqual(1, proc.returncode)
            self.assertIn(
                "Audit ledger 'audit.json' not found in directory", proc.stderr
            )

    def test_report_omitting_audit_outside_audit_dir_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_dir = Path(tmp_dir) / "empty"
            empty_dir.mkdir()
            out_file = Path(tmp_dir) / "report.html"

            proc = self.run_cli("report", "--output", str(out_file), cwd=str(empty_dir))
            self.assertEqual(1, proc.returncode)
            self.assertIn(
                "Audit ledger 'audit.json' not found in directory", proc.stderr
            )

    def test_issues_with_directory_target_and_omitted_argument(self):
        import shutil
        import tempfile

        audit_fixture = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "audit_run"
            run_dir.mkdir()
            shutil.copyfile(audit_fixture, run_dir / "audit.json")

            # 1. Pass directory target
            out_dir1 = Path(tmp_dir) / "issues1"
            proc1 = self.run_cli("issues", str(run_dir), "--output", str(out_dir1))
            self.assertEqual(0, proc1.returncode, proc1.stderr)
            self.assertTrue(out_dir1.is_dir())

            # 2. Omit audit argument in cwd with audit.json
            out_dir2 = Path(tmp_dir) / "issues2"
            proc2 = self.run_cli("issues", "--output", str(out_dir2), cwd=str(run_dir))
            self.assertEqual(0, proc2.returncode, proc2.stderr)
            self.assertTrue(out_dir2.is_dir())

            # 3. Directory missing audit.json fails
            empty_dir = Path(tmp_dir) / "empty"
            empty_dir.mkdir()
            proc3 = self.run_cli("issues", str(empty_dir), "--output", str(out_dir1))
            self.assertEqual(1, proc3.returncode)
            self.assertIn(
                "Audit ledger 'audit.json' not found in directory", proc3.stderr
            )

    def test_export_sarif_with_directory_target_and_omitted_argument(self):
        import shutil
        import tempfile

        audit_fixture = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "audit_run"
            run_dir.mkdir()
            shutil.copyfile(audit_fixture, run_dir / "audit.json")

            # 1. Pass directory target
            out_file1 = Path(tmp_dir) / "out1.sarif"
            proc1 = self.run_cli(
                "export-sarif", str(run_dir), "--output", str(out_file1)
            )
            self.assertEqual(0, proc1.returncode, proc1.stderr)
            self.assertTrue(out_file1.is_file())

            # 2. Omit audit argument in cwd with audit.json
            out_file2 = Path(tmp_dir) / "out2.sarif"
            proc2 = self.run_cli(
                "export-sarif", "--output", str(out_file2), cwd=str(run_dir)
            )
            self.assertEqual(0, proc2.returncode, proc2.stderr)
            self.assertTrue(out_file2.is_file())

            # 3. Directory missing audit.json fails
            empty_dir = Path(tmp_dir) / "empty"
            empty_dir.mkdir()
            proc3 = self.run_cli(
                "export-sarif", str(empty_dir), "--output", str(out_file1)
            )
            self.assertEqual(1, proc3.returncode)
            self.assertIn(
                "Audit ledger 'audit.json' not found in directory", proc3.stderr
            )

    def test_installed_table_in_terminal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_proc = self.run_cli(
                "install",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                "--pack",
                "starter",
            )
            self.assertEqual(0, install_proc.returncode, install_proc.stderr)

            proc = self.run_cli(
                "installed",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                isatty=True,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("Skill", proc.stdout)
            self.assertIn("Version", proc.stdout)
            self.assertIn("Harness Path", proc.stdout)
            self.assertIn("repo-audit-concurrency", proc.stdout)

    def test_installed_json_output(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_proc = self.run_cli(
                "install",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                "--pack",
                "starter",
            )
            self.assertEqual(0, install_proc.returncode, install_proc.stderr)

            # Non-terminal piped output
            proc = self.run_cli(
                "installed",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                isatty=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual("codex", data["host"])
            self.assertEqual("project", data["scope"])
            self.assertIn("repo-audit-concurrency", data["skills"])
            self.assertIn("content_hash", data["skills"]["repo-audit-concurrency"])

            # Explicit --json flag with terminal
            proc_json = self.run_cli(
                "installed",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                "--json",
                isatty=True,
            )
            self.assertEqual(0, proc_json.returncode, proc_json.stderr)
            data_json = json.loads(proc_json.stdout)
            self.assertEqual("codex", data_json["host"])

    def test_installed_empty_destination_returns_empty_skills_with_message(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Piped JSON
            proc = self.run_cli(
                "installed",
                "--destination",
                tmp_dir,
                isatty=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual({}, data["skills"])
            self.assertIn("message", data)

            # Interactive terminal
            proc_tty = self.run_cli(
                "installed",
                "--destination",
                tmp_dir,
                isatty=True,
            )
            self.assertEqual(0, proc_tty.returncode, proc_tty.stderr)
            self.assertTrue(
                "No installation receipt" in proc_tty.stdout
                or "No entries found." in proc_tty.stdout
            )

    def test_installed_omitting_destination_uses_cwd(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_proc = self.run_cli(
                "install",
                "--destination",
                tmp_dir,
                "--host",
                "codex",
                "--pack",
                "starter",
            )
            self.assertEqual(0, install_proc.returncode, install_proc.stderr)

            # Piped JSON omitting --destination
            proc = self.run_cli(
                "installed",
                "--host",
                "codex",
                cwd=tmp_dir,
                isatty=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(str(Path(tmp_dir).resolve()), data["destination"])
            self.assertEqual("codex", data["host"])
            self.assertEqual("project", data["scope"])
            self.assertIn("repo-audit-concurrency", data["skills"])

            # Auto-detected host omitting both --destination and --host
            proc_auto = self.run_cli(
                "installed",
                cwd=tmp_dir,
                isatty=False,
            )
            self.assertEqual(0, proc_auto.returncode, proc_auto.stderr)
            data_auto = json.loads(proc_auto.stdout)
            self.assertEqual("codex", data_auto["host"])
            self.assertIn("repo-audit-concurrency", data_auto["skills"])

            # Interactive terminal omitting --destination
            proc_tty = self.run_cli(
                "installed",
                cwd=tmp_dir,
                isatty=True,
            )
            self.assertEqual(0, proc_tty.returncode, proc_tty.stderr)
            self.assertIn("repo-audit-concurrency", proc_tty.stdout)

    def test_installed_omitting_destination_empty_directory_returns_empty_skills_with_message(
        self,
    ):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Piped JSON
            proc = self.run_cli(
                "installed",
                cwd=tmp_dir,
                isatty=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual({}, data["skills"])
            self.assertIn("message", data)
            self.assertEqual(str(Path(tmp_dir).resolve()), data["destination"])

            # Interactive terminal
            proc_tty = self.run_cli(
                "installed",
                cwd=tmp_dir,
                isatty=True,
            )
            self.assertEqual(0, proc_tty.returncode, proc_tty.stderr)
            self.assertTrue(
                "No installation receipt" in proc_tty.stdout
                or "No entries found." in proc_tty.stdout
            )

    def test_installed_omitting_destination_user_scope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as user_home:
            user_home_resolved = str(Path(user_home).resolve())
            install_proc = self.run_cli(
                "install",
                "--destination",
                user_home_resolved,
                "--host",
                "claude-code",
                "--scope",
                "user",
                "--pack",
                "starter",
            )
            self.assertEqual(0, install_proc.returncode, install_proc.stderr)

            env = dict(
                os.environ,
                HOME=user_home_resolved,
                USERPROFILE=user_home_resolved,
            )
            proc = self.run_cli(
                "installed",
                "--scope",
                "user",
                isatty=False,
                env=env,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(user_home_resolved, data["destination"])
            self.assertEqual("user", data["scope"])
            self.assertEqual("claude-code", data["host"])
            self.assertIn("repo-audit-concurrency", data["skills"])

    def test_installed_status_column_and_check_flag_exit_codes(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            install_proc = self.run_cli(
                "install",
                "--destination",
                tmp_dir,
                "--host",
                "claude-code",
                "--pack",
                "starter",
            )
            self.assertEqual(0, install_proc.returncode, install_proc.stderr)

            # Healthy workspace with an untracked local skill still passes --check
            skills_dir = tmp_path / ".claude/skills"
            (skills_dir / "custom-local").mkdir(parents=True)
            (skills_dir / "custom-local/SKILL.md").write_text("Custom local skill")

            tty_healthy = self.run_cli(
                "installed", "--destination", tmp_dir, isatty=True
            )
            self.assertEqual(0, tty_healthy.returncode)
            self.assertIn("Status", tty_healthy.stdout)
            self.assertIn("tracked", tty_healthy.stdout)
            self.assertIn("untracked", tty_healthy.stdout)
            self.assertIn("custom-local", tty_healthy.stdout)

            chk_healthy = self.run_cli("installed", "--destination", tmp_dir, "--check")
            self.assertEqual(0, chk_healthy.returncode, chk_healthy.stderr)

            # Edit one managed skill and delete another managed skill
            (skills_dir / "repo-audit-concurrency/SKILL.md").write_text("Edited")
            managed_names = [
                name
                for name in json.loads(chk_healthy.stdout)["skills"]
                if name != "repo-audit-concurrency"
            ]
            shutil.rmtree(skills_dir / managed_names[0])

            tty_drift = self.run_cli("installed", "--destination", tmp_dir, isatty=True)
            self.assertEqual(0, tty_drift.returncode)
            self.assertIn("modified", tty_drift.stdout)
            self.assertIn("missing", tty_drift.stdout)
            self.assertIn("untracked", tty_drift.stdout)

            chk_drift = self.run_cli("installed", "--destination", tmp_dir, "--check")
            self.assertEqual(1, chk_drift.returncode)
            drift_json = json.loads(chk_drift.stdout)
            self.assertFalse(drift_json["healthy"])
            self.assertEqual(["custom-local"], drift_json["untracked"])
            self.assertEqual(
                "modified",
                drift_json["skills"]["repo-audit-concurrency"]["status"],
            )
            self.assertEqual(
                "missing", drift_json["skills"][managed_names[0]]["status"]
            )

            # Active lock file also causes installed --check to exit 1
            repair_proc = self.run_cli(
                "update",
                "--destination",
                tmp_dir,
                "--pack",
                "starter",
                "--force",
            )
            self.assertEqual(0, repair_proc.returncode, repair_proc.stderr)
            lock_file = tmp_path / ".fred-skills/claude-code-project/operation.lock"
            lock_file.write_text("locked")
            chk_locked = self.run_cli("installed", "--destination", tmp_dir, "--check")
            self.assertEqual(1, chk_locked.returncode)
            locked_json = json.loads(chk_locked.stdout)
            self.assertTrue(locked_json["locked"])
            self.assertFalse(locked_json["healthy"])

    def test_cli_force_repairs_deleted_and_modified_skills(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            self.assertEqual(
                0,
                self.run_cli(
                    "install",
                    "--destination",
                    tmp_dir,
                    "--host",
                    "claude-code",
                    "--pack",
                    "starter",
                ).returncode,
            )
            skills_dir = tmp_path / ".claude/skills"
            state_dir = tmp_path / ".fred-skills/claude-code-project"

            # Edit SKILL.md -> blocked without --force, repaired with install --force
            (skills_dir / "repo-audit-concurrency/SKILL.md").write_text(
                "Local CLI edit"
            )
            blocked = self.run_cli(
                "update", "--destination", tmp_dir, "--pack", "starter"
            )
            self.assertEqual(1, blocked.returncode)
            self.assertIn("--force", blocked.stderr)

            repaired_install = self.run_cli(
                "install",
                "--destination",
                tmp_dir,
                "--pack",
                "starter",
                "--force",
            )
            self.assertEqual(0, repaired_install.returncode, repaired_install.stderr)
            backups = list(
                (state_dir / "backups").glob("*/repo-audit-concurrency/SKILL.md")
            )
            self.assertTrue(any(p.read_text() == "Local CLI edit" for p in backups))
            self.assertEqual(
                0,
                self.run_cli(
                    "installed", "--destination", tmp_dir, "--check"
                ).returncode,
            )

            # Delete .claude/skills entirely -> repaired with update --force
            shutil.rmtree(skills_dir)
            repaired_update = self.run_cli(
                "update",
                "--destination",
                tmp_dir,
                "--pack",
                "starter",
                "--force",
            )
            self.assertEqual(0, repaired_update.returncode, repaired_update.stderr)
            self.assertEqual(
                0,
                self.run_cli(
                    "installed", "--destination", tmp_dir, "--check"
                ).returncode,
            )

            # Delete single managed skill -> removed from receipt with remove --force
            shutil.rmtree(skills_dir / "repo-audit-concurrency")
            rm_blocked = self.run_cli(
                "remove",
                "--destination",
                tmp_dir,
                "--skill",
                "repo-audit-concurrency",
            )
            self.assertEqual(1, rm_blocked.returncode)
            self.assertIn("--force", rm_blocked.stderr)

            rm_forced = self.run_cli(
                "remove",
                "--destination",
                tmp_dir,
                "--skill",
                "repo-audit-concurrency",
                "--force",
            )
            self.assertEqual(0, rm_forced.returncode, rm_forced.stderr)
            after_rm = json.loads(
                self.run_cli("installed", "--destination", tmp_dir).stdout
            )
            self.assertNotIn("repo-audit-concurrency", after_rm["skills"])
            self.assertTrue(after_rm["healthy"])

            # rollback and recover-install do not expose --force
            for subcmd in ["rollback", "recover-install"]:
                bad = self.run_cli(subcmd, "--force")
                self.assertEqual(2, bad.returncode)


class FredDispatchRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import fred

        cls.fred = fred

    def test_dispatch_cyclomatic_complexity_below_five(self):
        """Primary dispatch function cyclomatic complexity must be strictly less than 5."""
        import ast

        def calculate_complexity(node):
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
            return complexity

        tree = ast.parse((ROOT / "scripts/fred.py").read_text(encoding="utf-8"))
        dispatch_node = None
        for item in tree.body:
            if isinstance(item, ast.FunctionDef) and item.name == "dispatch":
                dispatch_node = item
                break
        self.assertIsNotNone(dispatch_node)
        score = calculate_complexity(dispatch_node)
        self.assertLess(score, 5, f"dispatch complexity {score} exceeds limit of 5")

    def test_dispatch_nesting_depth_at_most_one(self):
        """Primary dispatch function maximum control flow nesting depth must not exceed 1."""
        import ast

        def max_nesting_depth(node, depth=0):
            max_depth = depth
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.Try,
                        ast.With,
                        ast.AsyncWith,
                    ),
                ):
                    child_depth = max_nesting_depth(child, depth + 1)
                else:
                    child_depth = max_nesting_depth(child, depth)
                max_depth = max(max_depth, child_depth)
            return max_depth

        tree = ast.parse((ROOT / "scripts/fred.py").read_text(encoding="utf-8"))
        dispatch_node = None
        for item in tree.body:
            if isinstance(item, ast.FunctionDef) and item.name == "dispatch":
                dispatch_node = item
                break
        self.assertIsNotNone(dispatch_node)
        depth = max_nesting_depth(dispatch_node)
        self.assertLessEqual(depth, 1, f"dispatch nesting depth {depth} exceeds 1")

    def test_dispatch_registry_maps_all_twenty_seven_commands(self):
        """COMMAND_REGISTRY must contain all 27 subcommands across all lifecycle groups."""
        expected_commands = {
            cmd for cmds in self.fred.LIFECYCLE_GROUPS.values() for cmd in cmds
        }
        self.assertEqual(27, len(expected_commands))
        self.assertEqual(expected_commands, set(self.fred.COMMAND_REGISTRY.keys()))
        self.assertEqual(self.fred.COMMAND_REGISTRY, self.fred.DISPATCH_REGISTRY)

    def test_unregistered_command_raises_value_error(self):
        """Dispatching an unregistered command must raise ValueError."""
        import argparse

        with self.assertRaises(ValueError):
            self.fred.dispatch(argparse.Namespace(command="unregistered-subcommand"))

    def test_domain_handlers_callable(self):
        """All 7 domain handlers must exist and be callable."""
        handlers = [
            self.fred.handle_catalog,
            self.fred.handle_repository,
            self.fred.handle_runs,
            self.fred.handle_installation,
            self.fred.handle_reporting,
            self.fred.handle_release,
            self.fred.handle_diagnostics,
        ]
        for handler in handlers:
            self.assertTrue(callable(handler))


if __name__ == "__main__":
    unittest.main()

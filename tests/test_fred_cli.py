"""Tests for scripts/fred.py CLI lifecycle grouping, parameter guidance, and table formatting."""

import json
import os
import subprocess
import sys
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
        self, *args: str, isatty: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if not isatty:
            return subprocess.run(
                [sys.executable, str(FRED_PY), *args],
                check=False,
                capture_output=True,
                text=True,
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
        for cmd in ["plan", "init"]:
            proc = self.run_cli(cmd, "--help")
            self.assertEqual(0, proc.returncode, proc.stderr)
            stdout = proc.stdout

            self.assertIn("repo", stdout)
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
        for host in ["codex", "claude-code", "antigravity", "gemini-cli"]:
            self.assertIn(host, stdout)

    def test_host_choice_validation_fails_with_exit_code_2(self):
        proc = self.run_cli("install", "--host", "invalid", "--destination", "/tmp")
        self.assertEqual(2, proc.returncode)
        self.assertIn("invalid choice: 'invalid'", proc.stderr)
        for host in ["codex", "claude-code", "antigravity", "gemini-cli"]:
            self.assertIn(host, proc.stderr)

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
        self.assertIn("codex", stdout)
        self.assertIn(".agents/skills", stdout)
        self.assertIn("claude-code", stdout)
        self.assertIn(".claude/skills", stdout)
        self.assertIn("antigravity", stdout)
        self.assertIn("gemini-cli", stdout)

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


if __name__ == "__main__":
    unittest.main()

"""Tests for scripts/fred.py CLI lifecycle grouping, parameter guidance, and table formatting."""

import errno
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

fcntl: ModuleType | None
pty: ModuleType | None
termios: ModuleType | None
try:
    import fcntl as _fcntl
    import pty as _pty
    import termios as _termios

    fcntl = _fcntl
    pty = _pty
    termios = _termios
except ImportError:
    fcntl = None
    pty = None
    termios = None

ROOT = Path(__file__).resolve().parents[1]
FRED_PY = ROOT / "scripts/fred.py"


def _drain_pty_master(master: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


class FredCLITests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        isatty: bool = False,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        columns: int | None = None,
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
        try:
            run_env = dict(os.environ if env is None else env)
            if columns is not None:
                run_env["COLUMNS"] = str(columns)
                if fcntl is not None and termios is not None:
                    winsize = struct.pack("HHHH", 24, max(1, columns), 0, 0)
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, winsize)
            with subprocess.Popen(
                [sys.executable, str(FRED_PY), *args],
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=run_env,
            ) as proc:
                os.close(slave)
                slave = -1
                output = _drain_pty_master(master)
                returncode = proc.wait()
        finally:
            if slave >= 0:
                os.close(slave)
            os.close(master)
        return subprocess.CompletedProcess(
            args=[sys.executable, str(FRED_PY), *args],
            returncode=returncode,
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
            "kiro",
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
            "kiro",
        ]:
            self.assertIn(host, proc.stderr)

        # Also verify without --destination (destination is optional)
        proc2 = self.run_cli("install", "--host", "invalid")
        self.assertEqual(2, proc2.returncode)
        self.assertIn("invalid choice: 'invalid'", proc2.stderr)

    def test_install_without_destination_reports_resolved_directory(self):
        import tempfile

        starter_skills = json.loads(
            (ROOT / "catalog/packs.json").read_text(encoding="utf-8")
        )["packs"]["starter"]["skills"]
        self.assertEqual(6, len(starter_skills))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            proc = self.run_cli("install", cwd=tmp_dir)
            self.assertEqual(0, proc.returncode, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual("codex", data["host"])
            self.assertEqual(starter_skills, data["skills"])
            for skill_name in starter_skills:
                self.assertTrue(
                    (tmp_path / ".agents/skills" / skill_name / "SKILL.md").is_file()
                )
            receipt_path = tmp_path / ".fred-skills/codex-project/receipt.json"
            self.assertTrue(receipt_path.is_file())
            rec = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("codex", rec["host"])

        with tempfile.TemporaryDirectory() as empty_update_dir:
            upd_proc = self.run_cli("update", cwd=empty_update_dir)
            self.assertEqual(1, upd_proc.returncode)
            self.assertIn("No host markers (", upd_proc.stderr)
            self.assertIn(str(Path(empty_update_dir).resolve()), upd_proc.stderr)
            self.assertIn("--host", upd_proc.stderr)

    def test_cli_install_succeeds_for_new_hosts_in_project_and_user_scopes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            for host in ["cursor", "copilot", "opencode", "windsurf", "kiro"]:
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
            "kiro",
        ]:
            self.assertIn(host, stdout)
        self.assertIn(".agents/skills", stdout)
        self.assertIn(".claude/skills", stdout)
        self.assertIn(".cursor/skills", stdout)
        self.assertIn(".github/skills", stdout)
        self.assertIn(".opencode/skills", stdout)
        self.assertIn(".windsurf/skills", stdout)
        self.assertIn(".kiro/skills", stdout)

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

    def test_search_matches_all_tasks_symptoms_and_token_orders(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import fred

        recipes = json.loads((ROOT / "catalog/recipes.json").read_text("utf-8"))[
            "recipes"
        ]
        self.assertEqual(18, len(recipes))
        for task_name, recipe in recipes.items():
            with self.subTest(task=task_name):
                results = fred._search_skills(task_name)
                found_names = {item["name"] for item in results}
                self.assertTrue(
                    set(recipe["skills"]).issubset(found_names),
                    f"Task {task_name} missing member skills: {set(recipe['skills']) - found_names}",
                )
                spaced = task_name.replace("-", " ")
                reversed_tokens = " ".join(reversed(task_name.split("-")))
                self.assertEqual(
                    [r["name"] for r in results],
                    [r["name"] for r in fred._search_skills(spaced)],
                )
                self.assertTrue(
                    set(recipe["skills"]).issubset(
                        {r["name"] for r in fred._search_skills(reversed_tokens)}
                    )
                )

        for query in (
            "database migration",
            "migration database",
            "trust boundary",
            "deadlock",
            "memory leak",
            "leak memory",
            "xss",
            "ssrf",
            "breaking change",
            "rollback",
        ):
            with self.subTest(query=query):
                proc = self.run_cli("search", query, isatty=False)
                self.assertEqual(0, proc.returncode, proc.stderr)
                rows = json.loads(proc.stdout)
                self.assertIsInstance(rows, list)
                self.assertGreater(
                    len(rows), 0, f"Expected CLI search matches for {query!r}"
                )
                self.assertTrue(all(isinstance(item, dict) for item in rows))

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

    def test_pty_table_wraps_to_terminal_width_and_clamps_narrow_columns(self):
        for cmd_args, expected_id in [
            (["search", "contracts"], "repo-audit-behavioral-contracts"),
            (["packs"], "starter"),
            (["tasks"], "expand-automated-tests"),
            (["hosts"], "antigravity"),
        ]:
            with self.subTest(cmd=cmd_args):
                proc = self.run_cli(*cmd_args, isatty=True, columns=80)
                self.assertEqual(0, proc.returncode)
                lines = proc.stdout.splitlines()
                self.assertTrue(lines)
                for line in lines:
                    self.assertLessEqual(
                        len(line),
                        80,
                        f"Line exceeded 80 columns ({len(line)}): {line!r}",
                    )
                self.assertIn(expected_id, proc.stdout)

        for narrow_cols in (25, 10):
            narrow_proc = self.run_cli(
                "search", "contracts", isatty=True, columns=narrow_cols
            )
            self.assertEqual(0, narrow_proc.returncode)
            self.assertIn("repo-audit-behavioral-contracts", narrow_proc.stdout)

        large_pty_proc = self.run_cli("search", "audit", "--json", isatty=True)
        self.assertEqual(0, large_pty_proc.returncode)
        self.assertGreater(len(large_pty_proc.stdout.encode("utf-8")), 16384)
        self.assertIsInstance(json.loads(large_pty_proc.stdout), list)

    def test_format_table_unit_width_and_below_forty_column_clamping(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import fred

        headers = ["Skill", "Title", "Description"]
        rows = [
            [
                "repo-audit-behavioral-contracts",
                "Behavioral Contracts and Boundary Verification",
                "Inspect implicit and explicit behavioral contracts across module boundaries, asynchronous callbacks, and error semantics.",
            ]
        ]
        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 24))),
        ):
            rendered_80 = fred.format_table(headers, rows)
        for line in rendered_80.splitlines():
            self.assertLessEqual(len(line), 80)
        self.assertIn("repo-audit-behavioral-contracts", rendered_80)

        for tiny_cols in (35, 20, 5, 1):
            with (
                patch.object(sys.stdout, "isatty", return_value=True),
                patch(
                    "shutil.get_terminal_size",
                    return_value=os.terminal_size((tiny_cols, 24)),
                ),
            ):
                rendered_tiny = fred.format_table(headers, rows)
            self.assertIn("repo-audit-behavioral-contracts", rendered_tiny)

    def test_tty_summaries_and_json_override_across_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            (repo_dir / "main.py").write_text("x = 1\n", encoding="utf-8")

            # preflight: TTY concise summary vs --json override vs piped JSON
            pre_tty = self.run_cli("preflight", str(repo_dir), isatty=True)
            self.assertEqual(0, pre_tty.returncode)
            self.assertIn("Repository:", pre_tty.stdout)
            self.assertIn("Files indexed:", pre_tty.stdout)
            self.assertFalse(pre_tty.stdout.lstrip().startswith("{"))

            pre_json = self.run_cli("preflight", str(repo_dir), "--json", isatty=True)
            self.assertEqual(0, pre_json.returncode)
            self.assertEqual(
                str(repo_dir), json.loads(pre_json.stdout)["repository"]["path"]
            )

            # plan: TTY concise summary vs --json override
            plan_tty = self.run_cli(
                "plan", str(repo_dir), "--task", "investigate-bug", isatty=True
            )
            self.assertEqual(0, plan_tty.returncode)
            self.assertIn("Plan: investigate-bug", plan_tty.stdout)
            self.assertIn("Selected skills", plan_tty.stdout)
            self.assertFalse(plan_tty.stdout.lstrip().startswith("{"))

            plan_json = self.run_cli(
                "plan",
                str(repo_dir),
                "--task",
                "investigate-bug",
                "--json",
                isatty=True,
            )
            self.assertEqual(0, plan_json.returncode)
            self.assertEqual(
                "investigate-bug", json.loads(plan_json.stdout)["plan"]["task"]
            )

            # init, validate-run, checkpoint, resume, report, export-sarif
            ws = tmp_path / "audit-ws"
            init_tty = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "investigate-bug",
                "--output",
                str(ws),
                isatty=True,
            )
            self.assertEqual(0, init_tty.returncode)
            self.assertIn("Initialized run:", init_tty.stdout)
            self.assertIn("Prompt:", init_tty.stdout)

            val_tty = self.run_cli("validate-run", str(ws), isatty=True)
            self.assertEqual(0, val_tty.returncode)
            self.assertIn("Run validation: valid", val_tty.stdout)

            val_json = self.run_cli("validate-run", str(ws), "--json", isatty=True)
            self.assertEqual(0, val_json.returncode)
            self.assertTrue(json.loads(val_json.stdout)["valid"])

            chk_tty = self.run_cli(
                "checkpoint",
                str(ws),
                "--state",
                "paused",
                "--note",
                "slice complete",
                isatty=True,
            )
            self.assertEqual(0, chk_tty.returncode)
            self.assertIn("Recorded checkpoint:", chk_tty.stdout)
            self.assertIn("slice complete", chk_tty.stdout)

            res_tty = self.run_cli("resume", str(ws), isatty=True)
            self.assertEqual(0, res_tty.returncode)
            self.assertIn("Resume check:", res_tty.stdout)

            rep_out = tmp_path / "report.html"
            rep_tty = self.run_cli(
                "report", str(ws), "--output", str(rep_out), isatty=True
            )
            self.assertEqual(0, rep_tty.returncode)
            self.assertIn("Rendered report:", rep_tty.stdout)

            # install, update, remove, rollback
            dest = tmp_path / "host-dest"
            inst_tty = self.run_cli(
                "install",
                "--destination",
                str(dest),
                "--host",
                "codex",
                "--pack",
                "starter",
                isatty=True,
            )
            self.assertEqual(0, inst_tty.returncode)
            self.assertIn("Installed", inst_tty.stdout)

            upd_json = self.run_cli(
                "update",
                "--destination",
                str(dest),
                "--host",
                "codex",
                "--pack",
                "starter",
                "--json",
                isatty=True,
            )
            self.assertEqual(0, upd_json.returncode)
            self.assertEqual("update", json.loads(upd_json.stdout)["action"])

    def test_render_tty_output_covers_all_twenty_seven_subcommands(self):
        import argparse

        sys.path.insert(0, str(ROOT / "scripts"))
        import fred

        sample_payloads: dict[str, object] = {
            "tasks": {"recipes": {"review-pr": {"outcome": "Review", "skills": ["a"]}}},
            "packs": {
                "packs": {"starter": {"skills": ["a"], "description": "Starter"}}
            },
            "hosts": {
                "hosts": {
                    "codex": {"project_path": ".agents/skills", "invocation": "$skill"}
                }
            },
            "search": [{"name": "s1", "title": "Skill 1", "description": "Desc"}],
            "preflight": {
                "repository": {"path": "/repo", "revision": "abc123", "dirty": False},
                "snapshot": {"file_count": 10, "complete": True},
                "languages": ["Python"],
            },
            "plan": {
                "plan": {
                    "task": "review-pr",
                    "mode": "changes",
                    "budget": "balanced",
                    "skills": ["s1"],
                    "deferred_skills": [],
                    "expected_outputs": ["audit.json"],
                },
                "preflight": {"repository": {"path": "/repo", "revision": "abc123"}},
            },
            "init": {
                "output": "/ws",
                "run": {
                    "run_id": "run-1",
                    "plan": {
                        "task": "review-pr",
                        "mode": "investigate",
                        "budget": "balanced",
                        "skills": ["s1"],
                    },
                },
            },
            "validate-run": {"valid": True, "semantic_claims_verified": False},
            "resume": {
                "old_snapshot": "aaa111222333444",
                "new_snapshot": "bbb111222333444",
                "changed_files": ["a.py"],
                "stale_finding_ids": [],
                "next_action": "Review changes",
            },
            "advance": {
                "run_id": "run-1",
                "state": "running",
                "pass_index": 1,
                "total_passes": 6,
                "completed_skill": "repo-verify-finding",
                "active_skill": "repo-specify-regression",
                "completed": False,
                "old_snapshot": "aaa111222333444",
                "new_snapshot": "bbb111222333444",
                "changed_files": ["a.py"],
                "stale_finding_ids": [],
                "prompt": "/ws/prompt.md",
                "revalidation": "/ws/revalidation.json",
            },
            "memory": {"output": "/mem.json", "contracts": 2},
            "checkpoint": {
                "run_id": "run-1",
                "state": "paused",
                "checkpoints": [{"note": "paused here"}],
            },
            "report": {"output": "/report.html"},
            "issues": {"drafts": ["/issues/I-1.md"], "remote_writes": 0},
            "compare": {
                "introduced": ["F-2"],
                "persisting": [],
                "status_changed": [],
                "not_observed": [],
                "ambiguous": [],
            },
            "reconcile": {
                "finding_id": "F-1",
                "state": "in-progress",
                "note": "working",
            },
            "import-sarif": {"observations": 3, "output": "/obs.json"},
            "export-sarif": {"results": 2, "output": "/out.sarif"},
            "triage": {
                "session_id": "s-1",
                "failure_classification": {
                    "failure_type": "tool_timeout",
                    "line_number": 4,
                    "summary": "Timed out",
                },
                "correlated_domain": {"specialist_skill": "repo-audit-concurrency"},
                "recovery": {"instructions": ["1. Retry narrowly"]},
                "scrubbed_transcript": "LARGE_TRANSCRIPT_BODY",
            },
            "vet": {
                "target": "/dest/skill",
                "kind": "skill",
                "passed": True,
                "scanned_files": 2,
                "findings": [],
            },
            "installed": {
                "target": "/dest/.agents/skills",
                "skills": {"s1": {"source_version": "0.2.0", "status": "tracked"}},
                "untracked": [],
            },
            "install": {
                "action": "install",
                "skills": ["s1"],
                "host": "codex",
                "scope": "project",
                "target": "/dest/.agents/skills",
                "dry_run": False,
            },
            "update": {
                "action": "update",
                "skills": ["s1"],
                "host": "codex",
                "scope": "project",
                "target": "/dest/.agents/skills",
                "dry_run": True,
            },
            "remove": {"action": "remove", "skills": ["s1"], "dry_run": False},
            "rollback": {"action": "rollback", "skills": ["s1"]},
            "recover-install": {
                "recovery": "restored-before-state",
                "skills": ["s1"],
                "preserved_interrupted_content": "/backups/p1",
            },
            "evaluate": {
                "runs": [{"case": "c1"}, {"case": "c1"}],
                "summary": {
                    "without_skill": {
                        "runs": 1,
                        "exact_match_rate": 0.0,
                        "failures": 0,
                    },
                    "with_skill": {"runs": 1, "exact_match_rate": 1.0, "failures": 0},
                },
            },
            "release": {
                "version": "0.2.0",
                "pack": "starter",
                "archives": [{"file": "a.zip"}],
                "skills": ["s1"],
            },
            "verify-release": {
                "version": "0.2.0",
                "verified_archives": 2,
                "authenticity": "Checksums verified.",
            },
        }
        all_commands = [cmd for cmds in fred.LIFECYCLE_GROUPS.values() for cmd in cmds]
        self.assertEqual(29, len(all_commands))
        for cmd in all_commands:
            with self.subTest(command=cmd):
                rendered = fred.render_tty_output(
                    cmd,
                    sample_payloads[cmd],
                    argparse.Namespace(command=cmd, output=None, output_dir=None),
                )
                self.assertTrue(rendered.strip())
                self.assertFalse(rendered.lstrip().startswith("{"))
                self.assertNotIn("LARGE_TRANSCRIPT_BODY", rendered)

        multi_host_rendered = fred.render_tty_output(
            "install",
            {
                "action": "install",
                "skills": ["s1", "s2"],
                "host": None,
                "target": None,
                "hosts": [
                    {"host": "codex", "target": "/dest/.agents/skills"},
                    {"host": "claude-code", "target": "/dest/.claude/skills"},
                ],
                "scope": "project",
                "dry_run": False,
            },
        )
        self.assertIn("Installed 2 skills across 2 hosts", multi_host_rendered)
        self.assertIn("codex -> /dest/.agents/skills", multi_host_rendered)
        self.assertIn("claude-code -> /dest/.claude/skills", multi_host_rendered)

        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 24))),
        ):
            installed_table = fred.render_tty_output(
                "installed",
                {
                    "hosts": [
                        {
                            "host": "claude-code",
                            "target": r"C:\Users\runner\tmp\.claude\skills",
                            "skills": {
                                "repo-audit-behavioral-contracts": {
                                    "source_version": "0.2.0",
                                    "status": "tracked",
                                }
                            },
                            "untracked": [],
                        }
                    ]
                },
            )
        for line in installed_table.splitlines():
            self.assertLessEqual(len(line), 80)
        self.assertIn("claude-code", installed_table)
        self.assertIn("repo-audit-behavioral-contracts", installed_table)

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

    def test_report_loads_sibling_remediation_and_renders_comparison_tables(self):
        import shutil
        import tempfile

        audit_fixture = ROOT / "tests/fixtures/audit-valid.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_dir = tmp_path / "reconciled_run"
            run_dir.mkdir()
            shutil.copyfile(audit_fixture, run_dir / "audit.json")
            proof_path = tmp_path / "verification.json"
            proof_path.write_text(
                json.dumps(
                    {
                        "before_revision": "rev-a",
                        "after_revision": "rev-b",
                        "original_reproduced": True,
                        "regression_passed": True,
                        "safe_controls_passed": True,
                        "commands": ["pytest tests/test_fix.py"],
                        "result": "Passed regression and safe controls",
                    }
                ),
                encoding="utf-8",
            )
            rec_proc = self.run_cli(
                "reconcile",
                str(run_dir),
                "--finding",
                "F-1",
                "--state",
                "verified-fixed",
                "--note",
                "Verified via regression harness",
                "--verification",
                str(proof_path),
            )
            self.assertEqual(0, rec_proc.returncode, rec_proc.stderr)

            # Directory input auto-loads sibling remediation.json
            dir_report = tmp_path / "dir_report.html"
            proc_dir = self.run_cli("report", str(run_dir), "--output", str(dir_report))
            self.assertEqual(0, proc_dir.returncode, proc_dir.stderr)
            dir_html = dir_report.read_text(encoding="utf-8")
            self.assertIn("Verified fixed", dir_html)
            self.assertIn("Verified via regression harness", dir_html)
            self.assertIn('id="remediation"', dir_html)

            # Explicit audit.json file input in run_dir also auto-loads sibling remediation.json
            file_report = tmp_path / "file_report.html"
            proc_file = self.run_cli(
                "report", str(run_dir / "audit.json"), "--output", str(file_report)
            )
            self.assertEqual(0, proc_file.returncode, proc_file.stderr)
            self.assertIn("Verified fixed", file_report.read_text(encoding="utf-8"))

            # --prior renders structured HTML comparison tables with #F-* links and zero <pre>
            prior_dir = tmp_path / "prior_run"
            prior_dir.mkdir()
            prior_audit = json.loads(audit_fixture.read_text(encoding="utf-8"))
            prior_audit["findings"] = []
            (prior_dir / "audit.json").write_text(
                json.dumps(prior_audit), encoding="utf-8"
            )
            cmp_report = tmp_path / "cmp_report.html"
            proc_cmp = self.run_cli(
                "report",
                str(run_dir),
                "--prior",
                str(prior_dir),
                "--output",
                str(cmp_report),
            )
            self.assertEqual(0, proc_cmp.returncode, proc_cmp.stderr)
            cmp_html = cmp_report.read_text(encoding="utf-8")
            comp_section = cmp_html[cmp_html.index('<section id="comparison">') :]
            self.assertIn('<a href="#F-1">F-1</a>', comp_section)
            self.assertIn("<table>", comp_section)
            self.assertNotIn("<pre>", comp_section)

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

    def test_import_sarif_with_optional_root(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            src_file = repo_dir / "app.py"
            src_file.write_text("x = 1\n", encoding="utf-8")
            outside_file = Path(tmp_dir) / "outside.py"
            outside_file.write_text("y = 2\n", encoding="utf-8")

            sarif_doc = {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {
                            "driver": {
                                "name": "semgrep",
                                "version": "1.90.0",
                                "rules": [
                                    {
                                        "id": "py.check",
                                        "defaultConfiguration": {"level": "error"},
                                    }
                                ],
                            }
                        },
                        "results": [
                            {
                                "ruleId": "py.check",
                                "message": {"text": "found issue"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": src_file.resolve().as_uri()
                                            },
                                            "region": {"startLine": 1, "endLine": 1},
                                        },
                                        "logicalLocations": [{"name": "main"}],
                                    },
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": outside_file.resolve().as_uri()
                                            },
                                            "region": {"startLine": 1},
                                        }
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
            sarif_path = Path(tmp_dir) / "scan.sarif"
            sarif_path.write_text(json.dumps(sarif_doc), encoding="utf-8")
            out_path = Path(tmp_dir) / "observations.json"

            proc = self.run_cli(
                "import-sarif",
                str(sarif_path),
                "--root",
                str(repo_dir),
                "--output",
                str(out_path),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            imported = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual("error", imported["observations"][0]["reported_level"])
            self.assertEqual(
                [{"path": "app.py", "line": 1, "end_line": 1, "symbol": "main"}],
                imported["observations"][0]["locations"],
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
            self.assertIn("Host", proc.stdout)
            self.assertIn("Skill", proc.stdout)
            self.assertIn("Version", proc.stdout)
            self.assertIn("Harness Path", proc.stdout)
            self.assertIn("codex", proc.stdout)
            self.assertIn("repo-audit-concurrency", proc.stdout)

    def test_multi_host_install_update_installed_and_destructive_guardrails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            ws = Path(tmp_dir).resolve()
            (ws / ".claude").mkdir()
            (ws / ".agents").mkdir()

            inst = self.run_cli("install", "--destination", str(ws))
            self.assertEqual(0, inst.returncode, inst.stderr)
            inst_data = json.loads(inst.stdout)
            self.assertEqual(
                ["codex", "claude-code"], [h["host"] for h in inst_data["hosts"]]
            )
            self.assertTrue(
                (ws / ".claude/skills/repo-audit-concurrency/SKILL.md").is_file()
            )
            self.assertTrue(
                (ws / ".agents/skills/repo-audit-concurrency/SKILL.md").is_file()
            )
            self.assertFalse((ws / ".fred-skills/antigravity-project").exists())

            tty = self.run_cli("installed", "--destination", str(ws), isatty=True)
            self.assertEqual(0, tty.returncode, tty.stderr)
            self.assertIn("Host", tty.stdout)
            self.assertIn("claude-code", tty.stdout)
            self.assertIn("codex", tty.stdout)
            self.assertIn(".claude/skills", tty.stdout)
            self.assertIn(".agents/skills", tty.stdout)

            chk = self.run_cli("installed", "--destination", str(ws), "--check")
            self.assertEqual(0, chk.returncode, chk.stderr)
            chk_json = json.loads(chk.stdout)
            self.assertEqual(
                ["codex", "claude-code"], [h["host"] for h in chk_json["hosts"]]
            )

            upd = self.run_cli("update", "--destination", str(ws))
            self.assertEqual(0, upd.returncode, upd.stderr)

            for subcmd, extra in [
                ("remove", ["--skill", "repo-audit-concurrency"]),
                ("rollback", []),
                ("recover-install", ["--confirm-interrupted"]),
            ]:
                denied = self.run_cli(subcmd, "--destination", str(ws), *extra)
                self.assertEqual(1, denied.returncode)
                self.assertIn("--host", denied.stderr)

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

    def test_domain_workflows_end_to_end_cli_and_audit_only_guardrails(self):
        import shutil

        if not shutil.which("git"):
            self.skipTest("Git unavailable")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            repo = tmp_path / "sample_repo"
            repo.mkdir()
            (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")
            for argv in [
                ["init", "-q"],
                ["config", "user.name", "Fixture"],
                ["config", "user.email", "fixture@example.invalid"],
                ["add", "."],
                ["commit", "-qm", "initial"],
            ]:
                subprocess.run(["git", "-C", str(repo), *argv], check=True)
            base_rev = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            (repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n")

            cases = [
                ("design-architecture", [], "architecture.json"),
                ("review-code-changes", ["--base", base_rev], "review.json"),
                ("expand-automated-tests", [], "testing.json"),
                ("specify-feature", [], "spec.json"),
            ]
            for task, extra_args, deliverable in cases:
                with self.subTest(task=task):
                    plan_proc = self.run_cli(
                        "plan",
                        str(repo),
                        "--task",
                        task,
                        "--mode",
                        "comprehensive",
                        *extra_args,
                        "--json",
                    )
                    self.assertEqual(0, plan_proc.returncode, plan_proc.stderr)
                    plan_json = json.loads(plan_proc.stdout)["plan"]
                    self.assertEqual(3, len(plan_json["skills"]))
                    self.assertFalse(
                        any(
                            s.startswith("repo-audit-")
                            for s in plan_json["skills"] + plan_json["deferred_skills"]
                        )
                    )

                    ws = tmp_path / f"run-{task}"
                    init_proc = self.run_cli(
                        "init",
                        str(repo),
                        "--task",
                        task,
                        "--output",
                        str(ws),
                        *extra_args,
                        "--json",
                    )
                    self.assertEqual(0, init_proc.returncode, init_proc.stderr)
                    self.assertTrue((ws / deliverable).is_file())
                    self.assertFalse((ws / "audit.json").exists())
                    self.assertTrue((ws / "prompt.md").is_file())

                    val_proc = self.run_cli("validate-run", str(ws))
                    self.assertEqual(0, val_proc.returncode, val_proc.stderr)

                    # Invalid domain artifact causes validate-run to exit 1
                    starter_bytes = (ws / deliverable).read_bytes()
                    (ws / deliverable).write_text(
                        json.dumps({"schema_version": "1.0", "unexpected": True}),
                        encoding="utf-8",
                    )
                    bad_val_proc = self.run_cli("validate-run", str(ws))
                    self.assertEqual(1, bad_val_proc.returncode)
                    (ws / deliverable).write_bytes(starter_bytes)

                    # Report on workspace directory and on JSON file directly
                    rep_dir_html = tmp_path / f"report-dir-{task}.html"
                    rep_proc1 = self.run_cli(
                        "report", str(ws), "--output", str(rep_dir_html)
                    )
                    self.assertEqual(0, rep_proc1.returncode, rep_proc1.stderr)
                    self.assertTrue(rep_dir_html.is_file())

                    rep_file_html = tmp_path / f"report-file-{task}.html"
                    rep_proc2 = self.run_cli(
                        "report",
                        str(ws / deliverable),
                        "--output",
                        str(rep_file_html),
                    )
                    self.assertEqual(0, rep_proc2.returncode, rep_proc2.stderr)
                    self.assertTrue(rep_file_html.is_file())

                    # Audit-only commands fail with exit code 1 and clear audit.json guidance
                    prior_proc = self.run_cli(
                        "report",
                        str(ws),
                        "--prior",
                        str(ws),
                        "--output",
                        str(tmp_path / f"report-prior-{task}.html"),
                    )
                    self.assertEqual(1, prior_proc.returncode)
                    self.assertIn("audit.json", prior_proc.stderr)
                    self.assertIn(deliverable, prior_proc.stderr)

                    iss_proc = self.run_cli(
                        "issues", str(ws), "--output", str(tmp_path / f"iss-{task}")
                    )
                    self.assertEqual(1, iss_proc.returncode)
                    self.assertIn("audit.json", iss_proc.stderr)

                    sarif_proc = self.run_cli(
                        "export-sarif",
                        str(ws),
                        "--output",
                        str(tmp_path / f"{task}.sarif"),
                    )
                    self.assertEqual(1, sarif_proc.returncode)
                    self.assertIn("audit.json", sarif_proc.stderr)

                    # Even an empty {} domain file still triggers clear domain deliverable guidance
                    (ws / deliverable).write_text("{}", encoding="utf-8")
                    for empty_cmd in [
                        [
                            "issues",
                            str(ws),
                            "--output",
                            str(tmp_path / f"iss-empty-{task}"),
                        ],
                        [
                            "export-sarif",
                            str(ws / deliverable),
                            "--output",
                            str(tmp_path / f"empty-{task}.sarif"),
                        ],
                        [
                            "report",
                            str(ws),
                            "--prior",
                            str(ws / deliverable),
                            "--output",
                            str(tmp_path / f"prior-empty-{task}.html"),
                        ],
                    ]:
                        empty_proc = self.run_cli(*empty_cmd)
                        self.assertEqual(1, empty_proc.returncode)
                        self.assertIn("audit.json", empty_proc.stderr)
                        self.assertIn(deliverable, empty_proc.stderr)
                    (ws / deliverable).write_bytes(starter_bytes)

                    cmp_proc = self.run_cli(
                        "compare", str(ws / deliverable), str(ws / deliverable)
                    )
                    self.assertEqual(1, cmp_proc.returncode)
                    self.assertIn("audit.json", cmp_proc.stderr)

                    rec_proc = self.run_cli(
                        "reconcile",
                        str(ws),
                        "--finding",
                        "F-1",
                        "--state",
                        "open",
                        "--note",
                        "note",
                    )
                    self.assertEqual(1, rec_proc.returncode)
                    self.assertIn("audit.json", rec_proc.stderr)

    def test_workflow_companions_and_run_json_rejected_by_audit_only_cli_commands(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
            ws = root / "ws"
            init_proc = self.run_cli(
                "init", str(repo), "--task", "apply-fix", "--output", str(ws)
            )
            self.assertEqual(0, init_proc.returncode, init_proc.stderr)

            # Invalid companion file causes CLI validate-run to fail naming the companion file
            (ws / "plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_breakdowns": [],
                        "dependency_order": [],
                        "file_scopes": [],
                        "acceptance_criteria": [],
                    }
                ),
                encoding="utf-8",
            )
            bad_val = self.run_cli("validate-run", str(ws))
            self.assertEqual(1, bad_val.returncode)
            self.assertIn("plan.json", bad_val.stderr)
            (ws / "plan.json").unlink()
            ok_val = self.run_cli("validate-run", str(ws))
            self.assertEqual(0, ok_val.returncode, ok_val.stderr)

            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            non_audit_files = {
                "finding-verification.json": {
                    "finding_id": "F-1",
                    "reproduction_status": "supported",
                    "safe_control_outcomes": [{"control": "ok", "passed": True}],
                    "confidence_rating": "high",
                },
                "regression.json": {
                    "finding_id": "F-1",
                    "failure_boundaries": ["x"],
                    "test_command_traces": ["pytest"],
                    "assertion_expectations": ["assert ok"],
                },
                "plan.json": {
                    "task_breakdowns": [
                        {
                            "task_id": "W-1",
                            "title": "Fix",
                            "file_scopes": ["a.py"],
                            "acceptance_criteria": ["ok"],
                        }
                    ],
                    "dependency_order": ["W-1"],
                    "file_scopes": ["a.py"],
                    "acceptance_criteria": ["ok"],
                },
                "fix.json": {
                    "file_changes": ["a.py"],
                    "diff_summaries": ["guard input"],
                    "execution_results": [
                        {"command": "pytest", "exit_code": 0, "passed": True}
                    ],
                    "compatibility_limits": [],
                },
                "verification.json": {
                    "before_revision": "a1",
                    "after_revision": "b2",
                    "original_reproduced": True,
                    "regression_passed": True,
                    "safe_controls_passed": True,
                    "commands": ["pytest"],
                    "result": "ok",
                },
                "reconciliation.json": {
                    "finding_fingerprint_matches": [],
                    "remediation_state_transitions": [],
                    "unmapped_records": [],
                },
                "remediation.json": {"schema_version": "1.0", "records": []},
                "run.json": json.loads((ws / "run.json").read_text(encoding="utf-8")),
            }
            audit_file = ws / "audit.json"
            for fname, payload in non_audit_files.items():
                fpath = artifact_dir / fname
                fpath.write_text(json.dumps(payload), encoding="utf-8")
                for argv in (
                    ["report", str(fpath), "--output", str(root / f"rep-{fname}.html")],
                    ["issues", str(fpath), "--output", str(root / f"iss-{fname}")],
                    [
                        "export-sarif",
                        str(fpath),
                        "--output",
                        str(root / f"sarif-{fname}.json"),
                    ],
                    ["compare", str(fpath), str(audit_file)],
                ):
                    with self.subTest(artifact=fname, cmd=argv[0]):
                        proc = self.run_cli(*argv)
                        self.assertEqual(1, proc.returncode)
                        self.assertIn("audit.json", proc.stderr)
                        self.assertIn(fname, proc.stderr)

    def test_cli_profile_and_config_scope_domain_plans_and_copy_suite_assets(self):
        import shutil

        if not shutil.which("git"):
            self.skipTest("Git unavailable")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            repo = tmp_path / "profile_repo"
            repo.mkdir()
            (repo / "app.py").write_text("def calc(x):\n    return x * 2\n")
            for argv in [
                ["init", "-q"],
                ["config", "user.name", "Fixture"],
                ["config", "user.email", "fixture@example.invalid"],
                ["add", "."],
                ["commit", "-qm", "initial"],
            ]:
                subprocess.run(["git", "-C", str(repo), *argv], check=True)
            base_rev = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            (repo / "app.py").write_text("def calc(x):\n    return x * 2 + 1\n")
            config_file = ROOT / "examples/project-config.yaml"

            cases = [
                (
                    "design-architecture",
                    "software-architecture",
                    [],
                    "architecture.json",
                    [
                        "arch-decision-record",
                        "arch-boundary-specification",
                        "arch-component-decomposition",
                    ],
                    ["architecture-template.md", "architecture.schema.json"],
                ),
                (
                    "review-code-changes",
                    "code-review",
                    ["--base", base_rev],
                    "review.json",
                    [
                        "review-diff-analysis",
                        "review-invariant-violations",
                        "review-actionable-feedback",
                    ],
                    ["review-template.md", "review.schema.json"],
                ),
                (
                    "expand-automated-tests",
                    "automated-testing",
                    [],
                    "testing.json",
                    [
                        "test-gap-analysis",
                        "test-case-generation",
                        "test-suite-verification",
                    ],
                    ["testing-template.md", "testing.schema.json"],
                ),
            ]

            for (
                task,
                suite_name,
                extra_args,
                deliverable,
                expected_skills,
                asset_files,
            ) in cases:
                for budget in ("brief", "balanced", "deep"):
                    for flag_args in (
                        ["--profile", "modular-backend"],
                        ["--config", str(config_file)],
                    ):
                        with self.subTest(task=task, budget=budget, flags=flag_args[0]):
                            plan_proc = self.run_cli(
                                "plan",
                                str(repo),
                                "--task",
                                task,
                                "--budget",
                                budget,
                                *flag_args,
                                *extra_args,
                                "--json",
                            )
                            self.assertEqual(0, plan_proc.returncode, plan_proc.stderr)
                            plan_json = json.loads(plan_proc.stdout)["plan"]
                            self.assertEqual(expected_skills, plan_json["skills"])
                            self.assertEqual([], plan_json["deferred_skills"])
                            self.assertFalse(
                                any(
                                    s.startswith("repo-audit-")
                                    for s in plan_json["skills"]
                                    + plan_json["deferred_skills"]
                                )
                            )

                ws = tmp_path / f"ws-cfg-{task}"
                init_proc = self.run_cli(
                    "init",
                    str(repo),
                    "--task",
                    task,
                    "--budget",
                    "brief",
                    "--config",
                    str(config_file),
                    "--output",
                    str(ws),
                    *extra_args,
                    "--json",
                )
                self.assertEqual(0, init_proc.returncode, init_proc.stderr)
                self.assertTrue((ws / deliverable).is_file())
                self.assertFalse((ws / "audit.json").exists())
                for asset_name in asset_files:
                    copied = ws / "assets" / asset_name
                    canonical = (
                        ROOT / "shared" / suite_name / "resources/assets" / asset_name
                    )
                    self.assertTrue(copied.is_file(), f"Missing {copied}")
                    self.assertEqual(canonical.read_bytes(), copied.read_bytes())
                prompt_text = (ws / "prompt.md").read_text(encoding="utf-8")
                self.assertNotIn("repo-audit-", prompt_text)
                self.assertNotIn("audit.json", prompt_text)
                val_proc = self.run_cli("validate-run", str(ws))
                self.assertEqual(0, val_proc.returncode, val_proc.stderr)


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

    def test_dispatch_registry_maps_all_twenty_eight_commands(self):
        """COMMAND_REGISTRY must contain all 29 subcommands across all lifecycle groups."""
        expected_commands = {
            cmd for cmds in self.fred.LIFECYCLE_GROUPS.values() for cmd in cmds
        }
        self.assertEqual(29, len(expected_commands))
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


class FredStructuredCliErrorsTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FRED_PY), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def assertNoTraceback(self, stderr: str) -> None:
        self.assertNotIn("Traceback (most recent call last)", stderr)

    def test_malformed_yaml_on_plan_and_init_exits_1_with_hint_and_json_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
            bad_cfg = root / "malformed.yaml"
            bad_cfg.write_text("profile: [unclosed\n", encoding="utf-8")

            plan_proc = self.run_cli(
                "plan",
                str(repo),
                "--task",
                "investigate-bug",
                "--config",
                str(bad_cfg),
            )
            self.assertEqual(1, plan_proc.returncode)
            self.assertNoTraceback(plan_proc.stderr)
            self.assertIn("fred-skills:", plan_proc.stderr)
            self.assertIn("Malformed YAML", plan_proc.stderr)
            self.assertIn("line", plan_proc.stderr)
            self.assertIn("Hint:", plan_proc.stderr)

            init_proc = self.run_cli(
                "init",
                str(repo),
                "--task",
                "investigate-bug",
                "--config",
                str(bad_cfg),
                "--output",
                str(root / "out"),
            )
            self.assertEqual(1, init_proc.returncode)
            self.assertNoTraceback(init_proc.stderr)
            self.assertIn("Hint:", init_proc.stderr)

            json_proc = self.run_cli(
                "plan",
                str(repo),
                "--task",
                "investigate-bug",
                "--config",
                str(bad_cfg),
                "--json",
            )
            self.assertEqual(1, json_proc.returncode)
            self.assertEqual("", json_proc.stdout)
            payload = json.loads(json_proc.stderr)
            self.assertEqual("invalid_yaml", payload["code"])
            self.assertIn("Malformed YAML", payload["message"])
            self.assertTrue(payload["hint"])

    def test_profile_choices_on_cli_exit_2_and_yaml_profile_exits_1_listing_profiles(
        self,
    ):
        profiles = sorted(
            json.loads((ROOT / "catalog/profiles.json").read_text(encoding="utf-8"))[
                "profiles"
            ].keys()
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "main.py").write_text("x = 1\n", encoding="utf-8")

            cli_proc = self.run_cli(
                "plan",
                str(repo),
                "--task",
                "investigate-bug",
                "--profile",
                "unknown-profile",
            )
            self.assertEqual(2, cli_proc.returncode)
            self.assertNoTraceback(cli_proc.stderr)
            for prof in profiles:
                self.assertIn(prof, cli_proc.stderr)

            bad_prof_cfg = root / "bad-profile.yaml"
            bad_prof_cfg.write_text("profile: unknown-profile\n", encoding="utf-8")
            yaml_proc = self.run_cli(
                "plan",
                str(repo),
                "--task",
                "investigate-bug",
                "--config",
                str(bad_prof_cfg),
            )
            self.assertEqual(1, yaml_proc.returncode)
            self.assertNoTraceback(yaml_proc.stderr)
            self.assertIn("Hint:", yaml_proc.stderr)
            for prof in profiles:
                self.assertIn(prof, yaml_proc.stderr)

    def test_json_arrays_and_primitives_exit_1_with_zero_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arr_file = root / "array.json"
            arr_file.write_text("[1, 2]", encoding="utf-8")
            prim_file = root / "primitive.json"
            prim_file.write_text("42", encoding="utf-8")

            ws_arr = root / "ws-arr"
            ws_arr.mkdir()
            (ws_arr / "run.json").write_text("[]", encoding="utf-8")
            (ws_arr / "audit.json").write_text("[]", encoding="utf-8")

            ws_prim = root / "ws-prim"
            ws_prim.mkdir()
            (ws_prim / "run.json").write_text('"scalar"', encoding="utf-8")
            (ws_prim / "audit.json").write_text("true", encoding="utf-8")

            invocations = [
                ("validate-run", str(arr_file)),
                ("validate-run", str(ws_arr)),
                ("validate-run", str(ws_prim)),
                ("import-sarif", str(arr_file), "--output", str(root / "sarif1.json")),
                ("import-sarif", str(prim_file), "--output", str(root / "sarif2.json")),
                ("compare", str(arr_file), str(arr_file)),
                ("compare", str(prim_file), str(prim_file)),
                ("verify-release", str(arr_file)),
                ("verify-release", str(prim_file)),
            ]
            for args in invocations:
                with self.subTest(args=args):
                    proc = self.run_cli(*args)
                    self.assertEqual(1, proc.returncode)
                    self.assertNoTraceback(proc.stderr)
                    self.assertIn("fred-skills:", proc.stderr)
                    self.assertIn("Hint:", proc.stderr)

    def test_validate_run_empty_ledgers_prints_all_23_schema_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "empty-ledgers"
            ws.mkdir()
            (ws / "run.json").write_text("{}\n", encoding="utf-8")
            (ws / "audit.json").write_text("{}\n", encoding="utf-8")

            proc = self.run_cli("validate-run", str(ws))
            self.assertEqual(1, proc.returncode)
            self.assertNoTraceback(proc.stderr)
            self.assertNotEqual("fred-skills: 'snapshot'", proc.stderr.splitlines()[0])
            self.assertEqual(23, proc.stderr.count("is a required property"))
            self.assertIn("Hint:", proc.stderr)

    def test_compare_and_verify_release_accept_directories_and_hint_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

            ws1 = root / "run1"
            ws2 = root / "run2"
            self.assertEqual(
                0,
                self.run_cli(
                    "init",
                    str(repo),
                    "--task",
                    "investigate-bug",
                    "--budget",
                    "brief",
                    "--output",
                    str(ws1),
                ).returncode,
            )
            self.assertEqual(
                0,
                self.run_cli(
                    "init",
                    str(repo),
                    "--task",
                    "investigate-bug",
                    "--budget",
                    "brief",
                    "--output",
                    str(ws2),
                ).returncode,
            )
            cmp_ok = self.run_cli("compare", str(ws1), str(ws2), "--json")
            self.assertEqual(0, cmp_ok.returncode, cmp_ok.stderr)
            self.assertIn("introduced", json.loads(cmp_ok.stdout))

            empty_dir = root / "empty-dir"
            empty_dir.mkdir()
            cmp_miss = self.run_cli("compare", str(empty_dir), str(ws2))
            self.assertEqual(1, cmp_miss.returncode)
            self.assertNoTraceback(cmp_miss.stderr)
            self.assertIn("audit.json", cmp_miss.stderr)
            self.assertIn("Hint:", cmp_miss.stderr)

            rel_dir = root / "rel"
            self.assertEqual(
                0,
                self.run_cli(
                    "release", "--pack", "starter", "--output", str(rel_dir)
                ).returncode,
            )
            ver_ok = self.run_cli("verify-release", str(rel_dir), "--json")
            self.assertEqual(0, ver_ok.returncode, ver_ok.stderr)
            self.assertGreater(json.loads(ver_ok.stdout)["verified_archives"], 0)

            ver_miss = self.run_cli("verify-release", str(empty_dir))
            self.assertEqual(1, ver_miss.returncode)
            self.assertNoTraceback(ver_miss.stderr)
            self.assertIn("manifest.json", ver_miss.stderr)
            self.assertIn("Hint:", ver_miss.stderr)

    def test_recover_install_missing_operation_json_exits_1_and_advises_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "project"
            dest.mkdir()
            proc = self.run_cli(
                "recover-install",
                "--destination",
                str(dest),
                "--host",
                "codex",
                "--confirm-interrupted",
            )
            self.assertEqual(1, proc.returncode)
            self.assertNoTraceback(proc.stderr)
            self.assertIn("operation.json", proc.stderr)
            self.assertIn("Hint:", proc.stderr)
            self.assertIn("installed", proc.stderr)

    def test_plan_and_init_accept_tactical_scope_flags_and_persist_dependencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "app"
            (repo_dir / "src/orders").mkdir(parents=True)
            (repo_dir / "src/orders/handler.py").write_text(
                "def place_order(cart_id: str) -> bool:\n    return bool(cart_id)\n",
                encoding="utf-8",
            )
            (repo_dir / "pyproject.toml").write_text(
                '[project]\nname = "orders"\ndependencies = ["sqlalchemy==2.0.35"]\n',
                encoding="utf-8",
            )
            ws = Path(tmp) / "run-ws"

            plan_proc = self.run_cli(
                "plan",
                str(repo_dir),
                "--task",
                "plan-findings",
                "--budget",
                "brief",
                "--directory",
                "src/orders",
                "--symbol",
                "place_order",
                "--allowed-paths",
                "src/orders",
                "--do-not-modify-paths",
                "pyproject.toml",
                "--issue-number",
                "77",
                "--json",
            )
            self.assertEqual(0, plan_proc.returncode, plan_proc.stderr)
            plan_payload = json.loads(plan_proc.stdout)
            self.assertEqual("src/orders", plan_payload["plan"]["directory"])
            self.assertEqual("place_order", plan_payload["plan"]["symbol"])
            self.assertEqual(["src/orders"], plan_payload["plan"]["allowed_paths"])
            self.assertEqual(
                ["pyproject.toml"], plan_payload["plan"]["do_not_modify_paths"]
            )
            self.assertEqual(77, plan_payload["plan"]["issue_number"])
            self.assertEqual(
                "2.0.35",
                plan_payload["preflight"]["dependencies"]["packages"]["sqlalchemy"],
            )

            init_proc = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "plan-findings",
                "--budget",
                "brief",
                "--output",
                str(ws),
                "--directory",
                "src/orders",
                "--symbol",
                "place_order",
                "--allowed-paths",
                "src/orders",
                "--do-not-modify-paths",
                "pyproject.toml",
                "--issue-number",
                "77",
                "--json",
            )
            self.assertEqual(0, init_proc.returncode, init_proc.stderr)
            val_proc = self.run_cli("validate-run", str(ws))
            self.assertEqual(0, val_proc.returncode, val_proc.stderr)

            saved_run = json.loads((ws / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "2.0.35", saved_run["dependencies"]["packages"]["sqlalchemy"]
            )
            prompt_md = (ws / "prompt.md").read_text(encoding="utf-8")
            self.assertIn("`sqlalchemy` (2.0.35)", prompt_md)
            self.assertIn("`place_order`", prompt_md)
            self.assertIn("./src/orders/handler.py", prompt_md)
            self.assertIn("gh issue view 77", prompt_md)

    def test_cli_single_task_advance_loop_and_multi_suite_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "app"
            repo_dir.mkdir()
            (repo_dir / "service.py").write_text(
                "def check() -> int:\n    return 1\n", encoding="utf-8"
            )
            ws_fix = Path(tmp) / "ws-fix"
            init_proc = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "apply-fix",
                "--allow-fixes",
                "--single-task",
                "--output",
                str(ws_fix),
                "--json",
            )
            self.assertEqual(0, init_proc.returncode, init_proc.stderr)
            prompt_1 = (ws_fix / "prompt.md").read_text(encoding="utf-8")
            self.assertIn(
                "Single-task loop pass 1 of 6 — execute only this active skill: repo-verify-finding.",
                prompt_1,
            )
            self.assertNotIn("### `repo-apply-fix`", prompt_1)

            (repo_dir / "service.py").write_text(
                "def check() -> int:\n    return 2\n", encoding="utf-8"
            )
            adv_proc = self.run_cli(
                "advance",
                str(ws_fix),
                "--note",
                "Verified root cause in service.py",
                "--json",
            )
            self.assertEqual(0, adv_proc.returncode, adv_proc.stderr)
            adv_payload = json.loads(adv_proc.stdout)
            self.assertEqual("repo-verify-finding", adv_payload["completed_skill"])
            self.assertEqual("repo-specify-regression", adv_payload["active_skill"])
            self.assertEqual(["service.py"], adv_payload["changed_files"])
            val_proc = self.run_cli("validate-run", str(ws_fix), "--json")
            self.assertEqual(0, val_proc.returncode, val_proc.stderr)

            mem_audit_path = Path(tmp) / "audit-memory.json"
            mem_audit_proc = self.run_cli(
                "memory",
                str(ws_fix),
                "--output",
                str(mem_audit_path),
                "--json",
            )
            self.assertEqual(0, mem_audit_proc.returncode, mem_audit_proc.stderr)
            self.assertIn("contracts", json.loads(mem_audit_proc.stdout))

            # Domain deliverable CLI memory export (architecture.json)
            ws_arch = Path(tmp) / "ws-arch"
            arch_init = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "design-architecture",
                "--output",
                str(ws_arch),
                "--json",
            )
            self.assertEqual(0, arch_init.returncode, arch_init.stderr)
            mem_arch_path = Path(tmp) / "arch-memory.json"
            mem_arch_proc = self.run_cli(
                "memory",
                str(ws_arch),
                "--output",
                str(mem_arch_path),
                "--json",
            )
            self.assertEqual(0, mem_arch_proc.returncode, mem_arch_proc.stderr)
            mem_arch_summary = json.loads(mem_arch_proc.stdout)
            self.assertEqual("architecture.json", mem_arch_summary["deliverable"])
            mem_arch_doc = json.loads(mem_arch_path.read_text(encoding="utf-8"))
            self.assertIn("service.py", mem_arch_doc["file_hashes"])

            # Install standalone session-hygiene pack via CLI
            install_dest = Path(tmp) / "dest"
            install_dest.mkdir()
            inst_proc = self.run_cli(
                "install",
                "--destination",
                str(install_dest),
                "--host",
                "codex",
                "--pack",
                "session-hygiene",
                "--json",
            )
            self.assertEqual(0, inst_proc.returncode, inst_proc.stderr)
            inst_payload = json.loads(inst_proc.stdout)
            self.assertEqual(
                ["session-handoff", "terse-output-commit"],
                sorted(inst_payload["skills"]),
            )

    def test_cli_source_audit_checkpoint_completed_skills_resume_and_reconciliation_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            (repo_dir / "app.py").write_text("limit = 5\n", encoding="utf-8")

            ws_src = Path(tmp) / "ws-src"
            init_src = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "understand-repo",
                "--mode",
                "comprehensive",
                "--budget",
                "deep",
                "--output",
                str(ws_src),
                "--json",
            )
            self.assertEqual(0, init_src.returncode, init_src.stderr)
            src_run = json.loads(init_src.stdout)["run"]
            first_slice = src_run["plan"]["skills"][:3]

            chk_proc = self.run_cli(
                "checkpoint",
                str(ws_src),
                "--state",
                "paused",
                "--note",
                "Finished first slice",
                "--completed-skill",
                first_slice[0],
                "--completed-skill",
                f"{first_slice[1]},{first_slice[2]}",
                "--json",
            )
            self.assertEqual(0, chk_proc.returncode, chk_proc.stderr)
            chk_run = json.loads(chk_proc.stdout)
            self.assertEqual(
                first_slice, chk_run["checkpoints"][-1]["completed_skills"]
            )
            self.assertEqual(1, chk_run["checkpoints"][-1]["active_phase"])

            res_proc = self.run_cli("resume", str(ws_src), "--json")
            self.assertEqual(0, res_proc.returncode, res_proc.stderr)
            res_data = json.loads(res_proc.stdout)
            self.assertEqual(1, res_data["active_phase"])
            self.assertEqual(src_run["plan"]["skills"][3:6], res_data["active_skills"])

            # Seed audit.json with a finding and initialize apply-fix with --source-audit
            fixture_audit = json.loads(
                (ROOT / "tests/fixtures/audit-valid.json").read_text(encoding="utf-8")
            )
            fixture_audit["run_id"] = src_run["run_id"]
            fixture_audit["repository"] = json.loads(
                (ws_src / "audit.json").read_text(encoding="utf-8")
            )["repository"]
            fixture_audit["evidence"][0]["path"] = "app.py"
            (ws_src / "audit.json").write_text(
                json.dumps(fixture_audit), encoding="utf-8"
            )

            ws_wf = Path(tmp) / "ws-wf"
            init_wf = self.run_cli(
                "init",
                str(repo_dir),
                "--task",
                "apply-fix",
                "--source-audit",
                str(ws_src),
                "--allow-fixes",
                "--output",
                str(ws_wf),
                "--json",
            )
            self.assertEqual(0, init_wf.returncode, init_wf.stderr)
            wf_audit = json.loads((ws_wf / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(wf_audit["findings"]))

            # Write standalone reconciliation.json (without remediation.json) and verify CLI report picks it up
            (ws_wf / "reconciliation.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "finding_fingerprint_matches": [
                            {
                                "fingerprint": "fp-demo",
                                "finding_id": "F-1",
                                "match_status": "exact",
                            }
                        ],
                        "remediation_state_transitions": [
                            {
                                "finding_id": "F-1",
                                "fingerprint": "fp-demo",
                                "previous_state": "open",
                                "new_state": "in-progress",
                                "reason": "Triaged via standalone reconciliation.json",
                            }
                        ],
                        "unmapped_records": [],
                    }
                ),
                encoding="utf-8",
            )
            rep_html = Path(tmp) / "wf-report.html"
            rep_proc = self.run_cli(
                "report", str(ws_wf), "--output", str(rep_html), "--json"
            )
            self.assertEqual(0, rep_proc.returncode, rep_proc.stderr)
            rendered_html = rep_html.read_text(encoding="utf-8")
            self.assertIn("in-progress", rendered_html)
            self.assertIn("Triaged via standalone reconciliation.json", rendered_html)

    def test_cli_handles_git_and_subprocess_timeouts_cleanly(self) -> None:
        import contextlib
        import io

        sys.path.insert(0, str(ROOT / "scripts"))
        import fred

        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

            # 1. --base diff planning raises FredCliError(code="git_timeout") -> clean exit 1
            err_json = io.StringIO()
            with (
                patch("fredlib.repository.shutil.which", return_value="/usr/bin/git"),
                patch(
                    "fredlib.repository.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["git", "rev-parse", "HEAD"], timeout=30
                    ),
                ),
                contextlib.redirect_stderr(err_json),
            ):
                rc_json = fred.main(
                    [
                        "plan",
                        str(repo_dir),
                        "--task",
                        "review-pr",
                        "--mode",
                        "changes",
                        "--base",
                        "HEAD",
                        "--json",
                    ]
                )
            self.assertEqual(1, rc_json)
            err_payload = json.loads(err_json.getvalue())
            self.assertEqual("git_timeout", err_payload["code"])
            self.assertTrue(err_payload["hint"])

            # 2. Optional Git timeout during preflight falls back cleanly -> exit 0
            out_pre, err_pre = io.StringIO(), io.StringIO()
            with (
                patch("fredlib.repository.shutil.which", return_value="/usr/bin/git"),
                patch(
                    "fredlib.repository.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["git", "ls-files"], timeout=30
                    ),
                ),
                contextlib.redirect_stdout(out_pre),
                contextlib.redirect_stderr(err_pre),
            ):
                rc_pre = fred.main(["preflight", str(repo_dir), "--full", "--json"])
            self.assertEqual(0, rc_pre, err_pre.getvalue())
            pre_payload = json.loads(out_pre.getvalue())
            self.assertIn("app.py", pre_payload["snapshot"]["files"])
            self.assertEqual("unversioned", pre_payload["repository"]["revision"])

            # 3. Raw subprocess.TimeoutExpired at CLI boundary -> clean exit 1 without traceback
            err_raw = io.StringIO()
            with (
                patch(
                    "fred.dispatch",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["git", "status"], timeout=30
                    ),
                ),
                contextlib.redirect_stderr(err_raw),
            ):
                rc_raw = fred.main(["tasks"])
            self.assertEqual(1, rc_raw)
            self.assertIn("fred-skills: Subprocess timed out", err_raw.getvalue())
            self.assertIn("Hint:", err_raw.getvalue())
            self.assertNotIn("Traceback", err_raw.getvalue())

    def test_validate_run_advance_and_report_enforce_domain_validators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
            for git_args in (
                ["init"],
                ["config", "user.email", "test@example.com"],
                ["config", "user.name", "Test"],
                ["add", "."],
                ["commit", "-m", "initial"],
            ):
                subprocess.run(
                    ["git", *git_args], cwd=repo_dir, check=True, capture_output=True
                )
            (repo_dir / "app.py").write_text("x = 2\n", encoding="utf-8")
            subprocess.run(
                ["git", "commit", "-am", "second"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
            )

            def init_loop_ws(name: str, task: str, *extra: str) -> tuple[Path, dict]:
                ws = tmp_path / name
                proc = self.run_cli(
                    "init",
                    str(repo_dir),
                    "--task",
                    task,
                    *extra,
                    "--single-task",
                    "--output",
                    str(ws),
                    "--json",
                )
                self.assertEqual(0, proc.returncode, proc.stderr)
                run_doc = json.loads((ws / "run.json").read_text(encoding="utf-8"))
                return ws, run_doc

            def assert_all_three_fail(ws: Path, label: str) -> None:
                v_proc = self.run_cli("validate-run", str(ws))
                self.assertNotEqual(
                    0, v_proc.returncode, f"validate-run passed: {label}"
                )
                a_proc = self.run_cli("advance", str(ws), "--note", "step done")
                self.assertNotEqual(0, a_proc.returncode, f"advance passed: {label}")
                r_proc = self.run_cli(
                    "report", str(ws), "--output", str(tmp_path / f"{label}.html")
                )
                self.assertNotEqual(0, r_proc.returncode, f"report passed: {label}")

            # 1. Review: approved critical review & empty request_changes review
            rev_ws, rev_run = init_loop_ws(
                "rev-ws", "review-code-changes", "--base", "HEAD~1"
            )
            (rev_ws / "review.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": rev_run["run_id"],
                        "snapshot": rev_run["snapshot"]["digest"],
                        "verdict": "approve",
                        "summary": "Approved despite critical defect.",
                        "findings": [
                            {
                                "id": "REV-1",
                                "title": "Auth bypass",
                                "severity": "critical",
                                "file": "app.py",
                                "description": "Auth bypass in handler.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            assert_all_three_fail(rev_ws, "approved-critical-review")

            (rev_ws / "review.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": rev_run["run_id"],
                        "snapshot": rev_run["snapshot"]["digest"],
                        "verdict": "request_changes",
                        "summary": "Requesting changes with zero findings.",
                        "findings": [],
                    }
                ),
                encoding="utf-8",
            )
            assert_all_three_fail(rev_ws, "empty-request-changes")

            # 2. Spec: empty-check converged & out_of_scope overlap
            spec_ws, _ = init_loop_ws("spec-ws", "specify-feature")
            base_spec = {
                "schema_version": "1.0",
                "title": "App Spec",
                "status": "converged",
                "interview_decisions": [],
                "new_file_scopes": ["src/new/feature.py"],
                "out_of_scope": ["src/legacy/feature.py"],
                "verification_commands": ["python -m unittest"],
                "convergence_checks": [],
            }
            (spec_ws / "spec.json").write_text(json.dumps(base_spec), encoding="utf-8")
            assert_all_three_fail(spec_ws, "empty-converged-spec")

            overlap_spec = {
                **base_spec,
                "new_file_scopes": ["src/legacy/"],
                "out_of_scope": ["src/legacy/feature.py"],
                "convergence_checks": [
                    {"id": "CHK-1", "target": "REQ-1", "status": "aligned"}
                ],
            }
            (spec_ws / "spec.json").write_text(
                json.dumps(overlap_spec), encoding="utf-8"
            )
            assert_all_three_fail(spec_ws, "overlap-spec")

            # 3. Architecture: duplicate boundary names
            arch_ws, arch_run = init_loop_ws("arch-ws", "design-architecture")
            (arch_ws / "architecture.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": arch_run["run_id"],
                        "snapshot": arch_run["snapshot"]["digest"],
                        "title": "System Design",
                        "status": "proposed",
                        "boundaries": [
                            {"name": "core", "responsibility": "Domain logic."},
                            {"name": "core", "responsibility": "Duplicate boundary."},
                        ],
                        "decisions": [],
                    }
                ),
                encoding="utf-8",
            )
            assert_all_three_fail(arch_ws, "duplicate-boundaries")

            # 4. Testing: unsafe path in test_cases
            test_ws, test_run = init_loop_ws("test-ws", "expand-automated-tests")
            (test_ws / "testing.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": test_run["run_id"],
                        "snapshot": test_run["snapshot"]["digest"],
                        "component": "app",
                        "framework": "unittest",
                        "test_cases": [
                            {
                                "id": "TC-1",
                                "target": "app.x",
                                "scenario": "Escape check",
                                "oracle": "Raises ValueError",
                                "priority": "p1",
                                "status": "specified",
                                "file": "../outside/test_app.py",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            assert_all_three_fail(test_ws, "unsafe-testing-path")

            # 5. Workflow companion: unsafe path in fix.json
            wf_ws, _ = init_loop_ws("wf-ws", "verify-fix")
            (wf_ws / "fix.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "file_changes": [".git/config"],
                        "diff_summaries": ["Touched git config."],
                        "execution_results": ["1 test passed."],
                        "compatibility_limits": [],
                    }
                ),
                encoding="utf-8",
            )
            assert_all_three_fail(wf_ws, "unsafe-workflow-companion-path")


if __name__ == "__main__":
    unittest.main()

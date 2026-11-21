"""Tests for standalone GitHub Pages workflow triggers, permissions, environment rules, SHA pins, and site staging."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_experience
import build_site
import verify_workflow_actions
from skill_tools import (
    extract_markdown_headings,
    extract_markdown_preamble,
    extract_markdown_section,
    extract_markdown_tables,
    extract_relative_links,
    resolve_relative_link_target,
)

PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"

CHECKOUT_SHA = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON_SHA = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
CONFIGURE_PAGES_SHA = "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d"
UPLOAD_PAGES_SHA = (
    "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9"
)
DEPLOY_PAGES_SHA = "actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346"
HOSTED_URL = "https://fderuiter.github.io/fred-skills"
HOSTED_EXPLORER_URL = f"{HOSTED_URL}/docs/explorer.html"

_PASSTHROUGH_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
)

_FAKE_GH_PY = textwrap.dedent(
    """\
    import json
    import os
    import sys
    from pathlib import Path

    state_path = Path(os.environ["FAKE_GH_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    args = sys.argv[1:]
    state.setdefault("calls", []).append(args)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    if not os.environ.get("GH_TOKEN"):
        sys.stderr.write("gh: GH_TOKEN authentication required\\n")
        sys.exit(1)

    if len(args) < 2 or args[0] != "api":
        sys.stderr.write(f"unexpected gh invocation: {args!r}\\n")
        sys.exit(2)

    if state.get("api_error"):
        sys.stderr.write(str(state["api_error"]) + "\\n")
        sys.exit(1)

    endpoint = args[1]
    if "/actions/workflows/validate.yml/runs" not in endpoint:
        sys.stderr.write(f"unexpected gh api endpoint: {endpoint}\\n")
        sys.exit(2)

    if "raw_stdout" in state:
        sys.stdout.write(str(state["raw_stdout"]))
        sys.exit(0)

    sys.stdout.write(json.dumps({"workflow_runs": state.get("workflow_runs", [])}) + "\\n")
    sys.exit(0)
    """
)


def _write_lf(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _isolated_env(tmp: Path, extra_env: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV_KEYS if k in os.environ}
    home_dir = tmp / ".test-home"
    home_dir.mkdir(exist_ok=True)
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(extra_env)
    return env


def _install_fake_gh(
    base_dir: Path, initial_state: dict[str, Any] | None = None
) -> tuple[dict[str, str], Path]:
    bin_dir = base_dir / ".fake-gh-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake_gh_py = bin_dir / "fake_gh.py"
    _write_lf(fake_gh_py, _FAKE_GH_PY)
    fake_gh_sh = bin_dir / "gh"
    _write_lf(
        fake_gh_sh,
        '#!/bin/sh\nexec "$FAKE_GH_PYTHON" "$FAKE_GH_SCRIPT" "$@"\n',
    )
    fake_gh_sh.chmod(0o755)

    state_path = base_dir / ".fake-gh-state.json"
    _write_lf(state_path, json.dumps(initial_state or {"workflow_runs": []}))
    gh_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_GH_PYTHON": sys.executable,
        "FAKE_GH_SCRIPT": str(fake_gh_py),
        "FAKE_GH_STATE": str(state_path),
    }
    return gh_env, state_path


def _parse_github_output(output_file: Path) -> dict[str, str]:
    if not output_file.is_file():
        return {}
    parsed: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            parsed[key] = val
    return parsed


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test Maintainer",
            "-c",
            "user.email=test@example.com",
            "-c",
            "core.autocrlf=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _eval_if_condition(
    condition: str,
    github_ctx: dict[str, Any],
    needs_ctx: dict[str, Any] | None = None,
) -> bool:
    """Evaluate a GitHub Actions boolean `if` expression against synthetic `github` and `needs` contexts."""
    cleaned = condition.strip()
    if cleaned.startswith("${{") and cleaned.endswith("}}"):
        cleaned = cleaned[3:-2].strip()
    py_expr = cleaned.replace("&&", " and ").replace("||", " or ")

    def _to_namespace(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{k: _to_namespace(v) for k, v in value.items()})
        return value

    result = eval(
        py_expr,
        {"__builtins__": {}},
        {
            "github": _to_namespace(github_ctx),
            "needs": _to_namespace(needs_ctx or {}),
        },
    )
    return bool(result)


class PagesWorkflowTests(unittest.TestCase):
    """Verify standalone GitHub Pages workflow triggers, gates, permissions, pins, and staging."""

    workflow_text: str
    workflow_data: dict[Any, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = PAGES_WORKFLOW.read_text(encoding="utf-8")
        loaded = yaml.safe_load(cls.workflow_text)
        assert isinstance(loaded, dict)
        cls.workflow_data = loaded

    def _triggers(self) -> dict[str, Any]:
        raw: Any = self.workflow_data.get("on") or self.workflow_data.get(True) or {}
        assert isinstance(raw, dict)
        return raw

    def _run_shell_script(
        self,
        script: str,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as script_dir:
            script_path = Path(script_dir) / "pages_step.sh"
            _write_lf(script_path, script)
            return subprocess.run(
                ["sh", script_path.as_posix()],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def _run_preflight_gate(
        self,
        cwd: Path,
        env_vars: dict[str, str],
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        preflight_steps = self.workflow_data["jobs"]["preflight"]["steps"]
        gate_step = next(s for s in preflight_steps if s.get("id") == "gate")
        output_file = cwd / ".test-pages-output.txt"
        output_file.unlink(missing_ok=True)
        summary_file = cwd / ".test-pages-summary.md"
        summary_file.unlink(missing_ok=True)
        merged_env = _isolated_env(
            cwd,
            {
                "PYTHON_BIN": sys.executable,
                "GITHUB_OUTPUT": str(output_file),
                "GITHUB_STEP_SUMMARY": str(summary_file),
                **env_vars,
            },
        )
        proc = self._run_shell_script(
            str(gate_step["run"]),
            cwd=cwd,
            env=merged_env,
        )
        return proc, _parse_github_output(output_file)

    def test_workflow_yaml_syntax_triggers_permissions_and_concurrency(self) -> None:
        """pages.yml omits top-level concurrency, keeps preflight unserialized, and sets job concurrency on build/deploy."""
        self.assertEqual(self.workflow_data.get("permissions"), {"contents": "read"})
        self.assertNotIn("concurrency", self.workflow_data)

        triggers = self._triggers()
        self.assertEqual(set(triggers.keys()), {"workflow_run", "workflow_dispatch"})
        wf_run = triggers["workflow_run"]
        self.assertEqual(
            wf_run.get("workflows"), ["Validate skill packages", "Release"]
        )
        self.assertEqual(wf_run.get("types"), ["completed"])
        self.assertEqual(wf_run.get("branches"), ["main"])

        jobs = self.workflow_data["jobs"]
        self.assertNotIn("concurrency", jobs["preflight"])
        self.assertEqual(
            jobs["preflight"].get("permissions"),
            {"contents": "read", "actions": "read"},
        )
        self.assertEqual(
            jobs["build"].get("concurrency"),
            {
                "group": "skills-pages-build-${{ github.ref }}",
                "cancel-in-progress": True,
            },
        )
        self.assertEqual(
            jobs["deploy"].get("concurrency"),
            {
                "group": "skills-pages-deploy-${{ github.ref }}",
                "cancel-in-progress": False,
            },
        )
        for job_name, job_cfg in jobs.items():
            job_perms = job_cfg.get("permissions", {})
            if job_name == "deploy":
                self.assertEqual(job_perms.get("pages"), "write")
            else:
                self.assertNotEqual(job_perms.get("pages"), "write", job_name)

        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(validate_data.get("permissions"), {"contents": "read"})
        for job in validate_data.get("jobs", {}).values():
            job_perms = job.get("permissions", {})
            self.assertNotEqual(job_perms.get("pages"), "write")
            for step in job.get("steps", []):
                self.assertNotIn("deploy-pages", str(step.get("uses", "")))

    def test_jobs_steps_environment_and_verified_action_shas(self) -> None:
        """pages.yml wires preflight -> build -> deploy, single-pass staging, and pinned SHAs."""
        jobs = self.workflow_data["jobs"]
        self.assertIn("preflight", jobs)
        self.assertIn("build", jobs)
        self.assertIn("deploy", jobs)

        preflight_job = jobs["preflight"]
        self.assertEqual(
            preflight_job.get("outputs"),
            {
                "should_deploy": "${{ steps.gate.outputs.should_deploy }}",
                "target_sha": "${{ steps.gate.outputs.target_sha }}",
            },
        )
        preflight_checkout = preflight_job["steps"][0]
        self.assertEqual(preflight_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            preflight_checkout.get("with", {}).get("ref"),
            "${{ github.event.workflow_run.head_sha || github.sha }}",
        )
        self.assertEqual(preflight_checkout.get("with", {}).get("fetch-depth"), 0)
        self.assertIs(
            preflight_checkout.get("with", {}).get("persist-credentials"), False
        )

        build_job = jobs["build"]
        self.assertEqual(build_job.get("needs"), "preflight")
        self.assertEqual(
            build_job.get("if"),
            "needs.preflight.outputs.should_deploy == 'true'",
        )
        self.assertEqual(
            build_job.get("permissions"),
            {"contents": "read", "pages": "read"},
        )
        build_steps = build_job["steps"]

        checkout_step = build_steps[0]
        self.assertEqual(checkout_step.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            checkout_step.get("with", {}).get("ref"),
            "${{ needs.preflight.outputs.target_sha }}",
        )
        self.assertIs(checkout_step.get("with", {}).get("persist-credentials"), False)

        setup_py_step = next(
            s for s in build_steps if s.get("uses") == SETUP_PYTHON_SHA
        )
        self.assertEqual(setup_py_step.get("with", {}).get("python-version"), "3.12")
        self.assertNotIn("cache", setup_py_step.get("with", {}))
        for step in build_steps:
            self.assertNotIn("pip install", str(step.get("run", "")))

        stage_steps = [
            s for s in build_steps if "scripts/build_experience.py" in s.get("run", "")
        ]
        self.assertEqual(len(stage_steps), 1)
        stage_step = stage_steps[0]
        self.assertIn(
            "python scripts/build_experience.py --check --stage-site dist/site",
            stage_step["run"],
        )
        self.assertEqual(stage_step["run"].count("scripts/build_experience.py"), 1)
        self.assertIn("dist/site/.nojekyll", stage_step["run"])
        for step in build_steps:
            self.assertNotIn("scripts/build_site.py", step.get("run", ""))

        configure_step = next(
            s for s in build_steps if s.get("uses") == CONFIGURE_PAGES_SHA
        )
        self.assertIsNotNone(configure_step)

        upload_step = next(s for s in build_steps if s.get("uses") == UPLOAD_PAGES_SHA)
        self.assertEqual(upload_step.get("with", {}).get("path"), "dist/site/")
        self.assertIs(upload_step.get("with", {}).get("include-hidden-files"), True)

        deploy_job = jobs["deploy"]
        self.assertEqual(deploy_job.get("needs"), ["preflight", "build"])
        self.assertEqual(
            deploy_job.get("if"),
            "needs.preflight.outputs.should_deploy == 'true'",
        )
        self.assertEqual(
            deploy_job.get("permissions"),
            {"pages": "write", "id-token": "write"},
        )
        self.assertEqual(
            deploy_job.get("environment"),
            {
                "name": "github-pages",
                "url": "${{ steps.deployment.outputs.page_url }}",
            },
        )
        deploy_steps = deploy_job["steps"]
        self.assertEqual(len(deploy_steps), 1)
        self.assertEqual(deploy_steps[0].get("id"), "deployment")
        self.assertEqual(deploy_steps[0].get("uses"), DEPLOY_PAGES_SHA)

    def test_job_if_gates_block_pull_requests_failures_and_non_main_branches(
        self,
    ) -> None:
        """Failed runs, pull_request runs, and non-main branches skip preflight; build/deploy require should_deploy == 'true'."""
        preflight_if = str(self.workflow_data["jobs"]["preflight"]["if"])

        valid_wf_run = {
            "event_name": "workflow_run",
            "ref": "refs/heads/main",
            "event": {
                "workflow_run": {
                    "conclusion": "success",
                    "event": "push",
                    "head_branch": "main",
                }
            },
        }
        self.assertTrue(_eval_if_condition(preflight_if, valid_wf_run))

        valid_dispatch = {
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "event": {
                "workflow_run": {"conclusion": "", "event": "", "head_branch": ""}
            },
        }
        self.assertTrue(_eval_if_condition(preflight_if, valid_dispatch))

        blocked_contexts = [
            {
                "event_name": "workflow_run",
                "ref": "refs/heads/main",
                "event": {
                    "workflow_run": {
                        "conclusion": "failure",
                        "event": "push",
                        "head_branch": "main",
                    }
                },
            },
            {
                "event_name": "workflow_run",
                "ref": "refs/heads/main",
                "event": {
                    "workflow_run": {
                        "conclusion": "cancelled",
                        "event": "push",
                        "head_branch": "main",
                    }
                },
            },
            {
                "event_name": "workflow_run",
                "ref": "refs/heads/main",
                "event": {
                    "workflow_run": {
                        "conclusion": "success",
                        "event": "pull_request",
                        "head_branch": "main",
                    }
                },
            },
            {
                "event_name": "workflow_run",
                "ref": "refs/heads/main",
                "event": {
                    "workflow_run": {
                        "conclusion": "success",
                        "event": "push",
                        "head_branch": "feature/branch",
                    }
                },
            },
            {
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/feature/branch",
                "event": {
                    "workflow_run": {
                        "conclusion": "",
                        "event": "",
                        "head_branch": "",
                    }
                },
            },
            {
                "event_name": "pull_request",
                "ref": "refs/pull/10/merge",
                "event": {
                    "workflow_run": {
                        "conclusion": "success",
                        "event": "pull_request",
                        "head_branch": "main",
                    }
                },
            },
        ]
        for ctx in blocked_contexts:
            with self.subTest(ctx=ctx):
                self.assertFalse(_eval_if_condition(preflight_if, ctx))

        chained_wf_run = {
            "event_name": "workflow_run",
            "ref": "refs/heads/main",
            "event": {
                "workflow_run": {
                    "conclusion": "success",
                    "event": "workflow_run",
                    "head_branch": "main",
                }
            },
        }
        self.assertTrue(_eval_if_condition(preflight_if, chained_wf_run))

        for job_name in ("build", "deploy"):
            job_if = str(self.workflow_data["jobs"][job_name]["if"])
            self.assertTrue(
                _eval_if_condition(
                    job_if,
                    valid_wf_run,
                    {"preflight": {"outputs": {"should_deploy": "true"}}},
                ),
                job_name,
            )
            self.assertFalse(
                _eval_if_condition(
                    job_if,
                    valid_wf_run,
                    {"preflight": {"outputs": {"should_deploy": "false"}}},
                ),
                job_name,
            )
            self.assertFalse(
                _eval_if_condition(
                    job_if,
                    valid_wf_run,
                    {"preflight": {"outputs": {"should_deploy": ""}}},
                ),
                job_name,
            )

    def test_no_inline_workflow_expressions_in_shell_steps(self) -> None:
        """Shell run blocks in pages.yml must consume inputs via env blocks rather than inline expressions."""
        for job_name, job in self.workflow_data["jobs"].items():
            for step in job.get("steps", []):
                if "run" in step:
                    self.assertNotIn(
                        "${{",
                        step["run"],
                        f"Inline expression found in {job_name} step {step.get('name')!r}",
                    )

    def test_pages_preflight_api_gate_and_build_verification_shell_steps(
        self,
    ) -> None:
        """Preflight enforces origin/main ancestry, workflow_dispatch validation, and descendant supersession."""
        if not shutil.which("sh"):
            self.skipTest("sh is not available on this host")

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            origin = base / "origin.git"
            origin.mkdir()
            _git(origin, "init", "--bare")

            pusher = base / "pusher"
            pusher.mkdir()
            _git(pusher, "init")
            _git(pusher, "remote", "add", "origin", origin.as_posix())
            _write_lf(pusher / "README.md", "# Commit 1 (valid)\n")
            _git(pusher, "add", "README.md")
            _git(pusher, "commit", "-m", "Commit 1: valid")
            c1_sha = _git(pusher, "rev-parse", "HEAD")
            _git(pusher, "push", "-u", "origin", "HEAD:refs/heads/main")

            repo = base / "repo"
            _git(base, "clone", "--depth=1", origin.as_uri(), repo.as_posix())

            gh_env, state_path = _install_fake_gh(
                base,
                {
                    "workflow_runs": [
                        {
                            "head_sha": c1_sha,
                            "status": "completed",
                            "conclusion": "success",
                            "event": "push",
                            "head_branch": "main",
                        }
                    ]
                },
            )
            common_env = {
                **gh_env,
                "GH_REPO": "fderuiter/fred-skills",
                "GH_TOKEN": "ghs_synthetic_token",
                "GITHUB_REF_VALUE": "refs/heads/main",
            }

            ok_tip_wf, out_tip_wf = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c1_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(ok_tip_wf.returncode, 0, msg=ok_tip_wf.stderr)
            self.assertEqual(
                out_tip_wf,
                {"should_deploy": "true", "target_sha": c1_sha},
            )
            recorded_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    "repos/fderuiter/fred-skills/actions/workflows/validate.yml/runs"
                    in call[1]
                    for call in recorded_state["calls"]
                )
            )

            # Push broken Commit 2 on origin/main: failed/cancelled/PR runs do NOT supersede Commit 1,
            # and workflow_dispatch on unvalidated Commit 2 exits 1.
            _write_lf(pusher / "README.md", "# Commit 2 (broken)\n")
            _git(pusher, "add", "README.md")
            _git(pusher, "commit", "-m", "Commit 2: broken")
            c2_broken_sha = _git(pusher, "rev-parse", "HEAD")
            _git(pusher, "push", "origin", "HEAD:refs/heads/main")

            _write_lf(
                state_path,
                json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "head_sha": c2_broken_sha,
                                "status": "completed",
                                "conclusion": "failure",
                                "event": "push",
                                "head_branch": "main",
                            },
                            {
                                "head_sha": c2_broken_sha,
                                "status": "completed",
                                "conclusion": "cancelled",
                                "event": "push",
                                "head_branch": "main",
                            },
                            {
                                "head_sha": c2_broken_sha,
                                "status": "completed",
                                "conclusion": "success",
                                "event": "pull_request",
                                "head_branch": "main",
                            },
                            {
                                "head_sha": c1_sha,
                                "status": "completed",
                                "conclusion": "success",
                                "event": "push",
                                "head_branch": "main",
                            },
                        ]
                    }
                ),
            )

            ok_after_fail, out_after_fail = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c1_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(ok_after_fail.returncode, 0, msg=ok_after_fail.stderr)
            self.assertEqual(
                out_after_fail,
                {"should_deploy": "true", "target_sha": c1_sha},
            )

            ok_dispatch_c1, out_dispatch_c1 = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": c1_sha,
                },
            )
            self.assertEqual(ok_dispatch_c1.returncode, 0, msg=ok_dispatch_c1.stderr)
            self.assertEqual(
                out_dispatch_c1,
                {"should_deploy": "true", "target_sha": c1_sha},
            )

            unvalidated_dispatch, _ = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": c2_broken_sha,
                },
            )
            self.assertEqual(unvalidated_dispatch.returncode, 1)
            self.assertIn("unvalidated commit", unvalidated_dispatch.stderr)

            ok_release_wf_c1, out_release_wf_c1 = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c1_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "workflow_run",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(
                ok_release_wf_c1.returncode, 0, msg=ok_release_wf_c1.stderr
            )
            self.assertEqual(
                out_release_wf_c1,
                {"should_deploy": "true", "target_sha": c1_sha},
            )

            unvalidated_release_wf_c2, _ = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c2_broken_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "workflow_run",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(unvalidated_release_wf_c2.returncode, 1)
            self.assertIn("unvalidated commit", unvalidated_release_wf_c2.stderr)

            # Push valid Commit 3 on origin/main: Commit 1 is now superseded (should_deploy=false).
            _write_lf(pusher / "README.md", "# Commit 3 (valid)\n")
            _git(pusher, "add", "README.md")
            _git(pusher, "commit", "-m", "Commit 3: valid")
            c3_valid_sha = _git(pusher, "rev-parse", "HEAD")
            _git(pusher, "push", "origin", "HEAD:refs/heads/main")

            _write_lf(
                state_path,
                json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "head_sha": c3_valid_sha,
                                "status": "completed",
                                "conclusion": "success",
                                "event": "push",
                                "head_branch": "main",
                            },
                            {
                                "head_sha": c2_broken_sha,
                                "status": "completed",
                                "conclusion": "failure",
                                "event": "push",
                                "head_branch": "main",
                            },
                            {
                                "head_sha": c1_sha,
                                "status": "completed",
                                "conclusion": "success",
                                "event": "push",
                                "head_branch": "main",
                            },
                        ]
                    }
                ),
            )

            stale_wf, out_stale_wf = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c1_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(stale_wf.returncode, 0, msg=stale_wf.stderr)
            self.assertEqual(
                out_stale_wf,
                {"should_deploy": "false", "target_sha": c1_sha},
            )

            stale_dispatch, out_stale_dispatch = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": c1_sha,
                },
            )
            self.assertEqual(stale_dispatch.returncode, 0, msg=stale_dispatch.stderr)
            self.assertEqual(
                out_stale_dispatch,
                {"should_deploy": "false", "target_sha": c1_sha},
            )

            stale_release_wf, out_stale_release_wf = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c1_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "workflow_run",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(
                stale_release_wf.returncode, 0, msg=stale_release_wf.stderr
            )
            self.assertEqual(
                out_stale_release_wf,
                {"should_deploy": "false", "target_sha": c1_sha},
            )

            # Create a commit outside origin/main (on an unmerged feature branch).
            _git(pusher, "checkout", "-b", "feature/off-main", c1_sha)
            _write_lf(pusher / "OFF_MAIN.md", "off main\n")
            _git(pusher, "add", "OFF_MAIN.md")
            _git(pusher, "commit", "-m", "Commit outside origin/main")
            off_main_sha = _git(pusher, "rev-parse", "HEAD")
            _git(pusher, "push", "origin", "HEAD:refs/heads/feature/off-main")
            _git(
                repo,
                "fetch",
                "origin",
                "+refs/heads/feature/off-main:refs/remotes/origin/feature/off-main",
            )

            rejected_envs = [
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": off_main_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": off_main_sha,
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": "0" * 40,
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": "not-a-sha",
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c3_valid_sha,
                    "WORKFLOW_CONCLUSION": "failure",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c3_valid_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "pull_request",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_run",
                    "TARGET_SHA": c3_valid_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "feature",
                },
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_VALUE": "refs/heads/feature",
                    "TARGET_SHA": c3_valid_sha,
                },
                {
                    **common_env,
                    "GH_TOKEN": "",
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": c3_valid_sha,
                },
            ]
            for bad_env in rejected_envs:
                with self.subTest(bad_env=bad_env):
                    bad_proc, _ = self._run_preflight_gate(repo, bad_env)
                    self.assertEqual(bad_proc.returncode, 1)

            # API failure must also fail preflight closed with exit code 1.
            _write_lf(
                state_path, json.dumps({"api_error": "HTTP 503 Service Unavailable"})
            )
            api_err_proc, _ = self._run_preflight_gate(
                repo,
                {
                    **common_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "TARGET_SHA": c3_valid_sha,
                },
            )
            self.assertEqual(api_err_proc.returncode, 1)

            # Verify jobs.build checked-out SHA step enforces exact HEAD == TARGET_SHA match.
            build_verify_step = next(
                s
                for s in self.workflow_data["jobs"]["build"]["steps"]
                if s.get("name") == "Verify checked-out target commit"
            )
            _git(repo, "checkout", "--detach", c1_sha)
            ok_build_verify = self._run_shell_script(
                str(build_verify_step["run"]),
                cwd=repo,
                env=_isolated_env(repo, {"TARGET_SHA": c1_sha}),
            )
            self.assertEqual(ok_build_verify.returncode, 0, msg=ok_build_verify.stderr)

            bad_build_verify = self._run_shell_script(
                str(build_verify_step["run"]),
                cwd=repo,
                env=_isolated_env(repo, {"TARGET_SHA": c3_valid_sha}),
            )
            self.assertEqual(bad_build_verify.returncode, 1)

    def test_pages_action_pins_pass_offline_and_mocked_online_verification(
        self,
    ) -> None:
        """pages.yml action SHAs must pass offline format checks and mocked upstream tag verification."""
        offline_errors = verify_workflow_actions.verify_workflows(
            root=ROOT,
            workflow_files=[PAGES_WORKFLOW],
            online=False,
        )
        self.assertEqual([], offline_errors)

        remote_fixtures = {
            "https://github.com/actions/checkout.git": (
                "d23441a48e516b6c34aea4fa41551a30e30af803\trefs/tags/v6.0.0\n"
            ),
            "https://github.com/actions/setup-python.git": (
                "ece7cb06caefa5fff74198d8649806c4678c61a1\trefs/tags/v6.0.0\n"
            ),
            "https://github.com/actions/configure-pages.git": (
                "45bfe0192ca1faeb007ade9deae92b16b8254a0d\trefs/tags/v6.0.0\n"
            ),
            "https://github.com/actions/upload-pages-artifact.git": (
                "fc324d3547104276b827a68afc52ff2a11cc49c9\trefs/tags/v5.0.0\n"
            ),
            "https://github.com/actions/deploy-pages.git": (
                "368f82528645a54fb793d4d04e342629a3f51346\trefs/tags/v5.0.1\n"
            ),
        }

        def fake_ls_remote(
            cmd: list[str], **_kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            url = cmd[-1]
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=remote_fixtures[url],
                stderr="",
            )

        with mock.patch(
            "verify_workflow_actions.subprocess.run",
            side_effect=fake_ls_remote,
        ):
            online_errors = verify_workflow_actions.verify_workflows(
                root=ROOT,
                workflow_files=[PAGES_WORKFLOW],
                online=True,
            )
        self.assertEqual([], online_errors)

    def test_stage_site_creates_clean_nojekyll_bundle_and_resolves_all_routes(
        self,
    ) -> None:
        """stage_site stages .nojekyll, index.html, and all documentation/catalog routes cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            site_dir = Path(tmp_dir) / "dist/site"
            with mock.patch(
                "build_site.build_site", wraps=build_site.build_site
            ) as spy_build_site:
                staged = build_experience.stage_site(site_dir, root=ROOT)
                spy_build_site.assert_called_once_with(site_dir, root=ROOT)
            self.assertEqual(staged, site_dir.resolve())
            self.assertTrue((site_dir / ".nojekyll").is_file())
            self.assertEqual((site_dir / ".nojekyll").read_bytes(), b"")
            self.assertTrue((site_dir / "index.html").is_file())
            self.assertTrue((site_dir / "README.html").is_file())
            self.assertTrue((site_dir / "docs/index.html").is_file())
            self.assertTrue((site_dir / "docs/explorer.html").is_file())
            self.assertTrue((site_dir / "skills/index.html").is_file())
            self.assertTrue((site_dir / "LICENSE").is_file())
            self.assertTrue((site_dir / "VERSION").is_file())
            self.assertTrue((site_dir / "gemini-extension.json").is_file())
            self.assertFalse((site_dir / ".cursor-plugin/plugin.json").exists())
            self.assertTrue((site_dir / ".cursor-plugin/marketplace.json").is_file())
            self.assertTrue((site_dir / ".claude-plugin/marketplace.json").is_file())

            # Clean artifact boundary: exclude repo internals, tests, scripts, and raw .md/.py/yaml
            for forbidden in (".git", ".github", "tests", "scripts"):
                self.assertFalse((site_dir / forbidden).exists(), forbidden)
            staged_files = [
                p.relative_to(site_dir).as_posix()
                for p in site_dir.rglob("*")
                if p.is_file()
            ]
            self.assertEqual([], [f for f in staged_files if f.endswith(".md")])
            self.assertEqual([], [f for f in staged_files if f.endswith(".py")])
            self.assertEqual([], [f for f in staged_files if f.endswith("openai.yaml")])
            self.assertEqual(
                [],
                [
                    f
                    for f in staged_files
                    if f.startswith("skills/")
                    and "/assets/" in f
                    and f.endswith(".html")
                ],
            )

            # All source docs compile to HTML guides without copying raw .md files
            source_docs = sorted(
                p.name for p in (ROOT / "docs").iterdir() if p.is_file()
            )
            self.assertGreaterEqual(len(source_docs), 1)
            for doc_name in source_docs:
                if doc_name.endswith(".md"):
                    self.assertFalse((site_dir / "docs" / doc_name).exists(), doc_name)
                    compiled_doc = site_dir / "docs" / f"{Path(doc_name).stem}.html"
                    self.assertTrue(compiled_doc.is_file(), compiled_doc)
                else:
                    self.assertTrue((site_dir / "docs" / doc_name).is_file(), doc_name)

            for skill_md in sorted((ROOT / "skills").glob("*/SKILL.md")):
                skill_name = skill_md.parent.name
                self.assertTrue(
                    (site_dir / "skills" / skill_name / "SKILL.html").is_file(),
                    skill_name,
                )
                self.assertTrue(
                    (site_dir / "skills" / skill_name / "index.html").is_file(),
                    skill_name,
                )

            staged_html_files = sorted(site_dir.rglob("*.html"))
            self.assertGreaterEqual(len(staged_html_files), 280)
            for html_file in staged_html_files:
                html_content = html_file.read_text(encoding="utf-8")
                for target in extract_relative_links(html_content):
                    clean_target = target.split("#")[0].split("?")[0]
                    self.assertFalse(
                        clean_target.lower().endswith(".md"),
                        f"{html_file}: unexpected raw .md link '{target}'",
                    )
                    resolved = resolve_relative_link_target(target, html_file.parent)
                    if resolved is None:
                        continue
                    self.assertTrue(
                        resolved.is_relative_to(site_dir.resolve()),
                        f"{html_file}: link '{target}' escaped dist/site -> {resolved}",
                    )
                    self.assertTrue(
                        resolved.exists(),
                        f"{html_file}: broken staged relative link '{target}' -> {resolved}",
                    )
                    if resolved.is_dir():
                        self.assertTrue(
                            (resolved / "index.html").is_file(),
                            f"{html_file}: directory target '{target}' missing index.html",
                        )

            with self.assertRaises(ValueError):
                build_experience.stage_site(ROOT, root=ROOT)

    def _assert_pages_docs_structure(
        self,
        readme_text: str,
        exp_text: str,
        impl_text: str,
        rel_text: str,
    ) -> None:
        readme_preamble = extract_markdown_preamble(readme_text)
        self.assertIn(HOSTED_URL, readme_preamble)
        self.assertIn("docs/explorer.html", readme_preamble)

        install_section = extract_markdown_section(readme_text, "Installation", level=2)
        self.assertIsNotNone(install_section)
        assert install_section is not None
        subheadings = extract_markdown_headings(install_section, level=3)
        self.assertGreaterEqual(len(subheadings), 1)
        self.assertEqual((3, "Claude Code"), subheadings[0])
        claude_section = extract_markdown_section(
            install_section, "Claude Code", level=3
        )
        self.assertIsNotNone(claude_section)
        assert claude_section is not None
        non_empty_claude_lines = [
            line.strip() for line in claude_section.splitlines() if line.strip()
        ]
        self.assertGreaterEqual(len(non_empty_claude_lines), 1)
        self.assertEqual("```sh", non_empty_claude_lines[0])

        exp_preamble = extract_markdown_preamble(exp_text)
        self.assertIn(HOSTED_EXPLORER_URL, exp_preamble)
        self.assertIn("](explorer.html)", exp_preamble)

        impl_tables = extract_markdown_tables(impl_text)
        self.assertGreaterEqual(len(impl_tables), 1)
        self.assertGreaterEqual(len(impl_tables[0]), 1)
        discovery_rows = [
            row
            for row in impl_tables[0]
            if row.get("Improvement") == "Task-oriented discovery"
        ]
        self.assertEqual(1, len(discovery_rows))
        self.assertIn(HOSTED_EXPLORER_URL, discovery_rows[0]["Delivered"])
        self.assertIn("](explorer.html)", discovery_rows[0]["Delivered"])

        rel_preamble = extract_markdown_preamble(rel_text)
        self.assertIn(HOSTED_URL, rel_preamble)
        self.assertIn("pages.yml", rel_preamble)
        self.assertIn("Validate skill packages", rel_preamble)
        self.assertIn("workflow_dispatch", rel_preamble)
        self.assertIn("superseded", rel_preamble)

    def test_documentation_links_hosted_pages_retains_offline_and_preserves_readme_offsets(
        self,
    ) -> None:
        """Docs must link to fderuiter.github.io/fred-skills, retain offline explorer, and tolerate intro prose edits."""
        self.assertEqual(build_experience.HOSTED_EXPLORER_URL, HOSTED_EXPLORER_URL)

        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        exp_text = (ROOT / "docs/experience.md").read_text(encoding="utf-8")
        impl_text = (ROOT / "docs/implementation-status.md").read_text(encoding="utf-8")
        rel_text = (ROOT / "docs/releasing.md").read_text(encoding="utf-8")

        self._assert_pages_docs_structure(readme_text, exp_text, impl_text, rel_text)

        prepended = "\n\nAdditional introductory prose paragraph above headings.\n\n"
        self._assert_pages_docs_structure(
            prepended + readme_text,
            prepended + exp_text,
            prepended + impl_text,
            prepended + rel_text,
        )

        banned_providers = re.compile(
            r"\b(netlify|vercel|cloudflare\s+pages|surge\.sh)\b",
            re.IGNORECASE,
        )
        for doc_rel in (
            "README.md",
            "docs/experience.md",
            "docs/implementation-status.md",
            "docs/releasing.md",
            ".github/workflows/pages.yml",
        ):
            text = (ROOT / doc_rel).read_text(encoding="utf-8")
            self.assertIsNone(banned_providers.search(text), doc_rel)


if __name__ == "__main__":
    unittest.main()

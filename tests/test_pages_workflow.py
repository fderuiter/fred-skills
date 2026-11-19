"""Tests for standalone GitHub Pages workflow triggers, permissions, environment rules, SHA pins, and site staging."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_experience
import verify_workflow_actions
from skill_tools import extract_relative_links, resolve_relative_link_target

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

_PASSTHROUGH_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
)


def _isolated_env(tmp: Path, extra_env: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV_KEYS if k in os.environ}
    home_dir = tmp / ".test-home"
    home_dir.mkdir(exist_ok=True)
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(extra_env)
    return env


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


def _eval_if_condition(condition: str, github_ctx: dict[str, Any]) -> bool:
    """Evaluate a GitHub Actions boolean `if` expression against a synthetic `github` context."""
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
        {"github": _to_namespace(github_ctx)},
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

    def test_workflow_yaml_syntax_triggers_permissions_and_concurrency(self) -> None:
        """pages.yml must configure workflow_run + workflow_dispatch, contents: read, and serialized concurrency."""
        self.assertEqual(self.workflow_data.get("permissions"), {"contents": "read"})
        self.assertEqual(
            self.workflow_data.get("concurrency"),
            {
                "group": "skills-${{ github.workflow }}-${{ github.ref }}",
                "cancel-in-progress": False,
            },
        )

        triggers = self._triggers()
        self.assertEqual(set(triggers.keys()), {"workflow_run", "workflow_dispatch"})
        wf_run = triggers["workflow_run"]
        self.assertEqual(wf_run.get("workflows"), ["Validate skill packages"])
        self.assertEqual(wf_run.get("types"), ["completed"])
        self.assertEqual(wf_run.get("branches"), ["main"])

        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        self.assertEqual(validate_data.get("permissions"), {"contents": "read"})
        for job in validate_data.get("jobs", {}).values():
            job_perms = job.get("permissions", {})
            self.assertNotEqual(job_perms.get("pages"), "write")
            for step in job.get("steps", []):
                self.assertNotIn("deploy-pages", str(step.get("uses", "")))

    def test_jobs_steps_environment_and_verified_action_shas(self) -> None:
        """pages.yml must stage dist/site/ in read-only build job and deploy via github-pages environment."""
        jobs = self.workflow_data["jobs"]
        self.assertIn("build", jobs)
        self.assertIn("deploy", jobs)

        build_job = jobs["build"]
        self.assertEqual(
            build_job.get("permissions"),
            {"contents": "read", "pages": "read"},
        )
        build_steps = build_job["steps"]

        checkout_step = build_steps[0]
        self.assertEqual(checkout_step.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            checkout_step.get("with", {}).get("ref"),
            "${{ github.event.workflow_run.head_sha || github.sha }}",
        )
        self.assertIs(checkout_step.get("with", {}).get("persist-credentials"), False)

        setup_py_step = next(
            s for s in build_steps if s.get("uses") == SETUP_PYTHON_SHA
        )
        self.assertEqual(setup_py_step.get("with", {}).get("python-version"), "3.12")

        stage_step = next(
            s for s in build_steps if "scripts/build_experience.py" in s.get("run", "")
        )
        self.assertIn("--check", stage_step["run"])
        self.assertIn("--stage-site dist/site", stage_step["run"])
        self.assertIn("dist/site/.nojekyll", stage_step["run"])

        configure_step = next(
            s for s in build_steps if s.get("uses") == CONFIGURE_PAGES_SHA
        )
        self.assertIsNotNone(configure_step)

        upload_step = next(s for s in build_steps if s.get("uses") == UPLOAD_PAGES_SHA)
        self.assertEqual(upload_step.get("with", {}).get("path"), "dist/site/")
        self.assertIs(upload_step.get("with", {}).get("include-hidden-files"), True)

        deploy_job = jobs["deploy"]
        self.assertEqual(deploy_job.get("needs"), "build")
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
        """Failed runs, pull_request runs, and non-main branches cannot run build/deploy or request pages: write."""
        for job_name in ("build", "deploy"):
            condition = str(self.workflow_data["jobs"][job_name]["if"])

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
            self.assertTrue(_eval_if_condition(condition, valid_wf_run), job_name)

            valid_dispatch = {
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/main",
                "event": {
                    "workflow_run": {"conclusion": "", "event": "", "head_branch": ""}
                },
            }
            self.assertTrue(_eval_if_condition(condition, valid_dispatch), job_name)

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
                with self.subTest(job=job_name, ctx=ctx):
                    self.assertFalse(_eval_if_condition(condition, ctx))

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

    def test_pages_verification_gate_shell_step_execution(self) -> None:
        """Defense-in-depth shell gate verifies event/branch/conclusion rules and exact checked-out SHA."""
        if not shutil.which("sh"):
            self.skipTest("sh is not available on this host")

        gate_step = next(
            s
            for s in self.workflow_data["jobs"]["build"]["steps"]
            if s.get("name") == "Verify Pages deployment gate and target commit"
        )
        script = str(gate_step["run"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "repo"
            repo.mkdir()
            _git(repo, "init")
            (repo / "README.md").write_text("# Synthetic\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "-m", "Initial commit")
            head_sha = _git(repo, "rev-parse", "HEAD")

            def run_gate(env_vars: dict[str, str]) -> subprocess.CompletedProcess[str]:
                with tempfile.TemporaryDirectory(
                    ignore_cleanup_errors=True
                ) as script_dir:
                    script_path = Path(script_dir) / "pages_gate.sh"
                    script_path.write_text(script, encoding="utf-8", newline="\n")
                    return subprocess.run(
                        ["sh", script_path.as_posix()],
                        cwd=str(repo),
                        env=_isolated_env(repo, env_vars),
                        capture_output=True,
                        text=True,
                        check=False,
                    )

            ok_wf = run_gate(
                {
                    "EVENT_NAME": "workflow_run",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                }
            )
            self.assertEqual(ok_wf.returncode, 0, msg=ok_wf.stderr)

            ok_dispatch = run_gate(
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "",
                    "WORKFLOW_EVENT": "",
                    "WORKFLOW_HEAD_BRANCH": "",
                }
            )
            self.assertEqual(ok_dispatch.returncode, 0, msg=ok_dispatch.stderr)

            rejected_envs = [
                {
                    "EVENT_NAME": "workflow_run",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "failure",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    "EVENT_NAME": "workflow_run",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "pull_request",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    "EVENT_NAME": "workflow_run",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "feature",
                },
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_VALUE": "refs/heads/feature",
                    "TARGET_SHA": head_sha,
                },
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": "0" * 40,
                },
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "TARGET_SHA": "not-a-sha",
                },
            ]
            for bad_env in rejected_envs:
                with self.subTest(bad_env=bad_env):
                    res = run_gate(bad_env)
                    self.assertNotEqual(res.returncode, 0)

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
            cmd: list[str], **kwargs: Any
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
            staged = build_experience.stage_site(site_dir, root=ROOT)
            self.assertEqual(staged, site_dir.resolve())
            self.assertTrue((site_dir / ".nojekyll").is_file())
            self.assertEqual((site_dir / ".nojekyll").read_bytes(), b"")
            self.assertTrue((site_dir / "index.html").is_file())
            self.assertTrue((site_dir / "docs/explorer.html").is_file())

            # Clean artifact boundary: exclude repo internals, tests, and scripts
            for forbidden in (".git", ".github", "tests", "scripts"):
                self.assertFalse((site_dir / forbidden).exists(), forbidden)

            # All 15 docs routes + root index route = 16 routes present
            source_docs = sorted(
                p.name for p in (ROOT / "docs").iterdir() if p.is_file()
            )
            self.assertGreaterEqual(len(source_docs), 15)
            for doc_name in source_docs:
                staged_doc = site_dir / "docs" / doc_name
                self.assertTrue(staged_doc.is_file(), doc_name)
                if doc_name.endswith(".md"):
                    self.assertEqual(
                        staged_doc.read_text(encoding="utf-8"),
                        (ROOT / "docs" / doc_name).read_text(encoding="utf-8"),
                        f"Markdown content was unexpectedly rewritten for {doc_name}",
                    )

            # Verify relative links resolve inside staged site for root index, offline explorer, README, and docs
            check_files = [
                site_dir / "index.html",
                site_dir / "docs/explorer.html",
                site_dir / "README.md",
                *sorted((site_dir / "docs").glob("*.md")),
            ]
            for file_path in check_files:
                content = file_path.read_text(encoding="utf-8")
                for target in extract_relative_links(content):
                    resolved = resolve_relative_link_target(target, file_path.parent)
                    if resolved is None:
                        continue
                    self.assertTrue(
                        resolved.is_relative_to(site_dir.resolve()),
                        f"{file_path}: link '{target}' escaped dist/site -> {resolved}",
                    )
                    self.assertTrue(
                        resolved.exists(),
                        f"{file_path}: broken staged relative link '{target}' -> {resolved}",
                    )

            with self.assertRaises(ValueError):
                build_experience.stage_site(ROOT, root=ROOT)

    def test_documentation_links_hosted_pages_retains_offline_and_preserves_readme_offsets(
        self,
    ) -> None:
        """Docs must link to fderuiter.github.io/fred-skills, retain offline explorer, and preserve README offsets."""
        readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        intro_window = "\n".join(readme_lines[8:11])
        self.assertIn(HOSTED_URL, intro_window)
        self.assertIn("docs/explorer.html", intro_window)
        self.assertEqual(readme_lines[28], "### Claude Code")
        self.assertEqual(readme_lines[30], "```sh")

        exp_lines = (
            (ROOT / "docs/experience.md").read_text(encoding="utf-8").splitlines()
        )
        self.assertIn(HOSTED_URL, exp_lines[2])
        self.assertIn("explorer.html", exp_lines[2])

        impl_lines = (
            (ROOT / "docs/implementation-status.md")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertIn(HOSTED_URL, impl_lines[8])
        self.assertIn("explorer.html", impl_lines[8])

        rel_lines = (
            (ROOT / "docs/releasing.md").read_text(encoding="utf-8").splitlines()
        )
        rel_window = "\n".join(rel_lines[7:16])
        self.assertIn(HOSTED_URL, rel_window)
        self.assertIn("pages.yml", rel_window)

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

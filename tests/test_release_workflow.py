"""Tests for release workflow preflight gating, automated main publishing, action SHA pins, and idempotency."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import traceback
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

if os.environ.get("GITHUB_ACTIONS") == "true":
    _orig_add_failure = unittest.TextTestResult.addFailure
    _orig_add_error = unittest.TextTestResult.addError
    _orig_add_subtest = unittest.TextTestResult.addSubTest

    def _emit_gha_err(label: str, err: Any) -> None:
        formatted = "".join(traceback.format_exception(*err))
        escaped = (
            formatted.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        )
        print(f"\n::error title={label}::{escaped}", flush=True)

    def _gha_add_failure(self: Any, test: Any, err: Any) -> None:
        _emit_gha_err(test.id(), err)
        _orig_add_failure(self, test, err)

    def _gha_add_error(self: Any, test: Any, err: Any) -> None:
        _emit_gha_err(test.id(), err)
        _orig_add_error(self, test, err)

    def _gha_add_subtest(self: Any, test: Any, subtest: Any, err: Any) -> None:
        if err is not None:
            _emit_gha_err(subtest.id(), err)
        _orig_add_subtest(self, test, subtest, err)

    unittest.TextTestResult.addFailure = _gha_add_failure  # type: ignore[method-assign]
    unittest.TextTestResult.addError = _gha_add_error  # type: ignore[method-assign]
    unittest.TextTestResult.addSubTest = _gha_add_subtest  # type: ignore[method-assign]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_pins
import verify_workflow_actions
from fredlib import releases

RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"
ACTION_PINS_LOCKFILE = ROOT / ".github/action-pins.json"
RELEASING_DOC = ROOT / "docs/releasing.md"
CHECKOUT_SHA = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
ATTEST_SHA = "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"

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

    if len(args) < 2 or args[0] != "release":
        sys.stderr.write(f"unexpected gh invocation: {args!r}\\n")
        sys.exit(2)

    subcmd = args[1]
    state.setdefault("calls", []).append(args)

    if subcmd == "view":
        if state.get("view_error"):
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(state["view_error"] + "\\n")
            sys.exit(1)
        tag = args[2]
        matches = [r for r in state.get("releases", []) if r["tag"] == tag]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        if not matches:
            sys.stderr.write("release not found\\n")
            sys.exit(1)
        latest = matches[-1]
        if "--json" in args and "isDraft" in args:
            if "--jq" in args or "-q" in args:
                sys.stdout.write("true\\n" if latest["draft"] else "false\\n")
            else:
                sys.stdout.write(json.dumps({"isDraft": latest["draft"]}) + "\\n")
        sys.exit(0)

    if subcmd == "create":
        tag = args[2]
        draft = "--draft" in args or "-d" in args
        prerelease = "--prerelease" in args or "-p" in args
        verify_tag = "--verify-tag" in args
        generate_notes = "--generate-notes" in args
        notes_file = None
        notes_body = None
        target = None
        title = None
        assets = []
        i = 3
        while i < len(args):
            tok = args[i]
            if tok in (
                "--draft",
                "-d",
                "--prerelease",
                "-p",
                "--verify-tag",
                "--generate-notes",
            ):
                i += 1
            elif tok in ("--target", "--title", "-t", "--notes-file", "-F"):
                val = args[i + 1]
                if tok == "--target":
                    target = val
                elif tok in ("--title", "-t"):
                    title = val
                else:
                    notes_file = val
                    notes_body = Path(val).read_text(encoding="utf-8")
                i += 2
            elif tok.startswith("-"):
                sys.stderr.write(f"unsupported flag: {tok}\\n")
                sys.exit(2)
            else:
                p = Path(tok)
                assets.append({"name": p.name, "content": p.read_text(encoding="utf-8")})
                i += 1
        state.setdefault("releases", []).append(
            {
                "tag": tag,
                "draft": draft,
                "prerelease": prerelease,
                "verify_tag": verify_tag,
                "generate_notes": generate_notes,
                "notes_file": notes_file,
                "notes_body": notes_body,
                "target": target,
                "title": title,
                "assets": {a["name"]: a["content"] for a in assets},
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(0)

    if subcmd == "upload":
        tag = args[2]
        clobber = "--clobber" in args
        matches = [r for r in state.get("releases", []) if r["tag"] == tag]
        if not matches:
            sys.stderr.write("release not found\\n")
            sys.exit(1)
        rel = matches[-1]
        for tok in args[3:]:
            if tok == "--clobber":
                continue
            p = Path(tok)
            if p.name in rel["assets"] and not clobber:
                sys.stderr.write(f"asset already exists: {p.name}\\n")
                sys.exit(1)
            rel["assets"][p.name] = p.read_text(encoding="utf-8")
        rel["last_upload_clobber"] = clobber
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(0)

    if subcmd == "edit":
        tag = args[2]
        matches = [r for r in state.get("releases", []) if r["tag"] == tag]
        if not matches:
            sys.stderr.write("release not found\\n")
            sys.exit(1)
        rel = matches[-1]
        i = 3
        while i < len(args):
            tok = args[i]
            if tok == "--draft=false":
                rel["draft"] = False
                i += 1
            elif tok == "--draft=true" or tok == "--draft":
                rel["draft"] = True
                i += 1
            elif tok == "--verify-tag":
                rel["verify_tag"] = True
                i += 1
            elif tok in ("--prerelease", "-p", "--prerelease=true"):
                rel["prerelease"] = True
                i += 1
            elif tok in ("--target", "--title", "-t", "--notes-file", "-F"):
                val = args[i + 1]
                if tok == "--target":
                    rel["target"] = val
                elif tok in ("--title", "-t"):
                    rel["title"] = val
                else:
                    rel["notes_file"] = val
                    rel["notes_body"] = Path(val).read_text(encoding="utf-8")
                i += 2
            else:
                sys.stderr.write(f"unsupported edit flag: {tok}\\n")
                sys.exit(2)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(0)

    sys.stderr.write(f"unsupported release subcommand: {subcmd}\\n")
    sys.exit(2)
    """
)


def _write_lf(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


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
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "protocol.file.allow=always",
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


def _configure_local_test_repo(repo: Path) -> None:
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "maintenance.auto", "false")
    _git(repo, "config", "gc.auto", "0")
    _git(repo, "config", "protocol.file.allow", "always")


def _parse_github_output(output_file: Path) -> dict[str, str]:
    if not output_file.is_file():
        return {}
    parsed: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            parsed[key] = val
    return parsed


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
    _write_lf(state_path, json.dumps(initial_state or {"releases": []}))
    gh_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_GH_PYTHON": sys.executable,
        "FAKE_GH_SCRIPT": str(fake_gh_py),
        "FAKE_GH_STATE": str(state_path),
    }
    return gh_env, state_path


class ReleaseWorkflowTests(unittest.TestCase):
    """Verify release workflow triggers, preflight gate, direct main release, and idempotency."""

    workflow_text: str
    workflow_data: dict[Any, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        loaded = yaml.safe_load(cls.workflow_text)
        assert isinstance(loaded, dict)
        cls.workflow_data = loaded

    def _triggers(self) -> dict[str, Any]:
        raw: Any = self.workflow_data.get("on") or self.workflow_data.get(True) or {}
        assert isinstance(raw, dict)
        return raw

    def test_workflow_yaml_syntax_triggers_and_preflight_graph(self) -> None:
        """release.yml must configure main/tag/dispatch triggers and preflight -> build -> release gates."""
        self.assertEqual(self.workflow_data.get("permissions"), {"contents": "read"})
        self.assertEqual(
            self.workflow_data.get("concurrency"),
            {
                "group": "skills-${{ github.workflow }}-${{ github.sha }}-${{ github.run_id }}",
                "cancel-in-progress": False,
            },
        )

        triggers = self._triggers()
        self.assertIn("push", triggers)
        self.assertIn("main", triggers["push"].get("branches", []))
        self.assertIn("v*", triggers["push"].get("tags", []))
        self.assertIn("workflow_dispatch", triggers)

        dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertIn("tag", dispatch_inputs)
        self.assertIn("publish", dispatch_inputs)
        self.assertEqual(dispatch_inputs["publish"]["type"], "boolean")
        self.assertIs(dispatch_inputs["publish"]["default"], False)

        jobs = self.workflow_data["jobs"]
        self.assertIn("preflight", jobs)
        self.assertIn("validate", jobs)
        self.assertIn("build", jobs)
        self.assertIn("release", jobs)

        preflight = jobs["preflight"]
        self.assertNotIn("concurrency", preflight)
        self.assertEqual(preflight.get("permissions"), {"contents": "read"})
        self.assertEqual(
            set(preflight.get("outputs", {}).keys()),
            {"should_build", "should_publish", "release_tag", "target_sha"},
        )
        preflight_checkout = preflight["steps"][0]
        self.assertEqual(preflight_checkout.get("uses"), CHECKOUT_SHA)
        self.assertIs(
            preflight_checkout.get("with", {}).get("persist-credentials"), False
        )

        validate_job = jobs["validate"]
        self.assertNotIn("concurrency", validate_job)
        self.assertEqual(validate_job.get("needs"), "preflight")
        self.assertEqual(
            validate_job.get("if"), "needs.preflight.outputs.should_build == 'true'"
        )
        self.assertEqual(validate_job.get("permissions"), {"contents": "read"})
        self.assertEqual(validate_job.get("uses"), "./.github/workflows/validate.yml")
        self.assertEqual(
            validate_job.get("with"),
            {"ref": "${{ needs.preflight.outputs.target_sha }}"},
        )

        build = jobs["build"]
        self.assertNotIn("concurrency", build)
        self.assertEqual(build.get("needs"), ["preflight", "validate"])
        self.assertEqual(
            build.get("if"), "needs.preflight.outputs.should_build == 'true'"
        )
        self.assertEqual(build.get("permissions"), {"contents": "read"})
        build_checkout = build["steps"][0]
        self.assertEqual(build_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            build_checkout["with"]["ref"], "${{ needs.preflight.outputs.target_sha }}"
        )
        self.assertIs(build_checkout["with"]["persist-credentials"], False)

        release = jobs["release"]
        self.assertEqual(release.get("needs"), ["preflight", "validate", "build"])
        self.assertEqual(
            release.get("if"), "needs.preflight.outputs.should_publish == 'true'"
        )
        self.assertEqual(
            release.get("concurrency"),
            {
                "group": "skills-release-publish-${{ needs.preflight.outputs.release_tag || github.ref }}",
                "cancel-in-progress": False,
            },
        )
        self.assertEqual(
            release.get("permissions"),
            {"contents": "write", "id-token": "write", "attestations": "write"},
        )
        release_checkout = release["steps"][0]
        self.assertEqual(release_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            release_checkout["with"]["ref"], "${{ needs.preflight.outputs.target_sha }}"
        )
        self.assertEqual(release_checkout["with"].get("fetch-depth"), 0)
        self.assertIs(release_checkout["with"]["persist-credentials"], False)

    def test_release_concurrency_isolates_builds_and_locks_release_per_tag(
        self,
    ) -> None:
        """release.yml must key workflow runs by SHA and run_id while locking jobs.release per tag."""
        import re
        from types import SimpleNamespace

        expr_pattern = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")

        def to_ns(val: Any) -> Any:
            if isinstance(val, dict):
                return SimpleNamespace(**{k: to_ns(v) for k, v in val.items()})
            return val

        def eval_gha_template(
            template: str,
            github_ctx: dict[str, Any],
            needs_ctx: dict[str, Any] | None = None,
        ) -> Any:
            def eval_expr(expr: str) -> Any:
                py_expr = expr.replace("&&", " and ").replace("||", " or ")
                return eval(
                    py_expr,
                    {"__builtins__": {}},
                    {
                        "github": to_ns(github_ctx),
                        "needs": to_ns(needs_ctx or {}),
                    },
                )

            full_match = expr_pattern.fullmatch(template.strip())
            if full_match:
                return eval_expr(full_match.group(1))
            return expr_pattern.sub(lambda m: str(eval_expr(m.group(1))), template)

        wf_concurrency = self.workflow_data["concurrency"]
        wf_group_tpl = wf_concurrency["group"]
        self.assertIs(wf_concurrency["cancel-in-progress"], False)

        # Rapid main push burst: every commit run resolves a distinct SHA + run_id group.
        main_run_1 = {
            "workflow": "Release",
            "ref": "refs/heads/main",
            "sha": "1" * 40,
            "run_id": "9001",
        }
        main_run_2 = {
            "workflow": "Release",
            "ref": "refs/heads/main",
            "sha": "2" * 40,
            "run_id": "9002",
        }
        main_run_3 = {
            "workflow": "Release",
            "ref": "refs/heads/main",
            "sha": "3" * 40,
            "run_id": "9003",
        }
        wf_groups_burst = {
            eval_gha_template(wf_group_tpl, main_run_1),
            eval_gha_template(wf_group_tpl, main_run_2),
            eval_gha_template(wf_group_tpl, main_run_3),
        }
        self.assertEqual(3, len(wf_groups_burst))

        # Concurrent workflow_dispatch dry-run builds or tag push sharing the same commit SHA
        # still resolve distinct groups via run_id so jobs.build never suffers queue eviction.
        dispatch_run_a = {
            "workflow": "Release",
            "ref": "refs/heads/main",
            "sha": "1" * 40,
            "run_id": "9004",
        }
        tag_push_same_sha = {
            "workflow": "Release",
            "ref": "refs/tags/v0.2.0",
            "sha": "1" * 40,
            "run_id": "9005",
        }
        self.assertEqual(
            3,
            len(
                {
                    eval_gha_template(wf_group_tpl, main_run_1),
                    eval_gha_template(wf_group_tpl, dispatch_run_a),
                    eval_gha_template(wf_group_tpl, tag_push_same_sha),
                }
            ),
        )

        # jobs.release serializes publication per resolved release_tag across triggers.
        rel_concurrency = self.workflow_data["jobs"]["release"]["concurrency"]
        rel_group_tpl = rel_concurrency["group"]
        self.assertIs(rel_concurrency["cancel-in-progress"], False)

        needs_v020 = {"preflight": {"outputs": {"release_tag": "v0.2.0"}}}
        needs_v021 = {"preflight": {"outputs": {"release_tag": "v0.2.1"}}}
        needs_empty = {"preflight": {"outputs": {"release_tag": ""}}}

        lock_main_v020 = eval_gha_template(rel_group_tpl, main_run_1, needs_v020)
        lock_tag_v020 = eval_gha_template(rel_group_tpl, tag_push_same_sha, needs_v020)
        lock_dispatch_v020 = eval_gha_template(
            rel_group_tpl, dispatch_run_a, needs_v020
        )
        lock_main_v021 = eval_gha_template(rel_group_tpl, main_run_2, needs_v021)
        lock_fallback = eval_gha_template(rel_group_tpl, tag_push_same_sha, needs_empty)

        self.assertEqual("skills-release-publish-v0.2.0", lock_main_v020)
        self.assertEqual(lock_main_v020, lock_tag_v020)
        self.assertEqual(lock_main_v020, lock_dispatch_v020)
        self.assertEqual("skills-release-publish-v0.2.1", lock_main_v021)
        self.assertNotEqual(lock_main_v020, lock_main_v021)
        self.assertEqual("skills-release-publish-refs/tags/v0.2.0", lock_fallback)

    def test_no_inline_workflow_expressions_in_shell_steps(self) -> None:
        """Shell run blocks must consume inputs via env blocks rather than inline expressions."""
        for job_name, job in self.workflow_data["jobs"].items():
            for step in job.get("steps", []):
                if "run" in step:
                    self.assertNotIn(
                        "${{",
                        step["run"],
                        f"Inline expression found in {job_name} step {step.get('name')!r}",
                    )

    def test_build_job_preserves_existing_steps(self) -> None:
        """The build job must retain its validation, packaging, verification, and upload steps."""
        build_job = self.workflow_data["jobs"]["build"]
        step_names = [s.get("name") for s in build_job["steps"] if "name" in s]
        self.assertIn("Install validation dependencies", step_names)
        self.assertIn("Validate source and fixtures", step_names)
        self.assertIn("Package release assets", step_names)
        self.assertIn("Verify release manifest and checksums", step_names)

    def test_release_job_verifies_prerequisites_and_checksums_before_attestation(
        self,
    ) -> None:
        """The release job must verify prerequisites and downloaded checksums before SLSA attestation."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        step_names = [s.get("name", "") for s in release_steps]
        prereq_idx = next(
            i
            for i, name in enumerate(step_names)
            if "verify" in name.lower() and "prerequisite" in name.lower()
        )
        checksum_idx = step_names.index("Verify downloaded artifact checksums")
        attest_idx = step_names.index("Attest build provenance")
        publish_idx = step_names.index("Publish GitHub Release")
        self.assertLess(prereq_idx, checksum_idx)
        self.assertLess(checksum_idx, attest_idx)
        self.assertLess(attest_idx, publish_idx)

    def test_attest_provenance_step_pins_valid_v4_2_2_sha_and_permissions(self) -> None:
        """The release job must pin actions/attest-build-provenance to v4.2.2 SHA and retain SLSA permissions."""
        release_job = self.workflow_data["jobs"]["release"]
        self.assertEqual(
            release_job.get("permissions"),
            {
                "contents": "write",
                "id-token": "write",
                "attestations": "write",
            },
        )
        attest_step = next(
            s
            for s in release_job["steps"]
            if s.get("name") == "Attest build provenance"
        )
        self.assertEqual(attest_step.get("uses"), ATTEST_SHA)
        self.assertIn(
            f"{ATTEST_SHA} # v4.2.2",
            self.workflow_text,
        )
        self.assertEqual(
            attest_step.get("with", {}).get("subject-path"),
            "dist/release/*",
        )

    def test_root_license_exists_and_manifest_reports_repository_license(self) -> None:
        """Repository root must commit a non-empty MIT LICENSE reflected in release manifests."""
        license_path = ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "Expected root LICENSE file")
        license_text = license_path.read_text(encoding="utf-8")
        self.assertTrue(license_text.strip())
        self.assertIn("MIT License", license_text)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = releases.build(Path(tmp_dir) / "pack", pack="starter", root=ROOT)
            self.assertEqual("See repository LICENSE", manifest["licensing"])

    def test_publish_step_publishes_non_draft_and_declares_notes_and_idempotency(
        self,
    ) -> None:
        """Publish GitHub Release must publish directly (no --draft create), verify tags, and promote drafts."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        script = publish_step["run"]
        self.assertIn("git ls-remote --exit-code --refs origin", script)
        self.assertIn("gh release view", script)
        self.assertIn("gh release create", script)
        self.assertIn("gh release upload", script)
        self.assertIn("gh release edit", script)
        self.assertIn("--draft=false", script)
        self.assertIn("--clobber", script)
        self.assertIn("--verify-tag", script)
        self.assertIn('--target "$TARGET_SHA"', script)
        self.assertIn('--notes-file "$NOTES_FILE"', script)
        self.assertIn("--generate-notes", script)
        self.assertNotIn("--draft ", script)
        self.assertNotIn("--draft\n", script)

    def _run_preflight_script(
        self,
        cwd: Path,
        env_vars: dict[str, str],
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        preflight_steps = self.workflow_data["jobs"]["preflight"]["steps"]
        gate_step = next(s for s in preflight_steps if s.get("id") == "gate")
        output_file = cwd / ".gh-output"
        if output_file.exists():
            output_file.unlink()
        merged_env = _isolated_env(cwd, {"GITHUB_OUTPUT": str(output_file), **env_vars})
        proc = subprocess.run(
            ["sh", "-c", gate_step["run"]],
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc, _parse_github_output(output_file)

    def _run_verification_script(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        verify_step = next(
            s for s in release_steps if s.get("name") == "Verify release prerequisites"
        )
        return subprocess.run(
            ["sh", "-c", verify_step["run"]],
            cwd=str(cwd),
            env=_isolated_env(cwd, env_vars),
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_checksum_step(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        checksum_step = next(
            s
            for s in release_steps
            if s.get("name") == "Verify downloaded artifact checksums"
        )
        return subprocess.run(
            ["sh", "-c", checksum_step["run"]],
            cwd=str(cwd),
            env=_isolated_env(cwd, {"PYTHON_BIN": sys.executable}),
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_publish_script(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        return subprocess.run(
            ["sh", "-c", publish_step["run"]],
            cwd=str(cwd),
            env=_isolated_env(cwd, env_vars),
            capture_output=True,
            text=True,
            check=False,
        )

    def _setup_origin_and_clone(
        self,
        base_dir: Path,
        *,
        version_text: str = "0.2.0\n",
        license_text: str | None = "MIT License\n",
        remote_tag: str | None = "v0.2.0",
        annotated: bool = True,
    ) -> tuple[Path, Path, str]:
        origin = base_dir / "origin.git"
        origin.mkdir(parents=True, exist_ok=True)
        _git(origin, "init", "--bare")

        seed = base_dir / "seed"
        seed.mkdir(parents=True, exist_ok=True)
        _git(seed, "init")
        _write_lf(seed / "VERSION", version_text)
        if license_text is not None:
            _write_lf(seed / "LICENSE", license_text)
        _git(seed, "add", ".")
        _git(seed, "commit", "-m", "Initial release commit")
        tag_sha = _git(seed, "rev-parse", "HEAD")
        _git(seed, "remote", "add", "origin", origin.as_uri())
        _git(seed, "push", "origin", "HEAD:refs/heads/main")

        if remote_tag is not None:
            if annotated:
                _git(seed, "tag", "-a", remote_tag, "-m", f"Release {remote_tag}")
            else:
                _git(seed, "tag", remote_tag)
            _git(seed, "push", "origin", f"refs/tags/{remote_tag}")

        workspace = base_dir / "workspace"
        _git(
            base_dir,
            "clone",
            "--depth=1",
            "--no-tags",
            origin.as_uri(),
            str(workspace),
        )
        _configure_local_test_repo(workspace)
        return origin, workspace, tag_sha

    def test_verification_fails_when_license_missing_or_empty(self) -> None:
        """Verify release prerequisites aborts non-zero when LICENSE is absent or whitespace."""
        with _temp_dir() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            missing = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.2.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("LICENSE", missing.stderr)

            _write_lf(tmp / "LICENSE", "   \n\n  ")
            empty = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.2.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("LICENSE", empty.stderr)

    def test_verification_fails_when_tag_mismatches_version(self) -> None:
        """Verify release prerequisites aborts when git tag or input tag mismatches VERSION."""
        with _temp_dir() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            _write_lf(tmp / "LICENSE", "MIT License\n")

            bad_ref = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.1.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(bad_ref.returncode, 0)
            self.assertIn("does not match", bad_ref.stderr)

            bad_input = self._run_verification_script(
                tmp,
                {"REF_TYPE": "branch", "REF_NAME": "main", "INPUT_TAG": "v0.1.0"},
            )
            self.assertNotEqual(bad_input.returncode, 0)
            self.assertIn("does not match", bad_input.stderr)

    def test_verification_enforces_head_matches_target_sha(self) -> None:
        """Defense-in-depth verification succeeds when HEAD == TARGET_SHA and fails on divergence."""
        with _temp_dir() as tmp_dir:
            _, workspace, tag_sha = self._setup_origin_and_clone(Path(tmp_dir))
            ok = self._run_verification_script(
                workspace,
                {
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": tag_sha,
                },
            )
            self.assertEqual(ok.returncode, 0, msg=ok.stderr)

            missing_sha = self._run_verification_script(
                workspace,
                {
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": "",
                },
            )
            self.assertNotEqual(missing_sha.returncode, 0)
            self.assertIn("TARGET_SHA", missing_sha.stderr)

            diverged = self._run_verification_script(
                workspace,
                {
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "v0.2.0",
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": "0" * 40,
                },
            )
            self.assertNotEqual(diverged.returncode, 0)
            self.assertIn("target_sha", diverged.stderr)

    def test_downloaded_artifact_checksum_verification_step(self) -> None:
        """Downloaded artifact checksum step enforces matching manifests, rejects unlisted files, and catches tampering."""
        with _temp_dir() as tmp_dir:
            tmp = Path(tmp_dir)
            dist = tmp / "dist" / "release"
            dist.mkdir(parents=True)
            payload = b"deterministic-pack-archive"
            (dist / "pack.tar").write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            _write_lf(dist / "SHA256SUMS", f"{digest}  pack.tar\n")
            _write_lf(dist / "checksums.sha256", f"{digest}  pack.tar\n")

            ok = self._run_checksum_step(tmp)
            self.assertEqual(ok.returncode, 0, msg=ok.stderr)

            # Unlisted file injected into dist/release must fail before attestation.
            unlisted = dist / "unlisted.tar.gz"
            unlisted.write_bytes(b"injected")
            extra_res = self._run_checksum_step(tmp)
            self.assertNotEqual(extra_res.returncode, 0)
            self.assertIn("do not match SHA256SUMS", extra_res.stderr)
            unlisted.unlink()

            # Divergent SHA256SUMS vs checksums.sha256 must fail.
            _write_lf(dist / "checksums.sha256", f"{'0' * 64}  pack.tar\n")
            mismatch_res = self._run_checksum_step(tmp)
            self.assertNotEqual(mismatch_res.returncode, 0)
            _write_lf(dist / "checksums.sha256", f"{digest}  pack.tar\n")

            # Tampered archive payload must fail sha256sum -c.
            (dist / "pack.tar").write_bytes(b"tampered")
            bad = self._run_checksum_step(tmp)
            self.assertNotEqual(bad.returncode, 0)

            # Restore valid payload and verify full VERSION + manifest.json check under stdlib-only Python (-S).
            (dist / "pack.tar").write_bytes(payload)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            missing_manifest = self._run_checksum_step(tmp)
            self.assertNotEqual(missing_manifest.returncode, 0)
            self.assertIn("manifest.json is missing", missing_manifest.stderr)

            valid_manifest = {
                "schema_version": "1.0",
                "version": "0.2.0",
                "pack": "starter",
                "licensing": "See repository LICENSE",
                "archives": [
                    {
                        "file": "pack.tar",
                        "sha256": digest,
                        "bytes": len(payload),
                    }
                ],
            }
            _write_lf(dist / "manifest.json", json.dumps(valid_manifest) + "\n")
            ok_manifest = self._run_checksum_step(tmp)
            self.assertEqual(ok_manifest.returncode, 0, msg=ok_manifest.stderr)

            bad_version_manifest = {**valid_manifest, "version": "0.9.9"}
            _write_lf(dist / "manifest.json", json.dumps(bad_version_manifest) + "\n")
            bad_ver = self._run_checksum_step(tmp)
            self.assertNotEqual(bad_ver.returncode, 0)
            self.assertIn("does not match VERSION", bad_ver.stderr)

            bad_license_manifest = {**valid_manifest, "licensing": "Unknown"}
            _write_lf(dist / "manifest.json", json.dumps(bad_license_manifest) + "\n")
            bad_lic = self._run_checksum_step(tmp)
            self.assertNotEqual(bad_lic.returncode, 0)
            self.assertIn("licensing", bad_lic.stderr)

    def test_preflight_manual_dispatch_publish_false_builds_artifacts_only(
        self,
    ) -> None:
        """Manual workflow_dispatch with publish=false sets should_build=true and should_publish=false without LICENSE or gh."""
        with _temp_dir() as tmp_dir:
            _, workspace, head_sha = self._setup_origin_and_clone(
                Path(tmp_dir),
                license_text=None,
                remote_tag=None,
            )
            proc, outputs = self._run_preflight_script(
                workspace,
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "false",
                    "INPUT_TAG": "",
                    "REF_NAME": "feature/artifact-preview",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(
                outputs,
                {
                    "should_build": "true",
                    "should_publish": "false",
                    "release_tag": "",
                    "target_sha": head_sha,
                },
            )

            # Manual dispatch with publish=false and explicit tag resolves tag commit SHA read-only.
            _, tagged_ws, tagged_sha = self._setup_origin_and_clone(
                Path(tmp_dir) / "tagged-preview",
                license_text=None,
                remote_tag="v0.2.0",
                annotated=True,
            )
            proc_tagged, out_tagged = self._run_preflight_script(
                tagged_ws,
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": "f" * 40,
                    "INPUT_PUBLISH": "false",
                    "INPUT_TAG": "v0.2.0",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc_tagged.returncode, 0, msg=proc_tagged.stderr)
            self.assertEqual(
                out_tagged,
                {
                    "should_build": "true",
                    "should_publish": "false",
                    "release_tag": "v0.2.0",
                    "target_sha": tagged_sha,
                },
            )

    def test_preflight_main_push_across_missing_draft_published_and_api_error(
        self,
    ) -> None:
        """On main pushes, preflight schedules build/publish when missing or draft, skips when published, and fails on API error."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace, head_sha = self._setup_origin_and_clone(
                base,
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag=None,
            )

            # 1. Missing release on untagged main push -> schedules build and publish at main HEAD.
            gh_env, state_path = _install_fake_gh(base, {"releases": []})
            proc_missing, out_missing = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc_missing.returncode, 0, msg=proc_missing.stderr)
            self.assertEqual(
                out_missing,
                {
                    "should_build": "true",
                    "should_publish": "true",
                    "release_tag": "v0.2.0",
                    "target_sha": head_sha,
                },
            )

            # 2. Existing draft release -> schedules build and publish to promote draft.
            _write_lf(
                state_path,
                json.dumps({"releases": [{"tag": "v0.2.0", "draft": True}]}),
            )
            proc_draft, out_draft = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc_draft.returncode, 0, msg=proc_draft.stderr)
            self.assertEqual(out_draft["should_build"], "true")
            self.assertEqual(out_draft["should_publish"], "true")

            # 3. Already published release -> skips both build and publish cleanly.
            _write_lf(
                state_path,
                json.dumps({"releases": [{"tag": "v0.2.0", "draft": False}]}),
            )
            proc_pub, out_pub = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc_pub.returncode, 0, msg=proc_pub.stderr)
            self.assertEqual(
                out_pub,
                {
                    "should_build": "false",
                    "should_publish": "false",
                    "release_tag": "v0.2.0",
                    "target_sha": head_sha,
                },
            )

            # 4. API 502 error -> fails fast rather than misclassifying.
            _write_lf(
                state_path,
                json.dumps({"releases": [], "view_error": "HTTP 502 Bad Gateway"}),
            )
            proc_err, _ = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(proc_err.returncode, 0)
            self.assertIn("502", proc_err.stderr)

    def test_preflight_publish_rejects_missing_remote_tag_on_non_main_or_historical_tag(
        self,
    ) -> None:
        """Publish preflight rejects non-main dispatches, tag pushes, or historical tags absent from origin even if local tag exists."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace, head_sha = self._setup_origin_and_clone(
                base,
                remote_tag=None,
            )
            _git(workspace, "tag", "v0.2.0")
            gh_env, _ = _install_fake_gh(base)

            # Non-main branch dispatch cannot mint a tag even if local tag exists.
            proc_branch, _ = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "",
                    "REF_NAME": "feature/unmerged",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(proc_branch.returncode, 0)
            self.assertIn("origin", proc_branch.stderr)

            # Tag push event where tag is absent on origin must fail.
            proc_tag, _ = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "v0.2.0",
                    "REF_TYPE": "tag",
                },
            )
            self.assertNotEqual(proc_tag.returncode, 0)
            self.assertIn("origin", proc_tag.stderr)

            # Main dispatch targeting a historical tag not on origin must fail.
            proc_hist, _ = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "v0.1.0",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(proc_hist.returncode, 0)
            self.assertIn("origin", proc_hist.stderr)

    def test_preflight_publish_rejects_invalid_or_injected_tag_syntax(self) -> None:
        """Publish preflight validates tag syntax via env vars without shell injection."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace, _ = self._setup_origin_and_clone(base)
            gh_env, _ = _install_fake_gh(base)
            marker = workspace / "injected.txt"
            bad_tags = [
                "0.2.0",
                "v01.2.0",
                "v0.2.0.1",
                "--upload-pack=evil",
                f"v0.2.0; touch {marker}",
                "v0.2.0\ninjected",
                "refs/tags/v0.2.0",
            ]
            for bad_tag in bad_tags:
                with self.subTest(bad_tag=bad_tag):
                    proc, _ = self._run_preflight_script(
                        workspace,
                        {
                            **gh_env,
                            "EVENT_NAME": "workflow_dispatch",
                            "INPUT_PUBLISH": "true",
                            "INPUT_TAG": bad_tag,
                            "REF_NAME": "main",
                            "REF_TYPE": "branch",
                        },
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("Invalid release tag syntax", proc.stderr)
                    self.assertFalse(marker.exists())

    def test_preflight_publish_verifies_license_and_version_at_target_sha(self) -> None:
        """Publish preflight inspects LICENSE and VERSION blobs at target_sha rather than branch HEAD."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            gh_env, _ = _install_fake_gh(base)

            _, workspace_no_lic, _ = self._setup_origin_and_clone(
                base / "missing-license",
                license_text=None,
                remote_tag="v0.2.0",
            )
            _write_lf(workspace_no_lic / "LICENSE", "Added Later On Main\n")
            _git(workspace_no_lic, "add", "LICENSE")
            _git(workspace_no_lic, "commit", "-m", "Add license on main after tag")
            proc_missing, _ = self._run_preflight_script(
                workspace_no_lic,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "v0.2.0",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(proc_missing.returncode, 0)
            self.assertIn("LICENSE", proc_missing.stderr)

            _, workspace_ws_lic, _ = self._setup_origin_and_clone(
                base / "whitespace-license",
                license_text="  \n\t\n",
                remote_tag="v0.2.0",
            )
            proc_ws, _ = self._run_preflight_script(
                workspace_ws_lic,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "v0.2.0",
                    "REF_TYPE": "tag",
                },
            )
            self.assertNotEqual(proc_ws.returncode, 0)
            self.assertIn("LICENSE", proc_ws.stderr)

            _, workspace_mismatch, _ = self._setup_origin_and_clone(
                base / "mismatch-version",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag="v0.1.0",
            )
            proc_mismatch, _ = self._run_preflight_script(
                workspace_mismatch,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "v0.1.0",
                    "REF_TYPE": "tag",
                },
            )
            self.assertNotEqual(proc_mismatch.returncode, 0)
            self.assertIn("does not match", proc_mismatch.stderr)

    def test_preflight_publish_resolves_annotated_and_lightweight_tag_commit_sha(
        self,
    ) -> None:
        """Publish preflight peels annotated and lightweight tags to target_sha even after main advances."""
        for annotated in (True, False):
            with self.subTest(annotated=annotated), _temp_dir() as td:
                base = Path(td)
                gh_env, _ = _install_fake_gh(base)
                origin, _, tag_sha = self._setup_origin_and_clone(
                    base,
                    version_text="0.2.0\n",
                    license_text="MIT License\n",
                    remote_tag="v0.2.0",
                    annotated=annotated,
                )
                seed = base / "seed"
                _write_lf(seed / "VERSION", "0.3.0\n")
                _git(seed, "rm", "LICENSE")
                _git(seed, "add", "VERSION")
                _git(seed, "commit", "-m", "Advance main past release")
                _git(seed, "push", "origin", "HEAD:refs/heads/main")

                fresh_checkout = base / "fresh-checkout"
                _git(
                    base,
                    "clone",
                    "--depth=1",
                    "--no-tags",
                    origin.as_uri(),
                    str(fresh_checkout),
                )
                _configure_local_test_repo(fresh_checkout)
                self.assertEqual(
                    _git(fresh_checkout, "rev-parse", "--is-shallow-repository"),
                    "true",
                )

                proc_dispatch, out_dispatch = self._run_preflight_script(
                    fresh_checkout,
                    {
                        **gh_env,
                        "EVENT_NAME": "workflow_dispatch",
                        "INPUT_PUBLISH": "true",
                        "INPUT_TAG": "v0.2.0",
                        "REF_NAME": "main",
                        "REF_TYPE": "branch",
                    },
                )
                self.assertEqual(proc_dispatch.returncode, 0, msg=proc_dispatch.stderr)
                self.assertEqual(
                    out_dispatch,
                    {
                        "should_build": "true",
                        "should_publish": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": tag_sha,
                    },
                )

                proc_push, out_push = self._run_preflight_script(
                    fresh_checkout,
                    {
                        **gh_env,
                        "EVENT_NAME": "push",
                        "INPUT_PUBLISH": "",
                        "INPUT_TAG": "",
                        "REF_NAME": "v0.2.0",
                        "REF_TYPE": "tag",
                    },
                )
                self.assertEqual(proc_push.returncode, 0, msg=proc_push.stderr)
                self.assertEqual(
                    out_push,
                    {
                        "should_build": "true",
                        "should_publish": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": tag_sha,
                    },
                )

    def test_publish_pushes_missing_tag_on_main_and_selects_notes(self) -> None:
        """On main with missing tag, Publish GitHub Release pushes v<VERSION> and selects --notes-file or --generate-notes."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            origin, workspace, head_sha = self._setup_origin_and_clone(
                base / "curated",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag=None,
            )
            (workspace / "docs").mkdir(exist_ok=True)
            _write_lf(workspace / "docs/release-notes-0.2.0.md", "# 0.2.0 notes\n")
            dist_dir = workspace / "dist" / "release"
            dist_dir.mkdir(parents=True)
            _write_lf(dist_dir / "pack.tar.gz", "archive-v1")

            gh_env, state_path = _install_fake_gh(base / "curated")
            summary_file = base / "curated-summary.md"
            res = self._run_publish_script(
                workspace,
                {
                    **gh_env,
                    "GH_REPO": "fderuiter/fred-skills",
                    "GH_TOKEN": "synthetic-token",
                    "GITHUB_STEP_SUMMARY": str(summary_file),
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": head_sha,
                    "INPUT_TAG": "",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                },
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            remote_tags = _git(
                workspace, "ls-remote", "--refs", origin.as_uri(), "refs/tags/v0.2.0"
            )
            self.assertIn("refs/tags/v0.2.0", remote_tags)
            self.assertIn(
                "Published new GitHub Release",
                summary_file.read_text(encoding="utf-8"),
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)
            created = state["releases"][0]
            self.assertEqual(created["tag"], "v0.2.0")
            self.assertFalse(created["draft"])
            self.assertFalse(created["prerelease"])
            self.assertTrue(created["verify_tag"])
            self.assertFalse(created["generate_notes"])
            self.assertEqual(created["notes_file"], "docs/release-notes-0.2.0.md")
            self.assertEqual(created["target"], head_sha)

            # Without docs/release-notes-<VERSION>.md, falls back to --generate-notes; pre-release sets --prerelease.
            origin_gen, workspace_gen, gen_sha = self._setup_origin_and_clone(
                base / "generated",
                version_text="0.3.0-rc.1\n",
                license_text="MIT License\n",
                remote_tag=None,
            )
            dist_gen = workspace_gen / "dist" / "release"
            dist_gen.mkdir(parents=True)
            _write_lf(dist_gen / "pack.tar.gz", "archive-v0.3.0-rc.1")
            gh_env_gen, state_gen_path = _install_fake_gh(base / "generated")
            res_gen = self._run_publish_script(
                workspace_gen,
                {
                    **gh_env_gen,
                    "GH_REPO": "fderuiter/fred-skills",
                    "RELEASE_TAG": "v0.3.0-rc.1",
                    "TARGET_SHA": gen_sha,
                    "INPUT_TAG": "",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                },
            )
            self.assertEqual(res_gen.returncode, 0, msg=res_gen.stderr)
            self.assertIn(
                "refs/tags/v0.3.0-rc.1",
                _git(
                    workspace_gen,
                    "ls-remote",
                    "--refs",
                    origin_gen.as_uri(),
                    "refs/tags/v0.3.0-rc.1",
                ),
            )
            state_gen = json.loads(state_gen_path.read_text(encoding="utf-8"))
            self.assertTrue(state_gen["releases"][0]["generate_notes"])
            self.assertIsNone(state_gen["releases"][0]["notes_file"])
            self.assertFalse(state_gen["releases"][0]["draft"])
            self.assertTrue(state_gen["releases"][0]["prerelease"])

    def test_publish_promotes_draft_skips_published_and_handles_errors(self) -> None:
        """Publish GitHub Release promotes existing drafts with --clobber + edit --draft=false, skips published, and fails on API errors."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace, tag_sha = self._setup_origin_and_clone(
                base,
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag="v0.2.0",
            )
            (workspace / "docs").mkdir(exist_ok=True)
            _write_lf(workspace / "docs/release-notes-0.2.0.md", "# Curated 0.2.0\n")
            dist_dir = workspace / "dist" / "release"
            dist_dir.mkdir(parents=True)
            _write_lf(dist_dir / "pack.tar.gz", "archive-v2")

            gh_env, state_path = _install_fake_gh(
                base,
                {
                    "releases": [
                        {
                            "tag": "v0.2.0",
                            "draft": True,
                            "assets": {"pack.tar.gz": "archive-v1"},
                        }
                    ]
                },
            )
            base_env = {
                **gh_env,
                "GH_REPO": "fderuiter/fred-skills",
                "RELEASE_TAG": "v0.2.0",
                "TARGET_SHA": tag_sha,
                "INPUT_TAG": "",
                "REF_NAME": "v0.2.0",
                "REF_TYPE": "tag",
            }

            # 1. Existing draft is updated via --clobber and promoted via gh release edit --draft=false --verify-tag.
            res_draft = self._run_publish_script(workspace, base_env)
            self.assertEqual(res_draft.returncode, 0, msg=res_draft.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)
            rel = state["releases"][0]
            self.assertFalse(rel["draft"])
            self.assertTrue(rel.get("verify_tag"))
            self.assertTrue(rel.get("last_upload_clobber"))
            self.assertEqual(rel["assets"]["pack.tar.gz"], "archive-v2")
            self.assertEqual(rel["notes_file"], "docs/release-notes-0.2.0.md")

            # 2. Subsequent run on already-published release exits 0 without overwriting assets.
            _write_lf(dist_dir / "pack.tar.gz", "archive-v3")
            res_pub = self._run_publish_script(workspace, base_env)
            self.assertEqual(res_pub.returncode, 0, msg=res_pub.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["releases"][0]["assets"]["pack.tar.gz"], "archive-v2"
            )

            # 3. Unexpected gh release view errors fail non-zero without creating duplicate releases.
            state["view_error"] = "HTTP 502 Bad Gateway"
            _write_lf(state_path, json.dumps(state))
            res_err = self._run_publish_script(workspace, base_env)
            self.assertNotEqual(res_err.returncode, 0)
            self.assertIn("502", res_err.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)

            # 4. Defense in depth: non-main ref with missing remote tag is rejected in Publish GitHub Release.
            _, untagged_ws, untagged_sha = self._setup_origin_and_clone(
                base / "untagged-branch",
                remote_tag=None,
            )
            (untagged_ws / "dist" / "release").mkdir(parents=True)
            _write_lf(untagged_ws / "dist/release/pack.tar.gz", "archive-branch")
            res_untagged = self._run_publish_script(
                untagged_ws,
                {
                    **gh_env,
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": untagged_sha,
                    "INPUT_TAG": "",
                    "REF_NAME": "feature/branch",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(res_untagged.returncode, 0)
            self.assertIn("origin", res_untagged.stderr)

    def test_reusable_validate_workflow_blocks_release_across_all_ci_gates(
        self,
    ) -> None:
        """release.yml calls reusable validate.yml so Ubuntu/Windows, Python 3.12/3.13, pre-commit, walkthrough, and Chromium gate releases."""
        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        triggers = validate_data.get("on") or validate_data.get(True) or {}
        self.assertIn("workflow_call", triggers)
        wf_call_inputs = (triggers["workflow_call"] or {}).get("inputs", {})
        self.assertIn("ref", wf_call_inputs)
        self.assertEqual(wf_call_inputs["ref"].get("default"), "")

        matrix = validate_data["jobs"]["validate"]["strategy"]["matrix"]
        self.assertEqual(matrix["os"], ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix["python-version"], ["3.12", "3.13"])

        validate_steps = validate_data["jobs"]["validate"]["steps"]
        validate_checkout = validate_steps[0]
        self.assertEqual(validate_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            validate_checkout.get("with", {}).get("ref"), "${{ inputs.ref }}"
        )
        run_cmds = "\n".join(s.get("run", "") for s in validate_steps)
        self.assertIn("pre-commit run --all-files", run_cmds)
        self.assertIn("examples/walkthrough/run_demo.py", run_cmds)
        self.assertIn("verify-release", run_cmds)

        browser_steps = validate_data["jobs"]["browser"]["steps"]
        browser_checkout = browser_steps[0]
        self.assertEqual(browser_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            browser_checkout.get("with", {}).get("ref"), "${{ inputs.ref }}"
        )
        browser_cmds = "\n".join(s.get("run", "") for s in browser_steps)
        self.assertIn("python tests/browser_check.py", browser_cmds)

    def test_preflight_and_prerequisite_reject_unmerged_commits_outside_origin_main(
        self,
    ) -> None:
        """Unmerged tags or workflow_dispatch runs outside origin/main fail in both preflight and Verify release prerequisites."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            gh_env, _ = _install_fake_gh(base)
            origin, _, main_sha = self._setup_origin_and_clone(
                base,
                version_text="0.1.0\n",
                license_text="MIT License\n",
                remote_tag="v0.1.0",
            )

            seed = base / "seed"
            _git(seed, "checkout", "-b", "feature/unmerged")
            _write_lf(seed / "VERSION", "0.2.0\n")
            _git(seed, "add", "VERSION")
            _git(seed, "commit", "-m", "Unmerged version bump")
            unmerged_sha = _git(seed, "rev-parse", "HEAD")
            _git(seed, "push", "origin", "HEAD:refs/heads/feature/unmerged")
            _git(seed, "tag", "-a", "v0.2.0", "-m", "Unmerged tag v0.2.0")
            _git(seed, "push", "origin", "refs/tags/v0.2.0")

            unmerged_ws = base / "unmerged-ws"
            _git(
                base,
                "clone",
                "--depth=1",
                "--no-tags",
                "--branch",
                "feature/unmerged",
                origin.as_uri(),
                str(unmerged_ws),
            )
            _configure_local_test_repo(unmerged_ws)

            # 1. Tag push from unmerged commit fails preflight ancestry check.
            preflight_tag, _ = self._run_preflight_script(
                unmerged_ws,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "WORKFLOW_SHA": unmerged_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "v0.2.0",
                    "REF_TYPE": "tag",
                },
            )
            self.assertNotEqual(preflight_tag.returncode, 0)
            self.assertIn("not an ancestor of origin/main", preflight_tag.stderr)

            # 2. workflow_dispatch publish=true on unmerged branch fails preflight ancestry check.
            preflight_dispatch, _ = self._run_preflight_script(
                unmerged_ws,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": unmerged_sha,
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "v0.2.0",
                    "REF_NAME": "feature/unmerged",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(preflight_dispatch.returncode, 0)
            self.assertIn("not an ancestor of origin/main", preflight_dispatch.stderr)

            # 3. Verify release prerequisites rejects GITHUB_SHA outside origin/main after unshallow fetch.
            prereq_unmerged = self._run_verification_script(
                unmerged_ws,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_SHA": unmerged_sha,
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                },
            )
            self.assertNotEqual(prereq_unmerged.returncode, 0)
            self.assertIn("not an ancestor of origin/main", prereq_unmerged.stderr)

            # 4. Dry-run workflow_dispatch (publish=false) on unmerged branch still succeeds for artifact inspection.
            preflight_dry, dry_outputs = self._run_preflight_script(
                unmerged_ws,
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": unmerged_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "feature/unmerged",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(preflight_dry.returncode, 0, msg=preflight_dry.stderr)
            self.assertEqual(dry_outputs["should_build"], "true")
            self.assertEqual(dry_outputs["should_publish"], "false")
            self.assertNotEqual(main_sha, unmerged_sha)

    def test_prerequisite_enforces_remote_tag_binding_and_accepts_historical_main_ancestor(
        self,
    ) -> None:
        """Verify release prerequisites rejects missing/mismatched remote tags and accepts ancestor commits on origin/main."""
        with _temp_dir() as tmp_dir:
            base = Path(tmp_dir)
            gh_env, _ = _install_fake_gh(base)
            origin, untagged_ws, head_sha = self._setup_origin_and_clone(
                base / "missing-tag",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag=None,
            )

            # 1. Missing remote v<VERSION> tag fails on tag push and on workflow_dispatch (even on main).
            prereq_missing_tag = self._run_verification_script(
                untagged_ws,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_SHA": head_sha,
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                },
            )
            self.assertNotEqual(prereq_missing_tag.returncode, 0)
            self.assertIn("refs/tags/v0.2.0", prereq_missing_tag.stderr)

            prereq_dispatch_main = self._run_verification_script(
                untagged_ws,
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_SHA": head_sha,
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertNotEqual(prereq_dispatch_main.returncode, 0)
            self.assertIn("refs/tags/v0.2.0", prereq_dispatch_main.stderr)

            preflight_dispatch_main, _ = self._run_preflight_script(
                untagged_ws,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "WORKFLOW_SHA": head_sha,
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                },
            )
            self.assertNotEqual(preflight_dispatch_main.returncode, 0)
            self.assertIn("refs/tags/v0.2.0", preflight_dispatch_main.stderr)

            # 2. Direct push to main (EVENT_NAME=push, branch:main) passes prerequisite check before Publish mints tag.
            prereq_main_push = self._run_verification_script(
                untagged_ws,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_SHA": head_sha,
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(
                prereq_main_push.returncode, 0, msg=prereq_main_push.stderr
            )

            # 3. Historical ancestor commit on origin/main passes after main advances in a shallow checkout.
            hist_origin, _, v020_sha = self._setup_origin_and_clone(
                base / "historical",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag="v0.2.0",
                annotated=True,
            )
            hist_seed = base / "historical" / "seed"
            _write_lf(hist_seed / "VERSION", "0.3.0\n")
            _git(hist_seed, "add", "VERSION")
            _git(hist_seed, "commit", "-m", "Advance main to 0.3.0")
            _git(hist_seed, "push", "origin", "HEAD:refs/heads/main")

            shallow_tag_ws = base / "historical" / "shallow-tag-ws"
            shallow_tag_ws.mkdir(parents=True)
            _git(shallow_tag_ws, "init")
            _configure_local_test_repo(shallow_tag_ws)
            _git(shallow_tag_ws, "remote", "add", "origin", hist_origin.as_uri())
            _git(
                shallow_tag_ws,
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "--depth=1",
                "origin",
                "refs/tags/v0.2.0:refs/tags/v0.2.0",
            )
            _git(shallow_tag_ws, "checkout", "--detach", v020_sha)
            self.assertEqual(
                _git(shallow_tag_ws, "rev-parse", "--is-shallow-repository"), "true"
            )

            prereq_hist = self._run_verification_script(
                shallow_tag_ws,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_SHA": v020_sha,
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(prereq_hist.returncode, 0, msg=prereq_hist.stderr)

            # 4. Remote tag pointing to a different commit than GITHUB_SHA / TARGET_SHA is rejected.
            _git(hist_seed, "tag", "-f", "v0.2.0", "HEAD")
            _git(hist_seed, "push", "--force", "origin", "refs/tags/v0.2.0")
            prereq_retargeted = self._run_verification_script(
                shallow_tag_ws,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_SHA": v020_sha,
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                },
            )
            self.assertNotEqual(prereq_retargeted.returncode, 0)
            self.assertIn("expected", prereq_retargeted.stderr)
            self.assertNotEqual(origin, hist_origin)

    def test_releasing_docs_match_workflow_contracts(self) -> None:
        """docs/releasing.md must document reusable validation, main ancestry, tag verification, dry-run gating, and zero-touch publish."""
        doc_text = RELEASING_DOC.read_text(encoding="utf-8")
        self.assertIn("Pushes to `main`", doc_text)
        self.assertIn("validate.yml", doc_text)
        self.assertIn("origin/main", doc_text)
        self.assertIn("--verify-tag", doc_text)
        self.assertIn("--target", doc_text)
        self.assertIn("inputs.publish", doc_text)
        self.assertIn("`false` by default", doc_text)
        self.assertIn("inputs.tag", doc_text)
        self.assertIn("release-artifacts", doc_text)
        self.assertIn("[LICENSE](../LICENSE)", doc_text)


class WorkflowActionPinVerificationTests(unittest.TestCase):
    """Test offline SHA/comment checks and mocked online git ls-remote verification."""

    def test_repository_workflows_pass_offline_check(self) -> None:
        """All repository workflows must pass offline SHA and version comment checks."""
        errors = verify_workflow_actions.verify_workflows(root=ROOT, online=False)
        self.assertEqual([], errors, f"Workflow pin check failed: {errors}")

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_workflow_actions.py"),
                "--check",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_validate_workflow_runs_windows_offline_check_and_single_online_check(
        self,
    ) -> None:
        """validate.yml must run offline pin checks across OS matrix (including Windows) and --online once."""
        import run_checks

        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        steps = validate_data["jobs"]["validate"]["steps"]

        pkg_step = next(
            s
            for s in steps
            if "scripts/run_checks.py --phase packages" in s.get("run", "")
        )
        self.assertIsNone(
            pkg_step.get("if"),
            "packages phase must run unconditionally across Ubuntu and Windows runners",
        )
        pkg_cmds = run_checks.build_phase_commands("packages", root=ROOT)
        self.assertTrue(
            any(
                cmd[-2].endswith("verify_workflow_actions.py") and cmd[-1] == "--check"
                for cmd in pkg_cmds
            ),
            "packages phase must execute scripts/verify_workflow_actions.py --check",
        )

        online_steps = [
            s
            for s in steps
            if "check_workflow_pins.py --verify-remote" in s.get("run", "")
        ]
        self.assertEqual(1, len(online_steps))
        condition = online_steps[0].get("if", "")
        self.assertIn("matrix.os", condition)
        self.assertIn("matrix.python-version", condition)

    def test_offline_check_rejects_mutable_refs_uppercase_shas_and_missing_comments(
        self,
    ) -> None:
        """Offline check rejects unpinned tags, short/uppercase SHAs, and missing/invalid comments."""
        workflow_yaml = """\
name: Synthetic
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./local-action
      - name: Run shell with uses text
        run: |
          echo "uses: actions/checkout@main"
      - uses: actions/checkout@v6 # v6
      - uses: actions/setup-python@ECE7CB06CAEFA5FFF74198D8649806C4678C61A1 # v6
      - uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # main
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf_dir = Path(tmp_dir) / ".github/workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(workflow_yaml, encoding="utf-8")

            errors = verify_workflow_actions.verify_workflows(
                root=Path(tmp_dir),
                online=False,
            )
            self.assertEqual(4, len(errors), f"Unexpected errors: {errors}")
            self.assertTrue(any("got 'v6'" in err for err in errors))
            self.assertTrue(any("lowercase hex commit SHA" in err for err in errors))
            self.assertTrue(
                any("missing a trailing version comment" in err for err in errors)
            )
            self.assertTrue(
                any("invalid version comment '# main'" in err for err in errors)
            )

    def test_online_check_rejects_nonexistent_sha(self) -> None:
        """Online check rejects nonexistent commit SHAs like the broken attest-build-provenance pin."""
        broken_workflow = """\
name: BrokenRelease
on: [push]
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/attest-build-provenance@c074443f1a5fb4aeeae8296ba60daec12367e163 # v2.2.3
"""
        ls_remote_stdout = (
            "c074443f1aee8d4aeeae555aebba3282517141b2\trefs/tags/v2.2.3\n"
            "c074443f1aee8d4aeeae555aebba3282517141b2\trefs/heads/main\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf = Path(tmp_dir) / "release.yml"
            wf.write_text(broken_workflow, encoding="utf-8")
            with mock.patch(
                "verify_workflow_actions.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=0,
                    stdout=ls_remote_stdout,
                    stderr="",
                ),
            ) as mock_run:
                errors = verify_workflow_actions.verify_workflows(
                    root=Path(tmp_dir),
                    workflow_files=[wf],
                    online=True,
                    timeout=15.0,
                )

            self.assertEqual(1, len(errors), f"Unexpected errors: {errors}")
            self.assertIn("c074443f1a5fb4aeeae8296ba60daec12367e163", errors[0])
            self.assertIn("not found in upstream refs", errors[0])
            self.assertIn("c074443f1aee8d4aeeae555aebba3282517141b2", errors[0])
            _, kwargs = mock_run.call_args
            self.assertIs(kwargs.get("check"), False)
            self.assertEqual(kwargs.get("timeout"), 15.0)

    def test_online_check_resolves_peeled_annotated_tags_and_rejects_tag_object_sha(
        self,
    ) -> None:
        """Online check resolves annotated tags ending in ^{} to commit SHAs rather than tag objects."""
        peeled_ls_remote = (
            "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111\trefs/tags/v2.2.3\n"
            "c074443f1aee8d4aeeae555aebba3282517141b2\trefs/tags/v2.2.3^{}\n"
        )
        valid_workflow = """\
name: AnnotatedValid
on: [push]
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/attest-build-provenance@c074443f1aee8d4aeeae555aebba3282517141b2 # v2.2.3
"""
        tag_object_workflow = """\
name: AnnotatedTagObject
on: [push]
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/attest-build-provenance@aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111 # v2.2.3
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            valid_wf = Path(tmp_dir) / "valid.yml"
            valid_wf.write_text(valid_workflow, encoding="utf-8")
            bad_wf = Path(tmp_dir) / "bad.yml"
            bad_wf.write_text(tag_object_workflow, encoding="utf-8")

            with mock.patch(
                "verify_workflow_actions.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=0,
                    stdout=peeled_ls_remote,
                    stderr="",
                ),
            ):
                self.assertEqual(
                    [],
                    verify_workflow_actions.verify_workflows(
                        root=Path(tmp_dir),
                        workflow_files=[valid_wf],
                        online=True,
                    ),
                )
                bad_errors = verify_workflow_actions.verify_workflows(
                    root=Path(tmp_dir),
                    workflow_files=[bad_wf],
                    online=True,
                )
                self.assertEqual(1, len(bad_errors))
                self.assertIn("aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111", bad_errors[0])

    def test_online_check_accepts_major_version_comment_matching_semver_tag_and_deduplicates_repos(
        self,
    ) -> None:
        """Major version comments like '# v4' accept 'refs/tags/v4.1.9', reject 'v3.9.0'/'v40.0.0', and query each repo once."""
        ls_remote_stdout = (
            "ffffffffffffffffffffffffffffffffffffffff\trefs/tags/v4\n"
            "cc203385981b70ca67e1cc392babf9cc229d5806\trefs/tags/v4.1.9\n"
            "3333333333333333333333333333333333333333\trefs/tags/v3.9.0\n"
            "4040404040404040404040404040404040404040\trefs/tags/v40.0.0\n"
        )
        valid_major_wf = """\
name: MajorCommentValid
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@cc203385981b70ca67e1cc392babf9cc229d5806 # v4
      - uses: actions/download-artifact@ffffffffffffffffffffffffffffffffffffffff # v4
"""
        mismatched_major_wf = """\
name: MajorCommentMismatch
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@3333333333333333333333333333333333333333 # v4
      - uses: actions/download-artifact@4040404040404040404040404040404040404040 # v4
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            valid_path = Path(tmp_dir) / "valid_major.yml"
            valid_path.write_text(valid_major_wf, encoding="utf-8")
            mismatch_path = Path(tmp_dir) / "mismatch_major.yml"
            mismatch_path.write_text(mismatched_major_wf, encoding="utf-8")

            with mock.patch(
                "verify_workflow_actions.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=0,
                    stdout=ls_remote_stdout,
                    stderr="",
                ),
            ) as mock_run:
                errors = verify_workflow_actions.verify_workflows(
                    root=Path(tmp_dir),
                    workflow_files=[valid_path],
                    online=True,
                )
                self.assertEqual([], errors)
                self.assertEqual(1, mock_run.call_count)

                mismatch_errors = verify_workflow_actions.verify_workflows(
                    root=Path(tmp_dir),
                    workflow_files=[mismatch_path],
                    online=True,
                )
                self.assertEqual(2, len(mismatch_errors), mismatch_errors)
                self.assertTrue(any("v3.9.0" in err for err in mismatch_errors))
                self.assertTrue(any("v40.0.0" in err for err in mismatch_errors))

    def test_online_check_handles_timeout_cleanly(self) -> None:
        """Online check catches subprocess.TimeoutExpired and reports a clear error."""
        workflow_yaml = """\
name: TimeoutCheck
on: [push]
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            wf_path = Path(tmp_dir) / "timeout.yml"
            wf_path.write_text(workflow_yaml, encoding="utf-8")
            with mock.patch(
                "verify_workflow_actions.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["git", "ls-remote"], timeout=5.0
                ),
            ):
                errors = verify_workflow_actions.verify_workflows(
                    root=Path(tmp_dir),
                    workflow_files=[wf_path],
                    online=True,
                    timeout=5.0,
                )
            self.assertEqual(1, len(errors))
            self.assertIn("timed out", errors[0])

    def test_action_pins_lockfile_records_all_repository_actions_and_passes_offline(
        self,
    ) -> None:
        """The committed .github/action-pins.json lockfile must record all nine repository actions and pass check_workflow_pins.py."""
        self.assertTrue(ACTION_PINS_LOCKFILE.is_file())
        locked, lock_errors = check_workflow_pins.load_lockfile(
            ACTION_PINS_LOCKFILE, root=ROOT
        )
        self.assertEqual([], lock_errors)
        self.assertEqual(
            {
                "actions/attest-build-provenance": (
                    "4d101475d8b20a2381f78447822ac1eab6504dd8",
                    "v4.2.2",
                ),
                "actions/cache": (
                    "0057852bfaa89a56745cba8c7296529d2fc39830",
                    "v4",
                ),
                "actions/checkout": (
                    "d23441a48e516b6c34aea4fa41551a30e30af803",
                    "v6",
                ),
                "actions/configure-pages": (
                    "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
                    "v6.0.0",
                ),
                "actions/deploy-pages": (
                    "368f82528645a54fb793d4d04e342629a3f51346",
                    "v5.0.1",
                ),
                "actions/download-artifact": (
                    "cc203385981b70ca67e1cc392babf9cc229d5806",
                    "v4",
                ),
                "actions/setup-python": (
                    "ece7cb06caefa5fff74198d8649806c4678c61a1",
                    "v6",
                ),
                "actions/upload-artifact": (
                    "ea165f8d65b6e75b540449e92b4886f43607fa02",
                    "v4",
                ),
                "actions/upload-pages-artifact": (
                    "fc324d3547104276b827a68afc52ff2a11cc49c9",
                    "v5.0.0",
                ),
            },
            {repo: (pin.sha, pin.version) for repo, pin in locked.items()},
        )

        self.assertEqual([], check_workflow_pins.check_pins(root=ROOT))
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_workflow_pins.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_lockfile_parity_rejects_missing_malformed_drift_and_unused_entries(
        self,
    ) -> None:
        """Offline lockfile check rejects missing/malformed lockfiles, SHA/version drift, unrecorded actions, and stale entries."""
        valid_workflow = """\
name: CI
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6
      - uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            wf_dir = tmp_root / ".github/workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(valid_workflow, encoding="utf-8")
            lock_path = tmp_root / ".github/action-pins.json"

            # 1. Missing lockfile fails by default.
            missing_errors = check_workflow_pins.check_pins(root=tmp_root)
            self.assertTrue(
                any("does not exist" in err for err in missing_errors),
                missing_errors,
            )

            # 2. Malformed JSON lockfile fails cleanly.
            lock_path.write_text("{not-valid-json", encoding="utf-8")
            malformed_errors = check_workflow_pins.check_pins(root=tmp_root)
            self.assertTrue(
                any("Invalid JSON" in err for err in malformed_errors),
                malformed_errors,
            )

            # 3. SHA mismatch, version mismatch, missing action, and unused lockfile entry are all reported.
            drifted_lockfile = {
                "schema_version": 1,
                "actions": {
                    "actions/checkout": {
                        "sha": "0" * 40,
                        "version": "v5",
                    },
                    "actions/stale-unused": {
                        "sha": "1" * 40,
                        "version": "v1",
                    },
                },
            }
            lock_path.write_text(json.dumps(drifted_lockfile), encoding="utf-8")
            drift_errors = check_workflow_pins.check_pins(root=tmp_root)
            self.assertEqual(4, len(drift_errors), drift_errors)
            self.assertTrue(
                any("SHA" in err and "does not match" in err for err in drift_errors)
            )
            self.assertTrue(
                any(
                    "version comment '# v6' does not match" in err
                    for err in drift_errors
                )
            )
            self.assertTrue(
                any(
                    "Action 'actions/attest-build-provenance' is missing from .github/action-pins.json"
                    in err
                    for err in drift_errors
                )
            )
            self.assertTrue(
                any(
                    "Unused lockfile entry 'actions/stale-unused'" in err
                    for err in drift_errors
                )
            )

            # 4. Matching lockfile passes with zero errors.
            matching_lockfile = {
                "schema_version": 1,
                "actions": {
                    "actions/attest-build-provenance": {
                        "sha": "4d101475d8b20a2381f78447822ac1eab6504dd8",
                        "version": "v4.2.2",
                    },
                    "actions/checkout": {
                        "sha": "d23441a48e516b6c34aea4fa41551a30e30af803",
                        "version": "v6",
                    },
                },
            }
            lock_path.write_text(json.dumps(matching_lockfile), encoding="utf-8")
            self.assertEqual([], check_workflow_pins.check_pins(root=tmp_root))

    def test_check_pins_skips_local_and_docker_steps_and_rejects_malformed_pins(
        self,
    ) -> None:
        """check_workflow_pins skips './' and 'docker://' steps while rejecting unpinned tags, short SHAs, and missing comments."""
        workflow_yaml = """\
name: MixedSteps
on: [push]
jobs:
  local_reusable:
    uses: ./.github/workflows/reusable.yml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: ./scripts/local-step
      - uses: docker://alpine:3.20
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6
"""
        invalid_workflow_yaml = """\
name: InvalidPins
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6 # v6
      - uses: actions/setup-python@ece7cb06 # v6
      - uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830
"""
        lockfile = {
            "schema_version": 1,
            "actions": {
                "actions/checkout": {
                    "sha": "d23441a48e516b6c34aea4fa41551a30e30af803",
                    "version": "v6",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            wf_dir = tmp_root / ".github/workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "mixed.yml").write_text(workflow_yaml, encoding="utf-8")
            (tmp_root / ".github/action-pins.json").write_text(
                json.dumps(lockfile), encoding="utf-8"
            )
            self.assertEqual([], check_workflow_pins.check_pins(root=tmp_root))

            bad_wf = tmp_root / "invalid.yml"
            bad_wf.write_text(invalid_workflow_yaml, encoding="utf-8")
            bad_errors = check_workflow_pins.check_pins(
                root=tmp_root, workflow_files=[bad_wf]
            )
            self.assertEqual(3, len(bad_errors), bad_errors)
            self.assertTrue(any("got 'v6'" in err for err in bad_errors))
            self.assertTrue(any("got 'ece7cb06'" in err for err in bad_errors))
            self.assertTrue(
                any("missing a trailing version comment" in err for err in bad_errors)
            )

    def test_verify_remote_dereferences_annotated_v4_tag_and_retries_transient_errors(
        self,
    ) -> None:
        """--verify-remote dereferences refs/tags/v4^{} and retries transient git ls-remote errors."""
        workflow_yaml = """\
name: ReleaseAttest
on: [push]
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4
"""
        lockfile = {
            "schema_version": 1,
            "actions": {
                "actions/attest-build-provenance": {
                    "sha": "4d101475d8b20a2381f78447822ac1eab6504dd8",
                    "version": "v4",
                }
            },
        }
        ls_remote_stdout = (
            "8beda2b7ed98355c0e97c0a63bec38ae472e66c4\trefs/tags/v4\n"
            "4d101475d8b20a2381f78447822ac1eab6504dd8\trefs/tags/v4^{}\n"
            "4d101475d8b20a2381f78447822ac1eab6504dd8\trefs/tags/v4.2.2\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            wf_dir = tmp_root / ".github/workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "release.yml").write_text(workflow_yaml, encoding="utf-8")
            (tmp_root / ".github/action-pins.json").write_text(
                json.dumps(lockfile), encoding="utf-8"
            )

            slept: list[float] = []
            attempts: list[subprocess.CompletedProcess[str] | BaseException] = [
                subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=128,
                    stdout="",
                    stderr="fatal: unable to access: The requested URL returned error: 502",
                ),
                subprocess.TimeoutExpired(cmd=["git", "ls-remote"], timeout=10.0),
                subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=0,
                    stdout=ls_remote_stdout,
                    stderr="",
                ),
            ]
            call_idx = 0

            def fake_run(
                *_args: Any, **_kwargs: Any
            ) -> subprocess.CompletedProcess[str]:
                nonlocal call_idx
                item = attempts[call_idx]
                call_idx += 1
                if isinstance(item, BaseException):
                    raise item
                return item

            errors = check_workflow_pins.check_pins(
                root=tmp_root,
                verify_remote=True,
                timeout=10.0,
                retries=2,
                retry_backoff=0.5,
                run_fn=fake_run,
                sleep_fn=slept.append,
            )
            self.assertEqual([], errors)
            self.assertEqual(3, call_idx)
            self.assertEqual([0.5, 1.0], slept)

            # Exhausting retries surfaces the final remote error cleanly.
            always_fail_slept: list[float] = []
            fail_errors = check_workflow_pins.check_pins(
                root=tmp_root,
                verify_remote=True,
                timeout=10.0,
                retries=1,
                retry_backoff=0.25,
                run_fn=lambda *_a, **_k: subprocess.CompletedProcess(
                    args=["git", "ls-remote"],
                    returncode=128,
                    stdout="",
                    stderr="fatal: unable to access: Connection reset by peer",
                ),
                sleep_fn=always_fail_slept.append,
            )
            self.assertEqual(1, len(fail_errors), fail_errors)
            self.assertIn("Connection reset by peer", fail_errors[0])
            self.assertEqual([0.25], always_fail_slept)


if __name__ == "__main__":
    unittest.main()

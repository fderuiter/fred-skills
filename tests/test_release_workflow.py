"""Tests for release workflow structure, preflight gating, remote tag checks, action SHA pins, and draft enforcement."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_workflow_actions

RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"
RELEASING_DOC = ROOT / "docs/releasing.md"

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
        verify_tag = "--verify-tag" in args
        generate_notes = "--generate-notes" in args
        target = None
        title = None
        assets = []
        i = 3
        while i < len(args):
            tok = args[i]
            if tok in ("--draft", "-d", "--verify-tag", "--generate-notes"):
                i += 1
            elif tok in ("--target", "--title", "-t"):
                if tok == "--target":
                    target = args[i + 1]
                else:
                    title = args[i + 1]
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
                "verify_tag": verify_tag,
                "generate_notes": generate_notes,
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

    sys.stderr.write(f"unsupported release subcommand: {subcmd}\\n")
    sys.exit(2)
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


def _parse_github_output(output_file: Path) -> dict[str, str]:
    if not output_file.is_file():
        return {}
    parsed: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            parsed[key] = val
    return parsed


class ReleaseWorkflowTests(unittest.TestCase):
    """Verify release workflow preconditions, preflight gate, and idempotent draft enforcement."""

    @classmethod
    def setUpClass(cls):
        cls.workflow_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        cls.workflow_data = yaml.safe_load(cls.workflow_text)

    def _triggers(self) -> dict:
        return self.workflow_data.get("on") or self.workflow_data.get(True) or {}

    def test_workflow_yaml_syntax_and_structure(self):
        """release.yml must parse cleanly and configure preflight, build, and release gates."""
        self.assertEqual(self.workflow_data.get("permissions"), {"contents": "read"})
        triggers = self._triggers()
        dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertIn("tag", dispatch_inputs)
        self.assertIn("publish", dispatch_inputs)
        self.assertEqual(dispatch_inputs["publish"]["type"], "boolean")
        self.assertIs(dispatch_inputs["publish"]["default"], False)

        jobs = self.workflow_data["jobs"]
        self.assertIn("preflight", jobs)
        self.assertIn("build", jobs)
        self.assertIn("release", jobs)

        preflight = jobs["preflight"]
        self.assertEqual(preflight.get("permissions"), {"contents": "read"})
        self.assertEqual(
            set(preflight.get("outputs", {}).keys()),
            {"publish_enabled", "release_tag", "target_sha"},
        )

        build = jobs["build"]
        self.assertEqual(build.get("needs"), "preflight")
        self.assertEqual(build.get("permissions"), {"contents": "read"})
        build_checkout = build["steps"][0]
        self.assertEqual(
            build_checkout["with"]["ref"], "${{ needs.preflight.outputs.target_sha }}"
        )

        release = jobs["release"]
        self.assertEqual(release.get("needs"), ["preflight", "build"])
        self.assertEqual(
            release.get("if"), "needs.preflight.outputs.publish_enabled == 'true'"
        )
        self.assertEqual(
            release.get("permissions"),
            {"contents": "write", "id-token": "write", "attestations": "write"},
        )
        release_checkout = release["steps"][0]
        self.assertEqual(
            release_checkout["with"]["ref"], "${{ needs.preflight.outputs.target_sha }}"
        )

    def test_no_inline_workflow_expressions_in_shell_steps(self):
        """Shell run blocks must consume inputs via env blocks rather than inline expressions."""
        for job_name, job in self.workflow_data["jobs"].items():
            for step in job.get("steps", []):
                if "run" in step:
                    self.assertNotIn(
                        "${{",
                        step["run"],
                        f"Inline expression found in {job_name} step {step.get('name')!r}",
                    )

    def test_build_job_preserves_existing_steps(self):
        """The build job must retain its original validation, packaging, and upload steps."""
        build_job = self.workflow_data["jobs"]["build"]
        step_names = [s.get("name") for s in build_job["steps"] if "name" in s]
        self.assertIn("Install validation dependencies", step_names)
        self.assertIn("Validate source and fixtures", step_names)
        self.assertIn("Package release assets", step_names)
        self.assertIn("Verify release manifest and checksums", step_names)

    def test_release_job_contains_verification_step(self):
        """The release job must include a verification step before publication."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        step_names = [s.get("name", "") for s in release_steps]
        self.assertTrue(
            any(
                "verify" in name.lower() and "prerequisite" in name.lower()
                for name in step_names
            ),
            f"Expected verification step in release job steps: {step_names}",
        )

    def test_attest_provenance_step_pins_valid_v2_2_3_sha_and_permissions(self):
        """The release job must pin actions/attest-build-provenance to v2.2.3 SHA and retain SLSA permissions."""
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
        self.assertEqual(
            attest_step.get("uses"),
            "actions/attest-build-provenance@c074443f1aee8d4aeeae555aebba3282517141b2",
        )
        self.assertEqual(
            attest_step.get("with", {}).get("subject-path"),
            "dist/release/*",
        )

    def test_release_step_enforces_draft_flag(self):
        """The release step must enforce --draft, --verify-tag, --target, and --clobber."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        run_script = publish_step["run"]
        self.assertIn("gh release create", run_script)
        self.assertIn("--draft", run_script)
        self.assertIn("--verify-tag", run_script)
        self.assertIn("--target", run_script)
        self.assertIn("--clobber", run_script)

    def _run_preflight_script(self, cwd: Path, env_vars: dict[str, str]):
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

    def _run_verification_script(self, cwd: Path, env_vars: dict[str, str]):
        """Helper to run the release verification script under controlled workspace and environment."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        verify_step = next(
            s for s in release_steps if "verify" in s.get("name", "").lower()
        )
        return subprocess.run(
            ["sh", "-c", verify_step["run"]],
            cwd=str(cwd),
            env=_isolated_env(cwd, env_vars),
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_publish_script(self, cwd: Path, env_vars: dict[str, str]):
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
        license_text: str | None = "Synthetic License\n",
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
        return origin, workspace, tag_sha

    def test_verification_fails_when_license_missing(self):
        """Verification must abort immediately with non-zero exit when LICENSE is absent."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.2.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("LICENSE", result.stderr)

    def test_verification_fails_when_license_empty(self):
        """Verification must abort immediately with non-zero exit when LICENSE is empty or whitespace."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            _write_lf(tmp / "LICENSE", "   \n\n  ")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.2.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("LICENSE", result.stderr)

    def test_verification_fails_when_git_tag_mismatches_version(self):
        """Verification must abort immediately when git tag does not match v$(cat VERSION)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            _write_lf(tmp / "LICENSE", "Synthetic License\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.1.0", "INPUT_TAG": ""},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)

    def test_verification_fails_when_input_tag_mismatches_version(self):
        """Verification must abort immediately when dispatch input tag does not match v$(cat VERSION)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            _write_lf(tmp / "LICENSE", "Synthetic License\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "branch", "REF_NAME": "main", "INPUT_TAG": "v0.1.0"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)

    def test_verification_succeeds_with_license_and_matching_tag(self):
        """Defense-in-depth verification succeeds when LICENSE, VERSION, and HEAD match target_sha."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, workspace, tag_sha = self._setup_origin_and_clone(Path(tmp_dir))
            result = self._run_verification_script(
                workspace,
                {
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                    "INPUT_TAG": "",
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": tag_sha,
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_verification_fails_when_head_diverges_from_target_sha(self):
        """Defense-in-depth verification fails when checked-out HEAD does not equal target_sha."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, workspace, _ = self._setup_origin_and_clone(Path(tmp_dir))
            result = self._run_verification_script(
                workspace,
                {
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "v0.2.0",
                    "RELEASE_TAG": "v0.2.0",
                    "TARGET_SHA": "0" * 40,
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target_sha", result.stderr)

    def test_preflight_manual_dispatch_publish_false_skips_license_and_tag_checks(self):
        """Manual workflow_dispatch with publish=false succeeds without LICENSE or remote tags."""
        with tempfile.TemporaryDirectory() as tmp_dir:
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
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertEqual(
                outputs,
                {
                    "publish_enabled": "false",
                    "release_tag": "",
                    "target_sha": head_sha,
                },
            )

    def test_preflight_publish_fails_when_remote_tag_missing_even_if_local_tag_exists(
        self,
    ):
        """Publish preflight must query origin and reject branches or local-only tags."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, workspace, _ = self._setup_origin_and_clone(
                Path(tmp_dir),
                remote_tag=None,
            )
            _git(workspace, "tag", "v0.2.0")
            proc, _ = self._run_preflight_script(
                workspace,
                {
                    "EVENT_NAME": "workflow_dispatch",
                    "INPUT_PUBLISH": "true",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("origin", proc.stderr)

    def test_preflight_publish_rejects_invalid_or_injected_tag_syntax(self):
        """Publish preflight must validate tag syntax via env vars without shell injection."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            _, workspace, _ = self._setup_origin_and_clone(Path(tmp_dir))
            marker = workspace / "injected.txt"
            bad_tags = [
                "0.2.0",
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

    def test_preflight_publish_verifies_license_and_version_at_target_sha(self):
        """Publish preflight inspects LICENSE and VERSION at target_sha rather than branch HEAD."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace_no_lic, _ = self._setup_origin_and_clone(
                base / "missing-license",
                license_text=None,
                remote_tag="v0.2.0",
            )
            # Even if main later commits LICENSE, v0.2.0 at target_sha lacks LICENSE and must fail.
            _write_lf(workspace_no_lic / "LICENSE", "Added Later On Main\n")
            _git(workspace_no_lic, "add", "LICENSE")
            _git(workspace_no_lic, "commit", "-m", "Add license on main after tag")
            proc_missing, _ = self._run_preflight_script(
                workspace_no_lic,
                {
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
                license_text="Synthetic License\n",
                remote_tag="v0.1.0",
            )
            proc_mismatch, _ = self._run_preflight_script(
                workspace_mismatch,
                {
                    "EVENT_NAME": "push",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "v0.1.0",
                    "REF_TYPE": "tag",
                },
            )
            self.assertNotEqual(proc_mismatch.returncode, 0)
            self.assertIn("does not match", proc_mismatch.stderr)

    def test_preflight_publish_resolves_annotated_and_lightweight_tag_commit_sha(self):
        """Publish preflight peels annotated and lightweight tags to target_sha even after main advances."""
        for annotated in (True, False):
            with self.subTest(annotated=annotated), tempfile.TemporaryDirectory() as td:
                base = Path(td)
                origin, _, tag_sha = self._setup_origin_and_clone(
                    base,
                    version_text="0.2.0\n",
                    license_text="Synthetic License\n",
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

                proc_dispatch, out_dispatch = self._run_preflight_script(
                    fresh_checkout,
                    {
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
                        "publish_enabled": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": tag_sha,
                    },
                )

                proc_push, out_push = self._run_preflight_script(
                    fresh_checkout,
                    {
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
                        "publish_enabled": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": tag_sha,
                    },
                )

    def test_publish_step_idempotent_draft_update_and_published_rejection(self):
        """Publish GitHub Release creates one draft, updates assets with --clobber on rerun, and rejects published releases."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_lf(tmp / "VERSION", "0.2.0\n")
            dist_dir = tmp / "dist" / "release"
            dist_dir.mkdir(parents=True)
            _write_lf(dist_dir / "checksums.sha256", "abc123  pack.tar.gz\n")
            _write_lf(dist_dir / "pack.tar.gz", "archive-v1")

            bin_dir = tmp / "fake-bin"
            bin_dir.mkdir()
            fake_gh_py = bin_dir / "fake_gh.py"
            _write_lf(fake_gh_py, _FAKE_GH_PY)
            fake_gh_sh = bin_dir / "gh"
            _write_lf(
                fake_gh_sh,
                '#!/bin/sh\nexec "$FAKE_GH_PYTHON" "$FAKE_GH_SCRIPT" "$@"\n',
            )
            fake_gh_sh.chmod(0o755)

            state_path = tmp / "gh-state.json"
            _write_lf(state_path, json.dumps({"releases": []}))

            target_sha = "a" * 40
            base_env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_GH_PYTHON": sys.executable,
                "FAKE_GH_SCRIPT": str(fake_gh_py),
                "FAKE_GH_STATE": str(state_path),
                "RELEASE_TAG": "v0.2.0",
                "TARGET_SHA": target_sha,
                "INPUT_TAG": "",
                "REF_NAME": "v0.2.0",
                "REF_TYPE": "tag",
            }

            # Initial run creates a single draft release pinned to target_sha with --verify-tag.
            first = self._run_publish_script(tmp, base_env)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)
            created = state["releases"][0]
            self.assertEqual(created["tag"], "v0.2.0")
            self.assertTrue(created["draft"])
            self.assertTrue(created["verify_tag"])
            self.assertTrue(created["generate_notes"])
            self.assertEqual(created["target"], target_sha)
            self.assertEqual(created["assets"]["pack.tar.gz"], "archive-v1")

            # Rerun updates assets via --clobber without creating a second draft release.
            _write_lf(dist_dir / "pack.tar.gz", "archive-v2")
            second = self._run_publish_script(tmp, base_env)
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)
            self.assertTrue(state["releases"][0].get("last_upload_clobber"))
            self.assertEqual(
                state["releases"][0]["assets"]["pack.tar.gz"], "archive-v2"
            )

            # Published release must fail non-zero without overwriting assets.
            state["releases"][0]["draft"] = False
            _write_lf(state_path, json.dumps(state))
            _write_lf(dist_dir / "pack.tar.gz", "archive-v3")
            third = self._run_publish_script(tmp, base_env)
            self.assertNotEqual(third.returncode, 0)
            self.assertIn("already published", third.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["releases"][0]["assets"]["pack.tar.gz"], "archive-v2"
            )

            # Unexpected gh release view errors must fail rather than creating duplicate drafts.
            state["releases"][0]["draft"] = True
            state["view_error"] = "HTTP 502 Bad Gateway"
            _write_lf(state_path, json.dumps(state))
            fourth = self._run_publish_script(tmp, base_env)
            self.assertNotEqual(fourth.returncode, 0)
            self.assertIn("502", fourth.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)

    def test_releasing_docs_match_workflow_inputs(self):
        """docs/releasing.md must document inputs.publish default false and inputs.tag."""
        doc_text = RELEASING_DOC.read_text(encoding="utf-8")
        self.assertIn("inputs.publish", doc_text)
        self.assertIn("`false` by default", doc_text)
        self.assertIn("inputs.tag", doc_text)
        self.assertIn("release-artifacts", doc_text)


class WorkflowActionPinVerificationTests(unittest.TestCase):
    """Test offline SHA/comment checks and mocked online git ls-remote verification."""

    def test_repository_workflows_pass_offline_check(self):
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

    def test_validate_workflow_runs_windows_offline_check_and_single_online_check(self):
        """validate.yml must run --check in Windows fallback and --online once in validate job."""
        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        steps = validate_data["jobs"]["validate"]["steps"]

        win_step = next(s for s in steps if "Windows fallback" in s.get("name", ""))
        self.assertIn(
            "python scripts/verify_workflow_actions.py --check",
            win_step["run"],
        )

        online_steps = [
            s
            for s in steps
            if "verify_workflow_actions.py --online" in s.get("run", "")
        ]
        self.assertEqual(1, len(online_steps))
        condition = online_steps[0].get("if", "")
        self.assertIn("matrix.os", condition)
        self.assertIn("matrix.python-version", condition)

    def test_offline_check_rejects_mutable_refs_uppercase_shas_and_missing_comments(
        self,
    ):
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

    def test_online_check_rejects_nonexistent_sha(self):
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
    ):
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
    ):
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

    def test_online_check_handles_timeout_cleanly(self):
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


if __name__ == "__main__":
    unittest.main()

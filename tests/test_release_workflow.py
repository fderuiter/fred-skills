"""Tests for automated main release workflow, action SHA pins, pre-release verification, and idempotency."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_workflow_actions
from fredlib import releases

RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"
CHECKOUT_SHA = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"


class ReleaseWorkflowTests(unittest.TestCase):
    """Verify release workflow triggers, detection, non-draft publication, notes, and idempotency."""

    workflow_text: str
    workflow_data: dict[str, Any]

    @classmethod
    def setUpClass(cls):
        cls.workflow_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        cls.workflow_data = yaml.safe_load(cls.workflow_text)

    def _triggers(self):
        return self.workflow_data.get("on") or self.workflow_data.get(True, {})

    def test_workflow_yaml_syntax_and_structure(self):
        """release.yml must parse as valid YAML with detect_release, build, and release jobs."""
        self.assertIn("jobs", self.workflow_data)
        self.assertIn("detect_release", self.workflow_data["jobs"])
        self.assertIn("build", self.workflow_data["jobs"])
        self.assertIn("release", self.workflow_data["jobs"])

    def test_workflow_triggers_include_main_tags_and_dispatch(self):
        """release.yml must trigger on main pushes, v* tags, and workflow_dispatch without cancelling runs."""
        triggers = self._triggers()
        self.assertIn("push", triggers)
        self.assertIn("main", triggers["push"].get("branches", []))
        self.assertIn("v*", triggers["push"].get("tags", []))
        self.assertIn("workflow_dispatch", triggers)
        self.assertIs(
            False,
            self.workflow_data["concurrency"]["cancel-in-progress"],
        )

    def test_detect_release_job_checks_live_releases_and_gates_jobs(self):
        """detect_release must pin actions/checkout, query gh release view, and gate build and release."""
        detect_job = self.workflow_data["jobs"]["detect_release"]
        checkout_uses = [
            step.get("uses", "") for step in detect_job["steps"] if "uses" in step
        ]
        self.assertIn(CHECKOUT_SHA, checkout_uses)
        detect_step = next(step for step in detect_job["steps"] if "run" in step)
        self.assertIn('gh release view "$EXPECTED_TAG"', detect_step["run"])
        self.assertNotIn("git diff HEAD~1", detect_step["run"])

        build_job = self.workflow_data["jobs"]["build"]
        self.assertEqual("detect_release", build_job.get("needs"))
        self.assertEqual(
            "needs.detect_release.outputs.should_publish == 'true'",
            build_job.get("if"),
        )

        release_job = self.workflow_data["jobs"]["release"]
        needs = release_job.get("needs")
        if isinstance(needs, str):
            needs = [needs]
        self.assertIn("detect_release", needs)
        self.assertIn("build", needs)
        self.assertEqual(
            "needs.detect_release.outputs.should_publish == 'true'",
            release_job.get("if"),
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

    def test_root_license_exists_and_manifest_reports_repository_license(self):
        """Repository root must commit a non-empty MIT LICENSE reflected in release manifests."""
        license_path = ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "Expected root LICENSE file")
        license_text = license_path.read_text(encoding="utf-8")
        self.assertTrue(license_text.strip())
        self.assertIn("MIT License", license_text)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = releases.build(Path(tmp_dir) / "pack", pack="starter", root=ROOT)
            self.assertEqual("See repository LICENSE", manifest["licensing"])

    def test_publish_step_omits_draft_flag_and_declares_notes_and_idempotency(self):
        """Publish GitHub Release must omit --draft, select release notes, check tags, and guard releases."""
        self.assertNotIn("--draft", self.workflow_text)
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        script = publish_step["run"]
        self.assertIn("git ls-remote --exit-code --tags origin", script)
        self.assertIn("gh release view", script)
        self.assertIn("gh release create", script)
        self.assertIn("gh release upload", script)
        self.assertIn("--clobber", script)
        self.assertIn('--notes-file "docs/release-notes-${VERSION}.md"', script)
        self.assertIn("--generate-notes", script)
        self.assertNotIn("--draft", script)

    def _write_mock_gh(self, bin_dir: Path, log_file: Path) -> None:
        """Create a mock gh executable recording invocations and simulating view/create/upload/api."""
        gh_path = bin_dir / "gh"
        gh_path.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{log_file}"\n'
            'if [ "$1" = "release" ] && [ "$2" = "view" ]; then\n'
            '  if [ "${MOCK_GH_VIEW_STATE:-missing}" = "missing" ]; then\n'
            '    echo "release not found" >&2\n'
            "    exit 1\n"
            '  elif [ "${MOCK_GH_VIEW_STATE}" = "draft" ]; then\n'
            '    echo \'{"isDraft": true, "apiUrl": "https://api.github.com/repos/fderuiter/fred-skills/releases/42"}\'\n'
            "    exit 0\n"
            '  elif [ "${MOCK_GH_VIEW_STATE}" = "published" ]; then\n'
            '    echo \'{"isDraft": false, "apiUrl": "https://api.github.com/repos/fderuiter/fred-skills/releases/42"}\'\n'
            "    exit 0\n"
            "  else\n"
            '    echo "HTTP 500: Internal Server Error" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)

    def _run_detect_script(self, cwd: Path, extra_env: dict[str, str]):
        detect_steps = self.workflow_data["jobs"]["detect_release"]["steps"]
        detect_step = next(s for s in detect_steps if "run" in s)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **extra_env}
        return subprocess.run(
            ["sh", "-c", detect_step["run"]],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _init_repo_with_origin(self, base: Path, version: str = "0.2.0"):
        origin = base / "origin.git"
        work = base / "work"
        subprocess.run(
            ["git", "init", "--bare", str(origin)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(work)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.name", "Test User"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.email", "test@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "remote", "add", "origin", str(origin)],
            check=True,
            capture_output=True,
        )
        (work / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (work / "LICENSE").write_text("MIT License\n", encoding="utf-8")
        (work / "dist/release").mkdir(parents=True)
        (work / "dist/release/manifest.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        subprocess.run(
            ["git", "-C", str(work), "add", "VERSION", "LICENSE"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "Initial commit"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "push", "-u", "origin", "main"],
            check=True,
            capture_output=True,
        )
        return origin, work

    def _run_publish_script(self, cwd: Path, extra_env: dict[str, str]):
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **extra_env}
        return subprocess.run(
            ["sh", "-c", publish_step["run"]],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_detect_release_behaviour_across_missing_draft_and_published(self):
        """detect_release sets should_publish=true when missing or draft, and false when published."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            log_file = tmp / "gh.log"
            self._write_mock_gh(bin_dir, log_file)
            (tmp / "VERSION").write_text("0.2.0\n", encoding="utf-8")
            base_path = f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"

            for state, expected in [
                ("missing", "true"),
                ("draft", "true"),
                ("published", "false"),
            ]:
                out_file = tmp / f"out-{state}.txt"
                res = self._run_detect_script(
                    tmp,
                    {
                        "PATH": base_path,
                        "MOCK_GH_VIEW_STATE": state,
                        "GITHUB_OUTPUT": str(out_file),
                        "REF_TYPE": "branch",
                        "REF_NAME": "main",
                        "INPUT_TAG": "",
                    },
                )
                self.assertEqual(0, res.returncode, res.stderr)
                outputs = out_file.read_text(encoding="utf-8")
                self.assertIn(f"should_publish={expected}\n", outputs)
                self.assertIn("tag=v0.2.0\n", outputs)

    def test_publish_pushes_missing_tag_and_uses_curated_notes_file(self):
        """On main with missing release and docs/release-notes-0.2.0.md, pushes v0.2.0 tag and uses --notes-file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            origin, work = self._init_repo_with_origin(tmp, version="0.2.0")
            (work / "docs").mkdir()
            (work / "docs/release-notes-0.2.0.md").write_text(
                "# 0.2.0 notes\n", encoding="utf-8"
            )
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            log_file = tmp / "gh.log"
            self._write_mock_gh(bin_dir, log_file)

            res = self._run_publish_script(
                work,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "MOCK_GH_VIEW_STATE": "missing",
                    "GH_REPO": "fderuiter/fred-skills",
                    "GH_TOKEN": "synthetic-token",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(0, res.returncode, res.stderr)

            remote_tags = subprocess.run(
                ["git", "ls-remote", "--tags", str(origin), "refs/tags/v0.2.0"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("refs/tags/v0.2.0", remote_tags)

            calls = log_file.read_text(encoding="utf-8").splitlines()
            create_calls = [c for c in calls if c.startswith("release create ")]
            self.assertEqual(1, len(create_calls), f"Unexpected gh calls: {calls}")
            self.assertIn("--notes-file docs/release-notes-0.2.0.md", create_calls[0])
            self.assertNotIn("--generate-notes", create_calls[0])
            self.assertNotIn("--draft", create_calls[0])

    def test_publish_falls_back_to_generate_notes_when_notes_file_missing(self):
        """When docs/release-notes-<VERSION>.md is absent, gh release create uses --generate-notes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            origin, work = self._init_repo_with_origin(tmp, version="0.3.0")
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            log_file = tmp / "gh.log"
            self._write_mock_gh(bin_dir, log_file)

            res = self._run_publish_script(
                work,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "MOCK_GH_VIEW_STATE": "missing",
                    "GH_REPO": "fderuiter/fred-skills",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(0, res.returncode, res.stderr)

            remote_tags = subprocess.run(
                ["git", "ls-remote", "--tags", str(origin), "refs/tags/v0.3.0"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("refs/tags/v0.3.0", remote_tags)

            calls = log_file.read_text(encoding="utf-8").splitlines()
            create_calls = [c for c in calls if c.startswith("release create ")]
            self.assertEqual(1, len(create_calls), f"Unexpected gh calls: {calls}")
            self.assertIn("--generate-notes", create_calls[0])
            self.assertNotIn("--notes-file", create_calls[0])
            self.assertNotIn("--draft", create_calls[0])

    def test_publish_promotes_draft_and_skips_already_published_idempotently(self):
        """Existing drafts are promoted with gh release upload --clobber; published releases exit 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _, work = self._init_repo_with_origin(tmp, version="0.2.0")
            # Pre-push v0.2.0 to origin to verify existing remote tags do not fail
            subprocess.run(
                ["git", "-C", str(work), "tag", "v0.2.0", "HEAD"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(work), "push", "origin", "refs/tags/v0.2.0"],
                check=True,
                capture_output=True,
            )
            (work / "docs").mkdir()
            (work / "docs/release-notes-0.2.0.md").write_text(
                "# 0.2.0 notes\n", encoding="utf-8"
            )

            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            draft_log = tmp / "gh-draft.log"
            self._write_mock_gh(bin_dir, draft_log)

            res_draft = self._run_publish_script(
                work,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "MOCK_GH_VIEW_STATE": "draft",
                    "GH_REPO": "fderuiter/fred-skills",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(0, res_draft.returncode, res_draft.stderr)
            draft_calls = draft_log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(
                any(
                    c.startswith("release upload v0.2.0 ") and "--clobber" in c
                    for c in draft_calls
                ),
                f"Expected gh release upload --clobber in {draft_calls}",
            )
            self.assertTrue(
                any(
                    c.startswith("api --method PATCH ") and "draft=false" in c
                    for c in draft_calls
                ),
                f"Expected draft promotion PATCH in {draft_calls}",
            )
            self.assertFalse(any(c.startswith("release create ") for c in draft_calls))

            pub_log = tmp / "gh-published.log"
            self._write_mock_gh(bin_dir, pub_log)
            res_pub = self._run_publish_script(
                work,
                {
                    "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                    "MOCK_GH_VIEW_STATE": "published",
                    "GH_REPO": "fderuiter/fred-skills",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(0, res_pub.returncode, res_pub.stderr)
            pub_calls = pub_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(pub_calls), f"Expected only view call: {pub_calls}")
            self.assertTrue(pub_calls[0].startswith("release view v0.2.0"))

    def _run_verification_script(self, cwd, env_vars):
        """Helper to run the release verification script under controlled workspace and environment."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        verify_step = next(
            s for s in release_steps if "verify" in s.get("name", "").lower()
        )
        script = verify_step["run"]
        return subprocess.run(
            ["sh", "-c", script],
            cwd=str(cwd),
            env=env_vars,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_verification_fails_when_license_missing(self):
        """Verification must abort immediately with non-zero exit when LICENSE is absent."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "VERSION").write_text("0.2.0\n")
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
            (tmp / "VERSION").write_text("0.2.0\n")
            (tmp / "LICENSE").write_text("   \n\n  ")
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
            (tmp / "VERSION").write_text("0.2.0\n")
            (tmp / "LICENSE").write_text("Synthetic License\n")
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
            (tmp / "VERSION").write_text("0.2.0\n")
            (tmp / "LICENSE").write_text("Synthetic License\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "branch", "REF_NAME": "main", "INPUT_TAG": "v0.1.0"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)

    def test_verification_succeeds_with_license_and_matching_tag(self):
        """Verification succeeds when LICENSE exists and nonempty, and git tag matches VERSION."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "VERSION").write_text("0.2.0\n")
            (tmp / "LICENSE").write_text("Synthetic License\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "tag", "REF_NAME": "v0.2.0", "INPUT_TAG": ""},
            )
            self.assertEqual(result.returncode, 0)

    def test_verification_succeeds_with_default_dispatch_tag(self):
        """Verification succeeds when dispatched without tag and LICENSE exists and is nonempty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "VERSION").write_text("0.2.0\n")
            (tmp / "LICENSE").write_text("Synthetic License\n")
            result = self._run_verification_script(
                tmp,
                {"REF_TYPE": "branch", "REF_NAME": "main", "INPUT_TAG": ""},
            )
            self.assertEqual(result.returncode, 0)


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

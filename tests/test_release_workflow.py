"""Tests for release workflow structure, action SHA pins, and draft enforcement."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_workflow_actions

RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    """Verify release workflow preconditions, shell verification script, and draft enforcement."""

    @classmethod
    def setUpClass(cls):
        cls.workflow_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        cls.workflow_data = yaml.safe_load(cls.workflow_text)

    def test_workflow_yaml_syntax_and_structure(self):
        """release.yml must parse as valid YAML with build and release jobs."""
        self.assertIn("jobs", self.workflow_data)
        self.assertIn("build", self.workflow_data["jobs"])
        self.assertIn("release", self.workflow_data["jobs"])

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
        """The release step must pass the --draft flag to gh release create."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        self.assertIn("gh release create", publish_step["run"])
        self.assertIn("--draft", publish_step["run"])

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

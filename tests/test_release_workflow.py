"""Tests for release workflow structure, pre-release verification, and draft enforcement."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"


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


if __name__ == "__main__":
    unittest.main()

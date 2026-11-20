"""End-to-end integration tests for every operational recovery runbook in docs/runbooks.md."""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fredlib.common import atomic_json, digest, package_files, read_json
from verify_doc_snippets import extract_blocks_from_file


def _run_fred(
    *args: str,
    cwd: Path = ROOT,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute scripts/fred.py in a subprocess with bytecode generation disabled."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/fred.py"), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Execute a git command inside a temporary test repository."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Runbook Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "Runbook Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com",
        }
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _extract_scenario_c_python_script() -> str:
    """Extract and dedent the inline Python receipt reconstruction script from Runbook 3 Scenario C."""
    blocks = extract_blocks_from_file(
        ROOT / "docs/runbooks.md",
        start_line=408,
        end_line=450,
        root=ROOT,
    )
    if len(blocks) != 1 or not blocks[0].commands:
        raise AssertionError("Failed to locate Runbook 3 Scenario C Python block")
    _, cmd_str = blocks[0].commands[0]
    parts = shlex.split(cmd_str)
    if len(parts) < 3 or parts[1] != "-c":
        raise AssertionError(f"Unexpected Scenario C command tokens: {parts}")
    return textwrap.dedent(parts[2])


def _sample_valid_audit_records(
    file_path: str, symbol: str
) -> dict[str, list[dict[str, Any]]]:
    """Construct schema-valid contracts, evidence, and findings for audit.schema.json."""
    return {
        "contracts": [
            {
                "id": "C-1",
                "dimension": "correctness",
                "scope": file_path,
                "intended": f"{symbol} returns a non-null result dictionary to callers.",
                "observed": f"{symbol} returns an unchecked or null value.",
                "enforcement": "Unit tests and caller contract validation.",
                "evidence_ids": ["E-1"],
            }
        ],
        "evidence": [
            {
                "id": "E-1",
                "path": file_path,
                "line_start": 1,
                "line_end": 2,
                "symbol": symbol,
                "context": f"Function {symbol} implementation in {file_path}.",
            }
        ],
        "findings": [
            {
                "id": "F-1",
                "topic": "repo-audit-edge-cases",
                "tags": ["null-handling", "contract"],
                "title": f"Unchecked return contract in {symbol}",
                "status": "confirmed",
                "severity": "high",
                "confidence": "high",
                "contract_ids": ["C-1"],
                "evidence_ids": ["E-1"],
                "preconditions": f"Caller invokes {symbol} on standard input.",
                "sequence": [f"Invoke {symbol}()", "Inspect returned payload"],
                "expected": "Structured dictionary payload returned.",
                "actual": "Unexpected return value propagates to caller.",
                "impact": "Downstream callers fail when reading payload keys.",
                "root_cause": f"Missing structured return payload in {symbol}.",
                "counterevidence": "Checked wrapper modules; no fallback adapter exists.",
                "remedy": f"Return a validated dictionary from {symbol}.",
                "acceptance": [f"{symbol} returns dict and safe controls pass."],
                "validation": {
                    "state": "executed",
                    "commands": ["python -m unittest"],
                    "result": "Confirmed behavior in isolated unit check.",
                },
                "related_findings": [],
            }
        ],
    }


class RunbooksEndToEndIntegrationTests(unittest.TestCase):
    """Execute each documented recovery scenario in docs/runbooks.md against simulated failures."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_runbook_1_lock_contention_and_stale_lock_recovery(self) -> None:
        """Runbook 1: prepared/committed journals use recover-install; recovered/missing remove lock manually; installed --check detects locks."""
        # Pre-install workspace with a stale lock: `install --dry-run` exits 0, while `installed --check` exits 1
        fresh_locked = self.workspace / "fresh-locked"
        fresh_state = fresh_locked / ".fred-skills/claude-code-project"
        fresh_state.mkdir(parents=True)
        (fresh_state / "operation.lock").write_text("stale\n", encoding="utf-8")
        dry_install = _run_fred(
            "install",
            "--destination",
            str(fresh_locked),
            "--host",
            "claude-code",
            "--pack",
            "starter",
            "--dry-run",
        )
        self.assertEqual(0, dry_install.returncode, dry_install.stderr)
        fresh_check = _run_fred(
            "installed",
            "--destination",
            str(fresh_locked),
            "--host",
            "claude-code",
            "--check",
        )
        self.assertEqual(1, fresh_check.returncode)

        # Installed workspace lifecycle
        project = self.workspace / "project-rb1"
        project.mkdir()
        init_res = _run_fred(
            "install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--pack",
            "starter",
        )
        self.assertEqual(0, init_res.returncode, init_res.stderr)

        state_dir = project / ".fred-skills/claude-code-project"
        target_dir = project / ".claude/skills"
        lock_path = state_dir / "operation.lock"
        journal_path = state_dir / "operation.json"
        receipt_data = read_json(state_dir / "receipt.json")
        skill_name = min(receipt_data["skills"].keys())

        # Active/stale lock causes `installed --check` to exit 1 (`locked: true`, `healthy: false`)
        lock_path.write_text("stale-pid\n", encoding="utf-8")
        upd_dry = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--dry-run",
        )
        self.assertEqual(0, upd_dry.returncode, upd_dry.stderr)

        locked_check = _run_fred(
            "installed",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--check",
            "--json",
        )
        self.assertEqual(1, locked_check.returncode)
        locked_payload = json.loads(locked_check.stdout)
        self.assertTrue(locked_payload["locked"])
        self.assertFalse(locked_payload["healthy"])

        # Scenario A: "prepared" journal recovered via `recover-install --confirm-interrupted`
        op_id = "op-prepared-001"
        backup_root = state_dir / "backups" / op_id
        backup_skill_dir = backup_root / skill_name
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target_dir / skill_name, backup_skill_dir)

        skill_md = target_dir / skill_name / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8") + "\n<!-- interrupted swap -->\n",
            encoding="utf-8",
        )
        after_hash = digest(package_files(target_dir / skill_name))
        atomic_json(
            journal_path,
            {
                "id": op_id,
                "phase": "prepared",
                "action": "update",
                "host": "claude-code",
                "scope": "project",
                "target": str(target_dir),
                "skills": [skill_name],
                "before": copy.deepcopy(receipt_data["skills"]),
                "after_hashes": {skill_name: after_hash},
                "backups": str(backup_root),
            },
        )

        rec_a = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--confirm-interrupted",
        )
        self.assertEqual(0, rec_a.returncode, rec_a.stderr)
        rec_a_data = json.loads(rec_a.stdout)
        self.assertEqual("restored-before-state", rec_a_data["recovery"])
        self.assertTrue(Path(rec_a_data["preserved_interrupted_content"]).is_dir())
        self.assertFalse(lock_path.exists())
        self.assertEqual("recovered", read_json(journal_path)["phase"])

        check_after_a = _run_fred(
            "installed",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--check",
        )
        self.assertEqual(0, check_after_a.returncode, check_after_a.stderr)

        # Scenario C (case 1): lingering lock with "phase": "recovered" journal
        lock_path.write_text("lingering-after-recovery\n", encoding="utf-8")
        rec_on_recovered = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--confirm-interrupted",
        )
        self.assertEqual(1, rec_on_recovered.returncode)
        self.assertIn("Journal/receipt disagreement", rec_on_recovered.stderr)

        lock_path.unlink()
        self.assertTrue((state_dir / "backups").is_dir())
        self.assertEqual(
            0,
            _run_fred(
                "installed",
                "--destination",
                str(project),
                "--host",
                "claude-code",
                "--check",
            ).returncode,
        )

        # Scenario B: "committed" journal with lingering lock
        committed_id = receipt_data["history"][-1]["id"]
        atomic_json(
            journal_path,
            {
                "id": committed_id,
                "phase": "committed",
                "action": "install",
                "host": "claude-code",
                "scope": "project",
                "target": str(target_dir),
                "skills": list(receipt_data["skills"].keys()),
                "before": {},
                "after_hashes": {
                    k: v["content_hash"] for k, v in receipt_data["skills"].items()
                },
                "backups": str(state_dir / "backups" / committed_id),
            },
        )
        lock_path.write_text("lingering-committed\n", encoding="utf-8")
        rec_b = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--confirm-interrupted",
        )
        self.assertEqual(0, rec_b.returncode, rec_b.stderr)
        self.assertEqual("already-committed", json.loads(rec_b.stdout)["recovery"])
        self.assertFalse(lock_path.exists())
        self.assertEqual(
            0,
            _run_fred(
                "installed",
                "--destination",
                str(project),
                "--host",
                "claude-code",
                "--check",
            ).returncode,
        )

        # Scenario C (case 2): lingering lock when operation.json is missing
        journal_path.unlink()
        lock_path.write_text("aborted-before-journal\n", encoding="utf-8")
        rec_missing = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--confirm-interrupted",
        )
        self.assertEqual(1, rec_missing.returncode)
        self.assertIn("operation.json", rec_missing.stderr)

        lock_path.unlink()
        self.assertEqual(
            0,
            _run_fred(
                "installed",
                "--destination",
                str(project),
                "--host",
                "claude-code",
                "--check",
            ).returncode,
        )

    def test_runbook_2_interrupted_install_modified_destination_and_remove_power_loss(
        self,
    ) -> None:
        """Runbook 2 Scenarios A, B, and C recover interrupted swaps, modified targets, and interrupted removals."""
        project = self.workspace / "project-rb2"
        project.mkdir()
        self.assertEqual(
            0,
            _run_fred(
                "install",
                "--destination",
                str(project),
                "--host",
                "codex",
                "--pack",
                "starter",
            ).returncode,
        )

        state_dir = project / ".fred-skills/codex-project"
        target_dir = project / ".agents/skills"
        receipt_data = read_json(state_dir / "receipt.json")
        skill_name = min(receipt_data["skills"].keys())

        # Scenario B (superset of A): interrupted update + outside edit to target skill
        op_id = "op-rb2-mod"
        backup_root = state_dir / "backups" / op_id
        backup_dir = backup_root / skill_name
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target_dir / skill_name, backup_dir)
        (state_dir / "operation.lock").write_text("interrupted\n", encoding="utf-8")
        atomic_json(
            state_dir / "operation.json",
            {
                "id": op_id,
                "phase": "prepared",
                "action": "update",
                "host": "codex",
                "scope": "project",
                "target": str(target_dir),
                "skills": [skill_name],
                "before": copy.deepcopy(receipt_data["skills"]),
                "after_hashes": {skill_name: "0" * 64},
                "backups": str(backup_root),
            },
        )
        (target_dir / skill_name / "SKILL.md").write_text(
            "Uncommitted user customization\n", encoding="utf-8"
        )

        failed_rec = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "codex",
            "--confirm-interrupted",
        )
        self.assertEqual(1, failed_rec.returncode)
        self.assertIn("Modified recovery destination", failed_rec.stderr)

        # Follow Scenario B steps: save custom edits, restore from backup, rerun recover-install
        saved_custom = self.workspace / f"saved-{skill_name}-custom"
        shutil.copytree(target_dir / skill_name, saved_custom)
        shutil.rmtree(target_dir / skill_name)
        shutil.copytree(backup_dir, target_dir / skill_name)

        ok_rec = _run_fred(
            "recover-install",
            "--destination",
            str(project),
            "--host",
            "codex",
            "--confirm-interrupted",
        )
        self.assertEqual(0, ok_rec.returncode, ok_rec.stderr)
        self.assertTrue(saved_custom.is_dir())

        # Scenario C: power loss during `remove` after moving skill to backups/<uuid>/ before receipt commit
        rm_backup_dir = state_dir / "backups/powerloss-uuid" / skill_name
        rm_backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_dir / skill_name), str(rm_backup_dir))
        (state_dir / "operation.lock").write_text("powerloss\n", encoding="utf-8")

        missing_err = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "codex",
            "--dry-run",
        )
        self.assertEqual(1, missing_err.returncode)
        self.assertIn("Managed skill is missing", missing_err.stderr)

        # Restore skill from backup, clear stale lock, rerun `remove`, and verify with `update --dry-run`
        shutil.move(str(rm_backup_dir), str(target_dir / skill_name))
        (state_dir / "operation.lock").unlink()

        clean_rm = _run_fred(
            "remove",
            "--destination",
            str(project),
            "--host",
            "codex",
            "--skill",
            skill_name,
        )
        self.assertEqual(0, clean_rm.returncode, clean_rm.stderr)
        self.assertFalse((target_dir / skill_name).exists())

        verify_upd = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "codex",
            "--dry-run",
        )
        self.assertEqual(0, verify_upd.returncode, verify_upd.stderr)

    def test_runbook_3_drift_recovery_and_corrupt_or_missing_receipt_with_custom_skill(
        self,
    ) -> None:
        """Runbook 3 Scenarios A, B, and C preserve backups and custom folders while restoring update compatibility."""
        project = self.workspace / "project-rb3"
        project.mkdir()
        self.assertEqual(
            0,
            _run_fred(
                "install",
                "--destination",
                str(project),
                "--host",
                "claude-code",
                "--skill",
                "repo-audit-diagnostics",
                "--skill",
                "repo-verify-fix",
            ).returncode,
        )

        state_dir = project / ".fred-skills/claude-code-project"
        target_dir = project / ".claude/skills"
        receipt_path = state_dir / "receipt.json"

        # Scenario A: locally modified skill -> copy to <name>-custom (Route 2) + update --force (Route 1)
        mod_file = target_dir / "repo-verify-fix/SKILL.md"
        mod_file.write_text(
            mod_file.read_text(encoding="utf-8") + "\nLocal team note\n",
            encoding="utf-8",
        )
        blocked_upd = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--skill",
            "repo-verify-fix",
        )
        self.assertEqual(1, blocked_upd.returncode)
        self.assertIn("Locally modified skill", blocked_upd.stderr)

        custom_dir = target_dir / "repo-verify-fix-custom"
        shutil.copytree(target_dir / "repo-verify-fix", custom_dir)
        forced_upd = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--skill",
            "repo-verify-fix",
            "--force",
        )
        self.assertEqual(0, forced_upd.returncode, forced_upd.stderr)
        self.assertTrue(custom_dir.is_dir())
        self.assertTrue(any((state_dir / "backups").iterdir()))

        # Scenario B: missing managed skill -> restore via install --force
        shutil.rmtree(target_dir / "repo-audit-diagnostics")
        self.assertEqual(
            1,
            _run_fred(
                "installed",
                "--destination",
                str(project),
                "--host",
                "claude-code",
                "--check",
            ).returncode,
        )
        restored = _run_fred(
            "install",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--skill",
            "repo-audit-diagnostics",
            "--force",
        )
        self.assertEqual(0, restored.returncode, restored.stderr)

        # Scenario C: execute the documented Python reconstruction script from docs/runbooks.md
        raw_script = _extract_scenario_c_python_script()
        adapted_script = raw_script.replace(
            "/path/to/project", project.as_posix()
        ).replace("<host>", "claude-code")

        # Case 1: Corrupted receipt.json alongside untracked `repo-verify-fix-custom`
        receipt_path.write_text("{corrupt-json", encoding="utf-8")
        corrupt_bak = state_dir / "receipt.json.corrupt-bak"
        if receipt_path.is_file():
            shutil.copy2(receipt_path, corrupt_bak)

        rebuild_corrupt = subprocess.run(
            [sys.executable, "-c", adapted_script],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, rebuild_corrupt.returncode, rebuild_corrupt.stderr)
        self.assertTrue(corrupt_bak.is_file())
        rebuilt_data = read_json(receipt_path)
        self.assertIn("repo-audit-diagnostics", rebuilt_data["skills"])
        self.assertIn("repo-verify-fix", rebuilt_data["skills"])
        self.assertNotIn("repo-verify-fix-custom", rebuilt_data["skills"])

        # Case 2: Completely missing receipt.json alongside untracked `repo-verify-fix-custom`
        receipt_path.unlink()
        corrupt_bak.unlink()
        if receipt_path.is_file():
            shutil.copy2(receipt_path, corrupt_bak)
        self.assertFalse(corrupt_bak.exists())

        rebuild_missing = subprocess.run(
            [sys.executable, "-c", adapted_script],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, rebuild_missing.returncode, rebuild_missing.stderr)
        rebuilt_missing_data = read_json(receipt_path)
        self.assertEqual(
            {"repo-audit-diagnostics", "repo-verify-fix"},
            set(rebuilt_missing_data["skills"].keys()),
        )

        # Both `update --dry-run` (without --pack) and live `update` succeed while custom skill stays untracked
        upd_dry = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--dry-run",
        )
        self.assertEqual(0, upd_dry.returncode, upd_dry.stderr)

        upd_live = _run_fred(
            "update",
            "--destination",
            str(project),
            "--host",
            "claude-code",
        )
        self.assertEqual(0, upd_live.returncode, upd_live.stderr)

        check_json = _run_fred(
            "installed",
            "--destination",
            str(project),
            "--host",
            "claude-code",
            "--check",
            "--json",
        )
        self.assertEqual(0, check_json.returncode, check_json.stderr)
        installed_state = json.loads(check_json.stdout)
        self.assertTrue(installed_state["healthy"])
        self.assertIn("repo-verify-fix-custom", installed_state["untracked"])

    def test_runbook_4_git_revision_resolution_and_plan_json(self) -> None:
        """Runbook 4: missing/bad/non-commit --base fail cleanly; plan --json emits base_revision and changed_files."""
        repo = self.workspace / "repo-rb4"
        repo.mkdir()
        _run_git(repo, "init", "-b", "main")
        (repo / "app.py").write_text(
            "def calc(x):\n    return x + 1\n", encoding="utf-8"
        )
        _run_git(repo, "add", "app.py")
        _run_git(repo, "commit", "-m", "initial commit")
        base_sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
        blob_sha = _run_git(repo, "rev-parse", "HEAD:app.py").stdout.strip()

        _run_git(repo, "checkout", "-b", "feature/fix")
        (repo / "app.py").write_text(
            "def calc(x):\n    return x + 2\n", encoding="utf-8"
        )
        _run_git(repo, "add", "app.py")
        _run_git(repo, "commit", "-m", "update calc")

        # Scenario A: missing --base fails
        no_base = _run_fred(
            "plan", str(repo), "--task", "review-pr", "--mode", "changes"
        )
        self.assertEqual(1, no_base.returncode)
        self.assertIn("requires --base", no_base.stderr)

        # Scenario B: nonexistent ref fails
        bad_ref = _run_fred(
            "plan",
            str(repo),
            "--task",
            "review-pr",
            "--mode",
            "changes",
            "--base",
            "origin/nonexistent",
        )
        self.assertEqual(1, bad_ref.returncode)
        self.assertIn("Git could not resolve", bad_ref.stderr)

        # Scenario D: blob SHA (non-commit object) rejected by `<base>^{commit}` verification
        non_commit = _run_fred(
            "plan",
            str(repo),
            "--task",
            "review-pr",
            "--mode",
            "changes",
            "--base",
            blob_sha,
        )
        self.assertEqual(1, non_commit.returncode)
        self.assertIn("Git could not resolve", non_commit.stderr)

        # Verification: `plan ... --json` succeeds with `plan.base_revision` and `plan.changed_files`
        ok_plan = _run_fred(
            "plan",
            str(repo),
            "--task",
            "review-pr",
            "--mode",
            "changes",
            "--base",
            "main",
            "--json",
        )
        self.assertEqual(0, ok_plan.returncode, ok_plan.stderr)
        payload = json.loads(ok_plan.stdout)
        self.assertEqual(base_sha, payload["plan"]["base_revision"])
        self.assertNotIn("resolved_base", payload["plan"])
        self.assertEqual(["app.py"], payload["plan"]["changed_files"])

    def test_runbook_5_triage_discovery_redaction_tiers_and_key_aware_scrubbing(
        self,
    ) -> None:
        """Runbook 5: --workspace/--session-id discovery, --redaction-tier modes, and key-aware secret scrubbing."""
        repo = self.workspace / "repo-rb5"
        repo.mkdir()
        (repo / "service.py").write_text(
            "def handle():\n    return True\n", encoding="utf-8"
        )
        audit_ws = self.workspace / "audit-output/bug"

        init_res = _run_fred(
            "init",
            str(repo),
            "--task",
            "investigate-bug",
            "--output",
            str(audit_ws),
        )
        self.assertEqual(0, init_res.returncode, init_res.stderr)

        run_bytes_before = (audit_ws / "run.json").read_bytes()
        audit_bytes_before = (audit_ws / "audit.json").read_bytes()

        raw_key_secret = "unpatterned-custom-api-secret-value-987654"
        raw_openai_secret = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        transcript_events = [
            {
                "role": "tool",
                "name": "read_config",
                "content": json.dumps(
                    {
                        "api_key": raw_key_secret,
                        "Authorization": f"Bearer {raw_openai_secret}",
                        "benign_note": "turn-one-benign-tool-output",
                    }
                ),
            },
            {
                "role": "tool",
                "name": "bash",
                "is_error": True,
                "content": (
                    "Command 'pytest' timed out after 60.0 seconds\n"
                    f'{{"password": "{raw_key_secret}"}}'
                ),
            },
        ]
        transcript_path = audit_ws / "session-transcript.jsonl"
        transcript_path.write_text(
            "\n".join(json.dumps(ev) for ev in transcript_events) + "\n",
            encoding="utf-8",
        )

        fake_home = self.workspace / "fake-home"
        fake_home.mkdir()
        home_env = {"HOME": str(fake_home), "USERPROFILE": str(fake_home)}

        # Automatic discovery via `--workspace` across all 3 `--redaction-tier` choices
        bundles_by_tier: dict[str, dict[str, Any]] = {}
        for tier in ("skeleton", "evidence", "full"):
            out_dir = self.workspace / f"diag-{tier}"
            res = _run_fred(
                "triage",
                "--workspace",
                str(audit_ws),
                "--output",
                str(out_dir),
                "--redaction-tier",
                tier,
                "--check-schema",
                extra_env=home_env,
            )
            self.assertEqual(0, res.returncode, f"tier={tier} failed: {res.stderr}")
            bundle_file = out_dir / "diagnostic_bundle.json"
            report_file = out_dir / "diagnostic_report.md"
            self.assertTrue(bundle_file.is_file())
            self.assertTrue(report_file.is_file())

            raw_bundle_text = bundle_file.read_text(encoding="utf-8")
            raw_report_text = report_file.read_text(encoding="utf-8")
            for leaked in (raw_key_secret, raw_openai_secret):
                self.assertNotIn(leaked, raw_bundle_text)
                self.assertNotIn(leaked, raw_report_text)

            bundles_by_tier[tier] = json.loads(raw_bundle_text)
            self.assertEqual(
                "tool_timeout",
                bundles_by_tier[tier]["failure_classification"]["failure_type"],
            )

        # Verify tier-specific tool output retention
        self.assertEqual(
            2,
            bundles_by_tier["skeleton"]["redaction"]["tool_outputs_omitted"],
        )
        self.assertNotIn(
            "turn-one-benign-tool-output",
            bundles_by_tier["skeleton"]["scrubbed_transcript"],
        )
        self.assertEqual(
            1,
            bundles_by_tier["evidence"]["redaction"]["tool_outputs_omitted"],
        )
        self.assertNotIn(
            "turn-one-benign-tool-output",
            bundles_by_tier["evidence"]["scrubbed_transcript"],
        )
        self.assertIn(
            "timed out after 60.0 seconds",
            bundles_by_tier["evidence"]["scrubbed_transcript"],
        )
        self.assertEqual(
            0,
            bundles_by_tier["full"]["redaction"]["tool_outputs_omitted"],
        )
        self.assertIn(
            "turn-one-benign-tool-output",
            bundles_by_tier["full"]["scrubbed_transcript"],
        )

        # Automatic discovery via `--session-id` from host session store
        session_id = "sess-rb5-discovery-42"
        host_session_dir = fake_home / ".claude/projects/test-project"
        host_session_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(transcript_path, host_session_dir / f"{session_id}.jsonl")
        sid_out = self.workspace / "diag-session-id"
        sid_res = _run_fred(
            "triage",
            "--session-id",
            session_id,
            "--output",
            str(sid_out),
            "--redaction-tier",
            "skeleton",
            "--check-schema",
            extra_env=home_env,
        )
        self.assertEqual(0, sid_res.returncode, sid_res.stderr)
        self.assertTrue((sid_out / "diagnostic_bundle.json").is_file())

        # Canonical session ledgers remain strictly untouched
        self.assertEqual(run_bytes_before, (audit_ws / "run.json").read_bytes())
        self.assertEqual(audit_bytes_before, (audit_ws / "audit.json").read_bytes())

    def test_runbook_6_validate_run_drift_id_mismatch_and_schema_recovery(self) -> None:
        """Runbook 6 Scenarios A, B, and C detect and repair snapshot digest drift, ID mismatches, and schema errors."""
        repo = self.workspace / "repo-rb6"
        repo.mkdir()
        (repo / "worker.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        run_dir = self.workspace / "run-rb6"

        self.assertEqual(
            0,
            _run_fred(
                "init",
                str(repo),
                "--task",
                "investigate-bug",
                "--output",
                str(run_dir),
            ).returncode,
        )
        self.assertEqual(
            0, _run_fred("validate-run", str(run_dir), "--json").returncode
        )

        # Scenario A: Snapshot digest drift
        run_data = read_json(run_dir / "run.json")
        run_data["snapshot"]["exclude"].append("tmp/**")
        atomic_json(run_dir / "run.json", run_data)

        drift_res = _run_fred("validate-run", str(run_dir))
        self.assertEqual(1, drift_res.returncode)
        self.assertIn(
            "Snapshot content identity does not match its recorded file inventory",
            drift_res.stderr,
        )

        # Repair digest per Runbook 6 Scenario A
        snap = run_data["snapshot"]
        snap["digest"] = digest({k: snap[k] for k in ("files", "skipped", "exclude")})
        atomic_json(run_dir / "run.json", run_data)
        audit_data = read_json(run_dir / "audit.json")
        audit_data["repository"]["snapshot"] = snap["digest"]
        atomic_json(run_dir / "audit.json", audit_data)
        self.assertEqual(
            0, _run_fred("validate-run", str(run_dir), "--json").returncode
        )

        # Repository source edit handled via `resume`
        (repo / "worker.py").write_text("def work():\n    return 2\n", encoding="utf-8")
        resume_res = _run_fred("resume", str(run_dir), "--json")
        self.assertEqual(0, resume_res.returncode, resume_res.stderr)
        self.assertIn("worker.py", json.loads(resume_res.stdout)["changed_files"])

        # Scenario B: Run ID and snapshot mismatch
        audit_data["run_id"] = "run-wrong000000"
        audit_data["repository"]["snapshot"] = "f" * 64
        atomic_json(run_dir / "audit.json", audit_data)

        mismatch_res = _run_fred("validate-run", str(run_dir))
        self.assertEqual(1, mismatch_res.returncode)
        self.assertIn("Run and audit IDs differ", mismatch_res.stderr)
        self.assertIn(
            "Audit evidence is not tied to the current run snapshot",
            mismatch_res.stderr,
        )

        # Synchronize headers per Runbook 6 Scenario B
        audit_data["run_id"] = run_data["run_id"]
        audit_data["repository"]["snapshot"] = run_data["snapshot"]["digest"]
        atomic_json(run_dir / "audit.json", audit_data)
        self.assertEqual(
            0, _run_fred("validate-run", str(run_dir), "--json").returncode
        )

        # Scenario C: Ledger referential/schema error (finding cites missing evidence E-99)
        valid_records = _sample_valid_audit_records("worker.py", "work")
        broken_finding = copy.deepcopy(valid_records["findings"][0])
        broken_finding["evidence_ids"] = ["E-99"]
        audit_data["contracts"] = valid_records["contracts"]
        audit_data["evidence"] = valid_records["evidence"]
        audit_data["findings"] = [broken_finding]
        atomic_json(run_dir / "audit.json", audit_data)

        schema_fail = _run_fred("validate-run", str(run_dir))
        self.assertEqual(1, schema_fail.returncode)

        # Repair finding evidence reference and re-validate
        audit_data["findings"] = valid_records["findings"]
        atomic_json(run_dir / "audit.json", audit_data)
        valid_res = _run_fred("validate-run", str(run_dir), "--json")
        self.assertEqual(0, valid_res.returncode, valid_res.stderr)
        self.assertTrue(json.loads(valid_res.stdout)["valid"])

    def test_runbook_7_reconcile_states_verification_proof_and_ledger_recovery(
        self,
    ) -> None:
        """Runbook 7 Scenarios A, B, and C enforce finding IDs, verified-fixed proof schema, and remediation.json recovery."""
        repo = self.workspace / "repo-rb7"
        repo.mkdir()
        (repo / "handler.py").write_text(
            "def run():\n    return None\n", encoding="utf-8"
        )
        run_dir = self.workspace / "run-rb7"

        self.assertEqual(
            0,
            _run_fred(
                "init",
                str(repo),
                "--task",
                "investigate-bug",
                "--output",
                str(run_dir),
            ).returncode,
        )

        audit_data = read_json(run_dir / "audit.json")
        valid_records = _sample_valid_audit_records("handler.py", "run")
        audit_data.update(valid_records)
        atomic_json(run_dir / "audit.json", audit_data)

        # Scenario A: Unknown finding fails; valid finding + `in-progress` succeeds
        unknown_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-999",
            "--state",
            "in-progress",
            "--note",
            "Investigating",
        )
        self.assertEqual(1, unknown_res.returncode)
        self.assertIn("Unknown finding or remediation state", unknown_res.stderr)

        in_prog = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "in-progress",
            "--note",
            "Investigating root cause",
        )
        self.assertEqual(0, in_prog.returncode, in_prog.stderr)

        # Scenario B: `verified-fixed` rejected without --verification, with bad schema, or with false control boolean
        missing_ver = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "verified-fixed",
            "--note",
            "Attempt without proof",
        )
        self.assertEqual(1, missing_ver.returncode)
        self.assertIn(
            "verified-fixed requires a verification JSON artifact", missing_ver.stderr
        )

        bad_schema_path = run_dir / "bad_schema_verification.json"
        atomic_json(bad_schema_path, {"result": "missing required properties"})
        bad_schema_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "verified-fixed",
            "--verification",
            str(bad_schema_path),
            "--note",
            "Bad schema proof",
        )
        self.assertEqual(1, bad_schema_res.returncode)
        self.assertIn(
            "Verification artifact does not satisfy the proof schema",
            bad_schema_res.stderr,
        )

        incomplete_proof_path = run_dir / "incomplete_verification.json"
        atomic_json(
            incomplete_proof_path,
            {
                "before_revision": "1111111",
                "after_revision": "2222222",
                "original_reproduced": True,
                "regression_passed": True,
                "safe_controls_passed": False,
                "commands": ["python -m unittest"],
                "result": "Safe control failed",
            },
        )
        incomplete_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "verified-fixed",
            "--verification",
            str(incomplete_proof_path),
            "--note",
            "Incomplete proof",
        )
        self.assertEqual(1, incomplete_res.returncode)
        self.assertIn("safe controls", incomplete_res.stderr)

        # Record intermediate `fixed-pending-verification`, then complete `verification.json` and `verified-fixed`
        pending_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "fixed-pending-verification",
            "--note",
            "Patch applied; running verification",
        )
        self.assertEqual(0, pending_res.returncode, pending_res.stderr)

        valid_proof_path = run_dir / "verification.json"
        atomic_json(
            valid_proof_path,
            {
                "before_revision": "1111111",
                "after_revision": "2222222",
                "original_reproduced": True,
                "regression_passed": True,
                "safe_controls_passed": True,
                "commands": ["python -m unittest tests.test_handler"],
                "result": "Original failure reproduced before fix; regression and safe controls pass.",
            },
        )
        verified_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "verified-fixed",
            "--verification",
            str(valid_proof_path),
            "--note",
            "Verified fix with reproduction and safe controls",
        )
        self.assertEqual(0, verified_res.returncode, verified_res.stderr)

        bad_schema_path.unlink()
        incomplete_proof_path.unlink()
        self.assertEqual(
            0, _run_fred("validate-run", str(run_dir), "--json").returncode
        )

        # Scenario C: Malformed remediation.json backed up and repaired
        rem_path = run_dir / "remediation.json"
        good_rem = read_json(rem_path)
        atomic_json(rem_path, {"schema_version": "1.0", "records": "not-a-list"})

        malformed_res = _run_fred(
            "reconcile",
            str(run_dir),
            "--finding",
            "F-1",
            "--state",
            "verified-fixed",
            "--verification",
            str(valid_proof_path),
            "--note",
            "Retry against malformed ledger",
        )
        self.assertEqual(1, malformed_res.returncode)
        self.assertIn("Malformed remediation ledger", malformed_res.stderr)

        rem_bak = run_dir / "remediation.json.corrupt-bak"
        shutil.copy2(rem_path, rem_bak)
        atomic_json(rem_path, good_rem)
        self.assertTrue(rem_bak.is_file())
        final_check = _run_fred("validate-run", str(run_dir), "--json")
        self.assertEqual(0, final_check.returncode, final_check.stderr)


if __name__ == "__main__":
    unittest.main()

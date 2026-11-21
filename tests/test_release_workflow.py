"""Tests for release workflow preflight gating, automated main publishing, action SHA pins, and idempotency."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bump_version
import check_workflow_pins
import verify_workflow_actions
from fredlib import releases

RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
VALIDATE_WORKFLOW = ROOT / ".github/workflows/validate.yml"
ACTION_PINS_LOCKFILE = ROOT / ".github/action-pins.json"
RELEASING_DOC = ROOT / "docs/releasing.md"
CHECKOUT_SHA = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON_SHA = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
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
    import subprocess
    import sys
    from pathlib import Path

    state_path = Path(os.environ["FAKE_GH_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    args = sys.argv[1:]

    def _has_origin() -> bool:
        if not (Path.cwd() / ".git").exists():
            return False
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0

    def _origin_has_tag(tag_name: str) -> bool:
        res = subprocess.run(
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--refs",
                "origin",
                f"refs/tags/{tag_name}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0

    def _mint_origin_tag(tag_name: str, target_sha: str | None) -> None:
        if not _has_origin() or _origin_has_tag(tag_name):
            return
        ref_target = target_sha or "HEAD"
        res = subprocess.run(
            ["git", "push", "origin", f"{ref_target}:refs/tags/{tag_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            sys.stderr.write(f"failed to mint tag {tag_name}: {res.stderr}\\n")
            sys.exit(1)

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
        elif "--json" in args and "assets" in args:
            if state.get("view_assets_error"):
                sys.stderr.write(state["view_assets_error"] + "\\n")
                sys.exit(1)
            raw_assets = latest.get("assets", {})
            if isinstance(raw_assets, dict):
                asset_names = list(raw_assets.keys())
            else:
                asset_names = [
                    a["name"] if isinstance(a, dict) else str(a) for a in raw_assets
                ]
            if "--jq" in args or "-q" in args:
                for name in asset_names:
                    sys.stdout.write(f"{name}\\n")
            else:
                sys.stdout.write(
                    json.dumps({"assets": [{"name": n} for n in asset_names]}) + "\\n"
                )
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
        if verify_tag and _has_origin() and not _origin_has_tag(tag):
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(f"tag {tag} does not exist on origin (--verify-tag)\\n")
            sys.exit(1)
        if state.get("create_error"):
            if state.get("create_partial_draft_assets") is not None:
                state.setdefault("releases", []).append(
                    {
                        "tag": tag,
                        "draft": True,
                        "prerelease": prerelease,
                        "verify_tag": False,
                        "generate_notes": generate_notes,
                        "notes_file": notes_file,
                        "notes_body": notes_body,
                        "target": target,
                        "title": title,
                        "assets": dict(state["create_partial_draft_assets"]),
                    }
                )
            err_msg = state["create_error"]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(err_msg + "\\n")
            sys.exit(1)
        if not draft:
            _mint_origin_tag(tag, target)
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

    if subcmd == "delete-asset":
        if len(args) < 4:
            sys.stderr.write("delete-asset requires <tag> <asset-name>\\n")
            sys.exit(2)
        tag = args[2]
        asset_name = args[3]
        if "--yes" not in args[4:] and "-y" not in args[4:]:
            sys.stderr.write("delete-asset requires --yes in non-interactive mode\\n")
            sys.exit(2)
        if state.get("delete_asset_error"):
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(state["delete_asset_error"] + "\\n")
            sys.exit(1)
        matches = [r for r in state.get("releases", []) if r["tag"] == tag]
        if not matches:
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write("release not found\\n")
            sys.exit(1)
        rel = matches[-1]
        if asset_name not in rel.get("assets", {}):
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(f"asset not found: {asset_name}\\n")
            sys.exit(1)
        del rel["assets"][asset_name]
        rel.setdefault("deleted_assets", []).append(asset_name)
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
        verify_tag = False
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
                verify_tag = True
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
        rel["verify_tag"] = verify_tag
        if verify_tag and _has_origin() and not _origin_has_tag(tag):
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.stderr.write(f"tag {tag} does not exist on origin (--verify-tag)\\n")
            sys.exit(1)
        if not rel.get("draft", False):
            _mint_origin_tag(tag, rel.get("target"))
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
        self.assertIn("workflow_run", triggers)
        wf_run = triggers["workflow_run"]
        self.assertEqual(wf_run.get("workflows"), ["Validate skill packages"])
        self.assertEqual(wf_run.get("types"), ["completed"])
        self.assertEqual(wf_run.get("branches"), ["main"])
        self.assertIn("push", triggers)
        self.assertNotIn("branches", triggers["push"])
        self.assertIn("v*", triggers["push"].get("tags", []))
        self.assertIn("workflow_dispatch", triggers)

        dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
        self.assertIn("tag", dispatch_inputs)
        self.assertIn("publish", dispatch_inputs)
        self.assertEqual(dispatch_inputs["publish"]["type"], "boolean")
        self.assertIs(dispatch_inputs["publish"]["default"], False)

        jobs = self.workflow_data["jobs"]
        self.assertIn("prepare", jobs)
        self.assertIn("preflight", jobs)
        self.assertIn("validate", jobs)
        self.assertIn("build", jobs)
        self.assertIn("release", jobs)

        prepare = jobs["prepare"]
        self.assertEqual(prepare.get("permissions"), {"contents": "write"})
        self.assertEqual(
            prepare.get("concurrency"),
            {
                "group": "skills-release-prepare-main",
                "cancel-in-progress": False,
            },
        )
        self.assertIn("github.event_name == 'workflow_run'", str(prepare.get("if", "")))
        self.assertIn(
            "github.event.workflow_run.conclusion == 'success'",
            str(prepare.get("if", "")),
        )
        self.assertIn(
            "github.event.workflow_run.event == 'push'", str(prepare.get("if", ""))
        )
        self.assertIn(
            "github.event.workflow_run.head_branch == 'main'",
            str(prepare.get("if", "")),
        )
        self.assertIn(
            "github.actor != 'github-actions[bot]'", str(prepare.get("if", ""))
        )
        self.assertEqual(
            set(prepare.get("outputs", {}).keys()),
            {"bumped", "release_tag", "target_sha"},
        )
        prepare_checkout = prepare["steps"][0]
        self.assertEqual(prepare_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(prepare_checkout["with"].get("ref"), "refs/heads/main")
        self.assertEqual(prepare_checkout["with"].get("fetch-depth"), 0)
        self.assertIs(prepare_checkout["with"].get("persist-credentials"), False)
        prepare_py = prepare["steps"][1]
        self.assertEqual(prepare_py.get("uses"), SETUP_PYTHON_SHA)
        self.assertEqual(prepare_py["with"].get("python-version"), "3.12")

        preflight = jobs["preflight"]
        self.assertNotIn("concurrency", preflight)
        self.assertEqual(preflight.get("needs"), "prepare")
        self.assertIn("needs.prepare.result == 'success'", str(preflight.get("if", "")))
        self.assertIn("needs.prepare.result == 'skipped'", str(preflight.get("if", "")))
        self.assertIn(
            "github.event_name == 'workflow_run'", str(preflight.get("if", ""))
        )
        self.assertIn(
            "github.event.workflow_run.conclusion == 'success'",
            str(preflight.get("if", "")),
        )
        self.assertIn(
            "github.event.workflow_run.event == 'push'", str(preflight.get("if", ""))
        )
        self.assertIn(
            "github.event.workflow_run.head_branch == 'main'",
            str(preflight.get("if", "")),
        )
        self.assertEqual(preflight.get("permissions"), {"contents": "read"})
        self.assertEqual(
            set(preflight.get("outputs", {}).keys()),
            {
                "should_build",
                "should_publish",
                "needs_validation",
                "release_tag",
                "target_sha",
            },
        )
        preflight_checkout = preflight["steps"][0]
        self.assertEqual(preflight_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(
            preflight_checkout.get("with", {}).get("ref"),
            "${{ needs.prepare.outputs.target_sha || github.event.workflow_run.head_sha || github.sha }}",
        )
        self.assertIs(
            preflight_checkout.get("with", {}).get("persist-credentials"), False
        )

        expected_handoff_ref = "${{ needs.preflight.outputs.target_sha || needs.prepare.outputs.target_sha }}"

        validate_job = jobs["validate"]
        self.assertNotIn("concurrency", validate_job)
        self.assertEqual(validate_job.get("needs"), ["prepare", "preflight"])
        self.assertIn(
            "needs.preflight.outputs.should_build == 'true'",
            str(validate_job.get("if", "")),
        )
        self.assertIn(
            "needs.preflight.outputs.needs_validation != 'false'",
            str(validate_job.get("if", "")),
        )
        self.assertIn(
            "needs.preflight.result == 'success'", str(validate_job.get("if", ""))
        )
        self.assertEqual(validate_job.get("permissions"), {"contents": "read"})
        self.assertEqual(validate_job.get("uses"), "./.github/workflows/validate.yml")
        self.assertEqual(
            validate_job.get("with"),
            {"ref": expected_handoff_ref},
        )

        build = jobs["build"]
        self.assertNotIn("concurrency", build)
        self.assertEqual(build.get("needs"), ["prepare", "preflight", "validate"])
        self.assertIn(
            "needs.preflight.outputs.should_build == 'true'", str(build.get("if", ""))
        )
        self.assertIn("needs.validate.result == 'success'", str(build.get("if", "")))
        self.assertIn(
            "needs.validate.result == 'skipped' && needs.preflight.outputs.needs_validation == 'false'",
            str(build.get("if", "")),
        )
        self.assertEqual(build.get("permissions"), {"contents": "read"})
        build_checkout = build["steps"][0]
        self.assertEqual(build_checkout.get("uses"), CHECKOUT_SHA)
        self.assertEqual(build_checkout["with"]["ref"], expected_handoff_ref)
        self.assertIs(build_checkout["with"]["persist-credentials"], False)

        release = jobs["release"]
        self.assertEqual(
            release.get("needs"), ["prepare", "preflight", "validate", "build"]
        )
        self.assertIn(
            "needs.preflight.outputs.should_publish == 'true'",
            str(release.get("if", "")),
        )
        self.assertIn("needs.validate.result == 'success'", str(release.get("if", "")))
        self.assertIn(
            "needs.validate.result == 'skipped' && needs.preflight.outputs.needs_validation == 'false'",
            str(release.get("if", "")),
        )
        self.assertIn("needs.build.result == 'success'", str(release.get("if", "")))
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
        self.assertEqual(release_checkout["with"]["ref"], expected_handoff_ref)
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
        """The build job must package, verify checksums, and upload without rerunning make check or run_checks.py."""
        build_job = self.workflow_data["jobs"]["build"]
        step_names = [s.get("name") for s in build_job["steps"] if "name" in s]
        self.assertIn("Install validation dependencies", step_names)
        self.assertNotIn("Validate source and fixtures", step_names)
        self.assertIn("Package release assets", step_names)
        self.assertIn("Verify release manifest and checksums", step_names)
        run_cmds = "\n".join(s.get("run", "") for s in build_job["steps"])
        self.assertNotIn("make check", run_cmds)
        self.assertNotIn("run_checks.py", run_cmds)
        self.assertIn("scripts/fred.py release --all", run_cmds)
        self.assertIn("scripts/fred.py verify-release", run_cmds)
        self.assertIn("sha256sum -c SHA256SUMS", run_cmds)
        self.assertIn("sha256sum -c checksums.sha256", run_cmds)

    def test_release_job_verifies_prerequisites_and_checksums_before_attestation(
        self,
    ) -> None:
        """The release job must verify prerequisites, gate on release_state, and verify checksums before SLSA attestation."""
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        step_names = [s.get("name", "") for s in release_steps]
        prereq_idx = next(
            i
            for i, name in enumerate(step_names)
            if "verify" in name.lower() and "prerequisite" in name.lower()
        )
        state_idx = step_names.index("Check release publication state")
        download_idx = next(
            i
            for i, s in enumerate(release_steps)
            if str(s.get("uses", "")).startswith("actions/download-artifact@")
        )
        checksum_idx = step_names.index("Verify downloaded artifact checksums")
        attest_idx = step_names.index("Attest build provenance")
        publish_idx = step_names.index("Publish GitHub Release")
        self.assertLess(prereq_idx, state_idx)
        self.assertLess(state_idx, download_idx)
        self.assertLess(download_idx, checksum_idx)
        self.assertLess(checksum_idx, attest_idx)
        self.assertLess(attest_idx, publish_idx)

        self.assertEqual(release_steps[state_idx].get("id"), "release_state")
        expected_gate = "steps.release_state.outputs.should_publish == 'true'"
        for idx in (download_idx, checksum_idx, attest_idx, publish_idx):
            self.assertEqual(release_steps[idx].get("if"), expected_gate)

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
        self.assertIn("gh release delete-asset", script)
        self.assertIn("--yes", script)
        self.assertNotIn('git push origin "refs/tags/$TAG"', script)
        self.assertNotIn("--draft ", script)
        self.assertNotIn("--draft\n", script)

    def _run_shell_script(
        self,
        script: str,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as script_dir:
            script_path = Path(script_dir) / "workflow_step.sh"
            _write_lf(script_path, script)
            return subprocess.run(
                ["sh", script_path.as_posix()],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

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
        proc = self._run_shell_script(
            gate_step["run"],
            cwd=cwd,
            env=merged_env,
        )
        return proc, _parse_github_output(output_file)

    def _run_prepare_step(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        prepare_steps = self.workflow_data["jobs"]["prepare"]["steps"]
        bump_step = next(
            s
            for s in prepare_steps
            if s.get("name") == "Advance SemVer and push release commit"
        )
        output_file = cwd / ".test-prepare-output.txt"
        output_file.unlink(missing_ok=True)
        summary_file = cwd / ".test-prepare-summary.md"
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
            bump_step["run"],
            cwd=cwd,
            env=merged_env,
        )
        return proc, _parse_github_output(output_file)

    def _run_verification_script(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        verify_step = next(
            s for s in release_steps if s.get("name") == "Verify release prerequisites"
        )
        return self._run_shell_script(
            verify_step["run"],
            cwd=cwd,
            env=_isolated_env(cwd, env_vars),
        )

    def _run_release_state_script(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        state_step = next(s for s in release_steps if s.get("id") == "release_state")
        output_file = cwd / ".gh-release-state-output"
        output_file.unlink(missing_ok=True)
        summary_file = cwd / ".gh-release-state-summary.md"
        summary_file.unlink(missing_ok=True)
        merged_env = _isolated_env(
            cwd,
            {
                "GITHUB_OUTPUT": str(output_file),
                "GITHUB_STEP_SUMMARY": str(summary_file),
                **env_vars,
            },
        )
        proc = self._run_shell_script(
            state_step["run"],
            cwd=cwd,
            env=merged_env,
        )
        return proc, _parse_github_output(output_file)

    def _run_checksum_step(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        for repo_subdir in ("scripts", "catalog"):
            dest_link = cwd / repo_subdir
            if not dest_link.exists():
                dest_link.symlink_to(ROOT / repo_subdir, target_is_directory=True)
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        checksum_step = next(
            s
            for s in release_steps
            if s.get("name") == "Verify downloaded artifact checksums"
        )
        self.assertIn("scripts/fred.py verify-release", checksum_step["run"])
        self.assertNotIn("<<'PY'", checksum_step["run"])
        return self._run_shell_script(
            checksum_step["run"],
            cwd=cwd,
            env=_isolated_env(cwd, {"PYTHON_BIN": sys.executable}),
        )

    def _run_publish_script(
        self, cwd: Path, env_vars: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        release_steps = self.workflow_data["jobs"]["release"]["steps"]
        publish_step = next(
            s for s in release_steps if s.get("name") == "Publish GitHub Release"
        )
        return self._run_shell_script(
            publish_step["run"],
            cwd=cwd,
            env=_isolated_env(cwd, env_vars),
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
        return origin, workspace, tag_sha

    def test_verification_fails_when_license_missing_or_empty(self) -> None:
        """Verify release prerequisites aborts non-zero when LICENSE is absent or whitespace."""
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
                    "needs_validation": "true",
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
                    "needs_validation": "true",
                    "release_tag": "v0.2.0",
                    "target_sha": tagged_sha,
                },
            )

    def test_preflight_main_push_across_missing_draft_published_and_api_error(
        self,
    ) -> None:
        """On main pushes, preflight schedules build/publish when missing or draft, skips when published, and fails on API error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
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
                    "needs_validation": "true",
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
                    "needs_validation": "true",
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
            with self.subTest(annotated=annotated), tempfile.TemporaryDirectory() as td:
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
                        "needs_validation": "true",
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
                        "needs_validation": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": tag_sha,
                    },
                )

    def test_publish_pushes_missing_tag_on_main_and_selects_notes(self) -> None:
        """On main with missing tag, Publish GitHub Release mints v<VERSION> atomically via --target, retries failed creates cleanly, and selects notes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
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

            # 1. Failed gh release create does NOT strand refs/tags/v0.2.0 on origin, allowing clean retry.
            gh_env, state_path = _install_fake_gh(
                base / "curated",
                {"releases": [], "create_error": "HTTP 502 Bad Gateway"},
            )
            summary_file = base / "curated-summary.md"
            publish_env = {
                **gh_env,
                "GH_REPO": "fderuiter/fred-skills",
                "GH_TOKEN": "synthetic-token",
                "GITHUB_STEP_SUMMARY": str(summary_file),
                "RELEASE_TAG": "v0.2.0",
                "TARGET_SHA": head_sha,
                "INPUT_TAG": "",
                "REF_TYPE": "branch",
                "REF_NAME": "main",
            }
            failed_create = self._run_publish_script(workspace, publish_env)
            self.assertNotEqual(failed_create.returncode, 0)
            self.assertIn("502", failed_create.stderr)
            self.assertEqual(
                "",
                _git(
                    workspace,
                    "ls-remote",
                    "--refs",
                    origin.as_uri(),
                    "refs/tags/v0.2.0",
                ),
            )

            # Retry after transient failure mints refs/tags/v0.2.0 atomically without --verify-tag.
            _write_lf(state_path, json.dumps({"releases": []}))
            res = self._run_publish_script(workspace, publish_env)
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            remote_tags = _git(
                workspace, "ls-remote", "--refs", origin.as_uri(), "refs/tags/v0.2.0"
            )
            self.assertIn("refs/tags/v0.2.0", remote_tags)
            self.assertIn(head_sha, remote_tags)
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
            self.assertFalse(created["verify_tag"])
            self.assertFalse(created["generate_notes"])
            self.assertEqual(created["notes_file"], "docs/release-notes-0.2.0.md")
            self.assertEqual(created["target"], head_sha)

            # 2. Failed gh release create leaving an untagged partial draft cleans up stale partial assets and mints tag on retry.
            origin_retry, workspace_retry, retry_sha = self._setup_origin_and_clone(
                base / "partial-retry",
                version_text="0.2.1\n",
                license_text="MIT License\n",
                remote_tag=None,
            )
            dist_retry = workspace_retry / "dist" / "release"
            dist_retry.mkdir(parents=True)
            _write_lf(dist_retry / "pack.tar.gz", "verified-v0.2.1")
            gh_env_retry, state_retry_path = _install_fake_gh(
                base / "partial-retry",
                {
                    "releases": [],
                    "create_error": "asset upload interrupted",
                    "create_partial_draft_assets": {
                        "partial-corrupt.tar.gz": "corrupt",
                        "pack.tar.gz": "partial-old",
                    },
                },
            )
            retry_env = {
                **gh_env_retry,
                "GH_REPO": "fderuiter/fred-skills",
                "RELEASE_TAG": "v0.2.1",
                "TARGET_SHA": retry_sha,
                "INPUT_TAG": "",
                "REF_TYPE": "branch",
                "REF_NAME": "main",
            }
            interrupted = self._run_publish_script(workspace_retry, retry_env)
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertEqual(
                "",
                _git(
                    workspace_retry,
                    "ls-remote",
                    "--refs",
                    origin_retry.as_uri(),
                    "refs/tags/v0.2.1",
                ),
            )
            interrupted_state = json.loads(state_retry_path.read_text(encoding="utf-8"))
            interrupted_state.pop("create_error", None)
            interrupted_state.pop("create_partial_draft_assets", None)
            _write_lf(state_retry_path, json.dumps(interrupted_state))

            recovered = self._run_publish_script(workspace_retry, retry_env)
            self.assertEqual(recovered.returncode, 0, msg=recovered.stderr)
            self.assertIn(
                retry_sha,
                _git(
                    workspace_retry,
                    "ls-remote",
                    "--refs",
                    origin_retry.as_uri(),
                    "refs/tags/v0.2.1",
                ),
            )
            recovered_state = json.loads(state_retry_path.read_text(encoding="utf-8"))
            promoted_rel = recovered_state["releases"][0]
            self.assertFalse(promoted_rel["draft"])
            self.assertFalse(promoted_rel["verify_tag"])
            self.assertEqual(promoted_rel["deleted_assets"], ["partial-corrupt.tar.gz"])
            self.assertEqual(promoted_rel["assets"], {"pack.tar.gz": "verified-v0.2.1"})

            # 3. Without docs/release-notes-<VERSION>.md, falls back to --generate-notes; pre-release sets --prerelease; existing tag sets --verify-tag.
            origin_gen, workspace_gen, gen_sha = self._setup_origin_and_clone(
                base / "generated",
                version_text="0.3.0-rc.1\n",
                license_text="MIT License\n",
                remote_tag="v0.3.0-rc.1",
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
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.3.0-rc.1",
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
            self.assertTrue(state_gen["releases"][0]["verify_tag"])
            self.assertTrue(state_gen["releases"][0]["generate_notes"])
            self.assertIsNone(state_gen["releases"][0]["notes_file"])
            self.assertFalse(state_gen["releases"][0]["draft"])
            self.assertTrue(state_gen["releases"][0]["prerelease"])

    def test_publish_promotes_draft_skips_published_and_handles_errors(self) -> None:
        """Publish GitHub Release prunes stale draft assets, promotes drafts, rejects unsafe asset names, skips published, and fails on API errors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
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
                            "assets": {
                                "pack.tar.gz": "archive-v1",
                                "stale-unverified.tar.gz": "unattested-draft-file",
                            },
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

            # 1. Existing draft prunes unlisted stale assets, updates listed assets via --clobber, and promotes via gh release edit --draft=false --verify-tag.
            res_draft = self._run_publish_script(workspace, base_env)
            self.assertEqual(res_draft.returncode, 0, msg=res_draft.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)
            rel = state["releases"][0]
            self.assertFalse(rel["draft"])
            self.assertTrue(rel.get("verify_tag"))
            self.assertTrue(rel.get("last_upload_clobber"))
            self.assertEqual(rel.get("deleted_assets"), ["stale-unverified.tar.gz"])
            self.assertEqual(rel["assets"], {"pack.tar.gz": "archive-v2"})
            self.assertEqual(rel["notes_file"], "docs/release-notes-0.2.0.md")

            # 2. Subsequent run on already-published release exits 0 without overwriting assets.
            _write_lf(dist_dir / "pack.tar.gz", "archive-v3")
            res_pub = self._run_publish_script(workspace, base_env)
            self.assertEqual(res_pub.returncode, 0, msg=res_pub.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["releases"][0]["assets"], {"pack.tar.gz": "archive-v2"}
            )

            # 3. Draft asset names containing '/' or '..' are rejected before delete-asset or upload.
            for bad_asset_name in (
                "../evil.tar.gz",
                "nested/evil.tar.gz",
                "bad..name.tar.gz",
            ):
                with self.subTest(bad_asset_name=bad_asset_name):
                    _write_lf(
                        state_path,
                        json.dumps(
                            {
                                "releases": [
                                    {
                                        "tag": "v0.2.0",
                                        "draft": True,
                                        "assets": {bad_asset_name: "untrusted"},
                                    }
                                ]
                            }
                        ),
                    )
                    res_bad_asset = self._run_publish_script(workspace, base_env)
                    self.assertNotEqual(res_bad_asset.returncode, 0)
                    self.assertIn("Unsafe draft asset name", res_bad_asset.stderr)
                    bad_state = json.loads(state_path.read_text(encoding="utf-8"))
                    subcommands = [c[1] for c in bad_state.get("calls", [])]
                    self.assertNotIn("delete-asset", subcommands)
                    self.assertNotIn("upload", subcommands)
                    self.assertNotIn("edit", subcommands)

            # 4. Unexpected gh release view errors fail non-zero without creating duplicate releases.
            _write_lf(
                state_path,
                json.dumps(
                    {
                        "releases": [
                            {
                                "tag": "v0.2.0",
                                "draft": False,
                                "assets": {"pack.tar.gz": "archive-v2"},
                            }
                        ],
                        "view_error": "HTTP 502 Bad Gateway",
                    }
                ),
            )
            res_err = self._run_publish_script(workspace, base_env)
            self.assertNotEqual(res_err.returncode, 0)
            self.assertIn("502", res_err.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(state["releases"]), 1)

            # 5. Defense in depth: non-main ref with missing remote tag is rejected in Publish GitHub Release.
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

    def test_concurrent_main_pushes_bind_version_commit_and_gate_duplicate_signing(
        self,
    ) -> None:
        """Concurrent untagged main pushes bind target_sha to the first-parent VERSION commit and gate duplicate SLSA signing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            origin, _, v010_sha = self._setup_origin_and_clone(
                base,
                version_text="0.1.0\n",
                license_text="MIT License\n",
                remote_tag="v0.1.0",
            )
            seed = base / "seed"

            # Merge a PR branch with multiple commits changing VERSION so --first-parent binds to the main merge commit.
            _git(seed, "checkout", "-b", "feature/bump-020")
            _write_lf(seed / "VERSION", "0.2.0\n")
            _git(seed, "add", "VERSION")
            _git(seed, "commit", "-m", "PR commit 1: set VERSION 0.2.0")
            pr_inner_sha = _git(seed, "rev-parse", "HEAD")
            _write_lf(seed / "NOTES.txt", "pr follow-up\n")
            _git(seed, "add", "NOTES.txt")
            _git(seed, "commit", "-m", "PR commit 2: add notes")

            _git(seed, "checkout", "main")
            _git(
                seed,
                "merge",
                "--no-ff",
                "feature/bump-020",
                "-m",
                "Merge PR setting VERSION 0.2.0",
            )
            version_commit_sha = _git(seed, "rev-parse", "HEAD")
            self.assertNotEqual(pr_inner_sha, version_commit_sha)
            self.assertNotEqual(v010_sha, version_commit_sha)

            # Consecutive non-version pushes arriving seconds later on main before v0.2.0 is tagged.
            _write_lf(seed / "README.md", "# Docs update 1\n")
            _git(seed, "add", "README.md")
            _git(seed, "commit", "-m", "docs: update readme")
            push_2_sha = _git(seed, "rev-parse", "HEAD")

            _write_lf(seed / "README.md", "# Docs update 2\n")
            _git(seed, "add", "README.md")
            _git(seed, "commit", "-m", "docs: second update")
            push_3_sha = _git(seed, "rev-parse", "HEAD")
            _git(seed, "push", "origin", "HEAD:refs/heads/main")

            gh_env, state_path = _install_fake_gh(base, {"releases": []})

            # Shallow clones for all three concurrent main push runs resolve the exact same version_commit_sha.
            for idx, push_sha in enumerate(
                (version_commit_sha, push_2_sha, push_3_sha), start=1
            ):
                ws = base / f"shallow-run-{idx}"
                _git(
                    base,
                    "clone",
                    "--depth=1",
                    "--no-tags",
                    origin.as_uri(),
                    str(ws),
                )
                pf_proc, pf_out = self._run_preflight_script(
                    ws,
                    {
                        **gh_env,
                        "EVENT_NAME": "push",
                        "WORKFLOW_SHA": push_sha,
                        "INPUT_PUBLISH": "",
                        "INPUT_TAG": "",
                        "REF_NAME": "main",
                        "REF_TYPE": "branch",
                    },
                )
                self.assertEqual(pf_proc.returncode, 0, msg=pf_proc.stderr)
                self.assertEqual(
                    pf_out,
                    {
                        "should_build": "true",
                        "should_publish": "true",
                        "needs_validation": "true",
                        "release_tag": "v0.2.0",
                        "target_sha": version_commit_sha,
                    },
                )

            # Prepare release workspace checked out at version_commit_sha (matching jobs.release checkout).
            release_ws = base / "release-ws"
            _git(base, "clone", origin.as_uri(), str(release_ws))
            _git(release_ws, "checkout", "--detach", version_commit_sha)
            dist_dir = release_ws / "dist" / "release"
            dist_dir.mkdir(parents=True)
            _write_lf(dist_dir / "pack.tar.gz", "verified-archive-0.2.0")

            rel_env = {
                **gh_env,
                "EVENT_NAME": "push",
                "GH_REPO": "fderuiter/fred-skills",
                "GITHUB_SHA": push_2_sha,
                "INPUT_TAG": "",
                "REF_NAME": "main",
                "REF_TYPE": "branch",
                "RELEASE_TAG": "v0.2.0",
                "TARGET_SHA": version_commit_sha,
            }

            # Run 1 under release lock: prerequisites pass, release_state outputs should_publish=true, publish creates tag & release.
            prereq_1 = self._run_verification_script(release_ws, rel_env)
            self.assertEqual(prereq_1.returncode, 0, msg=prereq_1.stderr)
            state_1_proc, state_1_out = self._run_release_state_script(
                release_ws, rel_env
            )
            self.assertEqual(state_1_proc.returncode, 0, msg=state_1_proc.stderr)
            self.assertEqual(state_1_out, {"should_publish": "true"})

            pub_1 = self._run_publish_script(release_ws, rel_env)
            self.assertEqual(pub_1.returncode, 0, msg=pub_1.stderr)
            self.assertIn(
                version_commit_sha,
                _git(
                    release_ws,
                    "ls-remote",
                    "--refs",
                    origin.as_uri(),
                    "refs/tags/v0.2.0",
                ),
            )

            # Run 2 under release lock after Run 1 published: prerequisites pass and release_state gates off SLSA signing.
            prereq_2 = self._run_verification_script(release_ws, rel_env)
            self.assertEqual(prereq_2.returncode, 0, msg=prereq_2.stderr)
            state_2_proc, state_2_out = self._run_release_state_script(
                release_ws, rel_env
            )
            self.assertEqual(state_2_proc.returncode, 0, msg=state_2_proc.stderr)
            self.assertEqual(state_2_out, {"should_publish": "false"})

            # Even if an in-flight main push bound a different origin/main commit SHA than an already-published tag,
            # Verify release prerequisites allows it on main ONLY when isDraft=false and rejects drafts/5xx/tag/dispatch/unmerged.
            later_ws = base / "later-main-ws"
            _git(base, "clone", origin.as_uri(), str(later_ws))
            _git(later_ws, "checkout", "--detach", push_3_sha)
            mismatch_main_env = {
                **gh_env,
                "EVENT_NAME": "push",
                "GH_REPO": "fderuiter/fred-skills",
                "GITHUB_SHA": push_3_sha,
                "INPUT_TAG": "",
                "REF_NAME": "main",
                "REF_TYPE": "branch",
                "RELEASE_TAG": "v0.2.0",
                "TARGET_SHA": push_3_sha,
            }
            prereq_published_mismatch = self._run_verification_script(
                later_ws, mismatch_main_env
            )
            self.assertEqual(
                prereq_published_mismatch.returncode,
                0,
                msg=prereq_published_mismatch.stderr,
            )

            # v* tag push and workflow_dispatch still exit 1 on tag mismatch even when already published.
            for bad_trigger_env in (
                {
                    **mismatch_main_env,
                    "EVENT_NAME": "push",
                    "REF_TYPE": "tag",
                    "REF_NAME": "v0.2.0",
                },
                {
                    **mismatch_main_env,
                    "EVENT_NAME": "workflow_dispatch",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                },
            ):
                bad_trig = self._run_verification_script(later_ws, bad_trigger_env)
                self.assertNotEqual(bad_trig.returncode, 0)
                self.assertIn("expected", bad_trig.stderr)

            # Draft release or HTTP 5xx error on tag mismatch exits 1 even on main push.
            _write_lf(
                state_path,
                json.dumps({"releases": [{"tag": "v0.2.0", "draft": True}]}),
            )
            prereq_draft_mismatch = self._run_verification_script(
                later_ws, mismatch_main_env
            )
            self.assertNotEqual(prereq_draft_mismatch.returncode, 0)
            self.assertIn("expected", prereq_draft_mismatch.stderr)

            _write_lf(
                state_path,
                json.dumps(
                    {
                        "releases": [{"tag": "v0.2.0", "draft": False}],
                        "view_error": "HTTP 503 Service Unavailable",
                    }
                ),
            )
            prereq_503 = self._run_verification_script(later_ws, mismatch_main_env)
            self.assertNotEqual(prereq_503.returncode, 0)
            self.assertIn("503", prereq_503.stderr)
            state_503, _ = self._run_release_state_script(release_ws, rel_env)
            self.assertNotEqual(state_503.returncode, 0)
            self.assertIn("503", state_503.stderr)

            # Unmerged remote tag outside origin/main exits 1 even on main push when isDraft=false.
            _write_lf(
                state_path,
                json.dumps({"releases": [{"tag": "v0.2.0", "draft": False}]}),
            )
            _git(seed, "checkout", "-b", "feature/rogue-tag", v010_sha)
            _write_lf(seed / "VERSION", "0.2.0\n")
            _git(seed, "add", "VERSION")
            _git(seed, "commit", "-m", "Unmerged commit")
            _git(seed, "tag", "-f", "v0.2.0", "HEAD")
            _git(seed, "push", "--force", "origin", "refs/tags/v0.2.0")
            prereq_unmerged_tag = self._run_verification_script(
                later_ws, mismatch_main_env
            )
            self.assertNotEqual(prereq_unmerged_tag.returncode, 0)
            self.assertIn("expected", prereq_unmerged_tag.stderr)

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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
            self.assertIn("origin/main", preflight_tag.stderr)

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
            self.assertIn("origin/main", preflight_dispatch.stderr)

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
            self.assertIn("origin/main", prereq_unmerged.stderr)

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
        with tempfile.TemporaryDirectory() as tmp_dir:
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
        self.assertIn("scripts/bump_version.py", doc_text)
        self.assertIn("prepare", doc_text)
        self.assertIn("workflow_run", doc_text)
        self.assertIn("needs_validation", doc_text)
        self.assertIn("--first-parent", doc_text)
        self.assertIn("Check release publication state", doc_text)
        self.assertIn("gh release delete-asset", doc_text)

    def _create_bump_fixture_repo(self, base_dir: Path) -> tuple[Path, Path]:
        """Build a self-contained 2-skill git repository + bare origin for SemVer bump tests."""
        origin = base_dir / "origin.git"
        origin.mkdir(parents=True, exist_ok=True)
        _git(origin, "init", "--bare")

        repo = base_dir / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init")
        _git(repo, "remote", "add", "origin", origin.as_posix())

        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        for script_rel in (
            "scripts/bump_version.py",
            "scripts/skill_tools.py",
            "scripts/_markdown_utils.py",
            "scripts/_doc_invariants.py",
        ):
            _write_lf(
                repo / script_rel,
                (ROOT / script_rel).read_text(encoding="utf-8"),
            )
        (repo / "scripts/fredlib").mkdir(parents=True, exist_ok=True)
        _write_lf(repo / "scripts/fredlib/__init__.py", '"""Toolkit library."""\n')
        (repo / "shared/repo-audit").mkdir(parents=True, exist_ok=True)
        _write_lf(repo / "shared/repo-audit/manifest.json", '{"version": "1.0"}\n')
        _write_lf(repo / "LICENSE", "MIT License\n")
        _write_lf(repo / "VERSION", "0.2.0\n")

        for sname, sdesc in (
            ("skill-alpha", "Alpha audit skill."),
            ("skill-beta", "Beta workflow skill."),
        ):
            sdir = repo / "skills" / sname
            (sdir / ".cursor-plugin").mkdir(parents=True, exist_ok=True)
            _write_lf(
                sdir / "SKILL.md",
                f"---\nname: {sname}\ndescription: {sdesc}\n---\n\n# {sname}\n",
            )

        suites_payload = {
            "schema_version": "1.0",
            "suites": [
                {
                    "name": "repository-audits",
                    "phases": [{"id": 0, "title": "Core", "skills": ["skill-alpha"]}],
                },
                {
                    "name": "repository-workflows",
                    "phases": [{"id": 0, "title": "Fix", "skills": ["skill-beta"]}],
                },
            ],
        }
        skills_payload = {
            "schema_version": "1.0",
            "skills": [
                {"name": "skill-alpha", "description": "Alpha audit skill."},
                {"name": "skill-beta", "description": "Beta workflow skill."},
            ],
        }
        packs_payload = {
            "schema_version": "1.0",
            "packs": {
                "starter": {
                    "description": "Starter pack.",
                    "skills": ["skill-alpha"],
                },
                "complete": {
                    "description": "Complete pack.",
                    "skills": ["skill-alpha", "skill-beta"],
                },
            },
        }
        hosts_payload = {
            "schema_version": "1.0",
            "hosts": {"codex": {}, "claude": {}},
        }
        cases_payload = {"cases": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]}

        (repo / "catalog").mkdir(parents=True, exist_ok=True)
        (repo / "evals").mkdir(parents=True, exist_ok=True)
        (repo / "docs").mkdir(parents=True, exist_ok=True)
        (repo / "tests").mkdir(parents=True, exist_ok=True)
        _write_lf(
            repo / "catalog/suites.json", json.dumps(suites_payload, indent=2) + "\n"
        )
        _write_lf(
            repo / "catalog/skills.json", json.dumps(skills_payload, indent=2) + "\n"
        )
        _write_lf(
            repo / "catalog/packs.json", json.dumps(packs_payload, indent=2) + "\n"
        )
        _write_lf(
            repo / "catalog/hosts.json", json.dumps(hosts_payload, indent=2) + "\n"
        )
        _write_lf(repo / "evals/cases.json", json.dumps(cases_payload, indent=2) + "\n")

        _write_lf(
            repo / "README.md",
            "# Test\n"
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-0.2.0-starter.tar\n"
            "tar -xf fred-skills-0.2.0-starter.tar -C /path/to/project/.agents/skills/\n"
            "See the [0.2.0 release notes](docs/release-notes-0.2.0.md) and [releasing](docs/releasing.md).\n",
        )
        _write_lf(
            repo / "docs/releasing.md",
            "# Release process\n\n"
            "Publishes using `docs/release-notes-<VERSION>.md` when present "
            "(such as [release-notes-0.2.0.md](release-notes-0.2.0.md), or `--generate-notes` otherwise).\n",
        )
        _write_lf(
            repo / "docs/explorer.html",
            '<!doctype html><nav><a href="../README.md">Quickstart</a>'
            '<a href="release-notes-0.2.0.md">Release notes</a></nav>\n',
        )
        _write_lf(
            repo / "docs/usage.md",
            "# Usage\n"
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-0.2.0-complete.tar\n"
            "tar -xf fred-skills-0.2.0-complete.tar -C /path/to/project/.agents/skills/\n",
        )
        _write_lf(
            repo / "docs/release-notes-0.2.0.md",
            "# 0.2.0 — Initial snapshot\n\nTotal: 2 skills.\n",
        )
        for rel, content in bump_version.render_versioned_manifests(
            repo, "0.2.0"
        ).items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_lf(target, content)

        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "Release 0.2.0")
        _git(repo, "tag", "-a", "v0.2.0", "-m", "Release v0.2.0")
        _git(repo, "push", "-u", "origin", "HEAD:refs/heads/main")
        _git(repo, "push", "origin", "refs/tags/v0.2.0")
        return origin, repo

    def test_bump_version_minor_patch_skip_untagged_and_manifest_sync(self) -> None:
        """bump_version.py enforces minor/patch rules, skips non-distributable commits, syncs manifests, and restores tagged notes."""
        # Stdlib-only execution under python -S on the live repository
        stdlib_proc = subprocess.run(
            [
                sys.executable,
                "-S",
                str(ROOT / "scripts/bump_version.py"),
                "--dry-run",
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, stdlib_proc.returncode, stdlib_proc.stderr)
        parsed_live = json.loads(stdlib_proc.stdout)
        self.assertIn("bumped", parsed_live)

        with tempfile.TemporaryDirectory() as tmp_dir:
            _, repo = self._create_bump_fixture_repo(Path(tmp_dir))

            # 1. Commit modifying only tests/ and developer docs skips version bump
            _write_lf(repo / "tests/test_extra.py", "x = 1\n")
            _write_lf(
                repo / "docs/releasing.md",
                "# Release process\n\n"
                "Updated prose; publishes using `docs/release-notes-<VERSION>.md` when present "
                "(such as [release-notes-0.2.0.md](release-notes-0.2.0.md), or `--generate-notes` otherwise).\n",
            )
            _git(repo, "add", "tests/test_extra.py", "docs/releasing.md")
            _git(repo, "commit", "-m", "docs and tests only")

            skip_res = bump_version.bump_repository_version(repo)
            self.assertFalse(skip_res.bumped)
            self.assertEqual("0.2.0", skip_res.new_version)
            self.assertEqual("none", skip_res.bump_kind)

            # 2. Distributable edit without catalog membership change -> patch bump (0.2.0 -> 0.2.1)
            # Also corrupt docs/release-notes-0.2.0.md to verify automatic restoration from refs/tags/v0.2.0
            _write_lf(repo / "scripts/fredlib/__init__.py", '"""Updated toolkit."""\n')
            _write_lf(
                repo / "docs/release-notes-0.2.0.md",
                "# 0.2.0 — Rewritten by accident\n\nTotal: 99 skills.\n",
            )
            _git(
                repo,
                "add",
                "scripts/fredlib/__init__.py",
                "docs/release-notes-0.2.0.md",
            )
            _git(repo, "commit", "-m", "fix fredlib helper")

            patch_res = bump_version.bump_repository_version(repo)
            self.assertTrue(patch_res.bumped)
            self.assertEqual("patch", patch_res.bump_kind)
            self.assertEqual("0.2.1", patch_res.new_version)
            self.assertEqual(
                "0.2.1", (repo / "VERSION").read_text(encoding="utf-8").strip()
            )
            self.assertEqual(
                "# 0.2.0 — Initial snapshot\n\nTotal: 2 skills.\n",
                (repo / "docs/release-notes-0.2.0.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Total: 2 skills.",
                (repo / "docs/release-notes-0.2.1.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "v0.2.1/fred-skills-0.2.1-starter.tar",
                (repo / "README.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "fred-skills-0.2.1-complete.tar",
                (repo / "docs/usage.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "0.2.1",
                json.loads(
                    (repo / "gemini-extension.json").read_text(encoding="utf-8")
                )["version"],
            )

            # 3. Calling bump_version again before v0.2.1 is tagged is a clean no-op
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "chore(release): bump version to v0.2.1")
            untagged_res = bump_version.bump_repository_version(repo)
            self.assertFalse(untagged_res.bumped)
            self.assertEqual("0.2.1", untagged_res.new_version)

            # 4. Tag v0.2.1, then add a new skill and sync catalog/skills.json + docs/catalog.md -> minor bump (0.2.1 -> 0.3.0)
            import build_catalog

            _git(repo, "tag", "v0.2.1")
            sdir = repo / "skills/skill-gamma"
            (sdir / ".cursor-plugin").mkdir(parents=True, exist_ok=True)
            _write_lf(
                sdir / "SKILL.md",
                "---\nname: skill-gamma\ndescription: Gamma skill.\n---\n\n# skill-gamma\n",
            )
            suites_doc = json.loads(
                (repo / "catalog/suites.json").read_text(encoding="utf-8")
            )
            suites_doc["suites"][0]["phases"][0]["skills"].append("skill-gamma")
            _write_lf(
                repo / "catalog/suites.json", json.dumps(suites_doc, indent=2) + "\n"
            )
            packs_doc = json.loads(
                (repo / "catalog/packs.json").read_text(encoding="utf-8")
            )
            packs_doc["packs"]["complete"]["skills"].append("skill-gamma")
            _write_lf(
                repo / "catalog/packs.json", json.dumps(packs_doc, indent=2) + "\n"
            )
            build_catalog.build(root=repo)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "feat: add skill-gamma")

            minor_res = bump_version.bump_repository_version(repo)
            self.assertTrue(minor_res.bumped)
            self.assertEqual("minor", minor_res.bump_kind)
            self.assertEqual("0.3.0", minor_res.new_version)
            self.assertIn(
                "Total: 3 skills.",
                (repo / "docs/release-notes-0.3.0.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Total: 2 skills.",
                (repo / "docs/release-notes-0.2.1.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "[0.3.0 release notes](docs/release-notes-0.3.0.md)",
                (repo / "README.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'href="release-notes-0.3.0.md">Release notes</a>',
                (repo / "docs/explorer.html").read_text(encoding="utf-8"),
            )
            releasing_after_minor = (repo / "docs/releasing.md").read_text(
                encoding="utf-8"
            )
            for expected_note in (
                "release-notes-0.3.0.md",
                "release-notes-0.2.1.md",
                "release-notes-0.2.0.md",
            ):
                self.assertIn(
                    f"[{expected_note}]({expected_note})", releasing_after_minor
                )
            self.assertEqual([], build_catalog.build(root=repo, check=True))

    def test_bump_version_stdlib_isolation_and_clean_timeout_exit(self) -> None:
        """bump_version.py runs under python -S and returns exit code 1 without tracebacks on Git/subprocess timeouts."""
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp_dir:
            _, repo = self._create_bump_fixture_repo(Path(tmp_dir))
            stdlib_proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-S",
                    str(repo / "scripts/bump_version.py"),
                    "--root",
                    str(repo),
                    "--dry-run",
                    "--json",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, stdlib_proc.returncode, stdlib_proc.stderr)
            parsed = json.loads(stdlib_proc.stdout)
            self.assertFalse(parsed["bumped"])

            err_git = io.StringIO()
            with (
                mock.patch(
                    "skill_tools.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["git", "rev-parse"], timeout=30
                    ),
                ),
                contextlib.redirect_stderr(err_git),
            ):
                rc_git = bump_version.main(["--root", str(repo), "--dry-run"])
            self.assertEqual(1, rc_git)
            self.assertIn("Error [git_timeout]:", err_git.getvalue())
            self.assertIn("Hint:", err_git.getvalue())
            self.assertNotIn("Traceback", err_git.getvalue())

            err_raw = io.StringIO()
            with (
                mock.patch(
                    "bump_version.bump_repository_version",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["git", "diff"], timeout=30
                    ),
                ),
                contextlib.redirect_stderr(err_raw),
            ):
                rc_raw = bump_version.main(["--root", str(repo), "--dry-run"])
            self.assertEqual(1, rc_raw)
            self.assertIn("Error [subprocess_timeout]:", err_raw.getvalue())
            self.assertNotIn("Traceback", err_raw.getvalue())

    def test_prepare_job_shell_step_pushes_bump_commit_and_hands_off_target_sha(
        self,
    ) -> None:
        """jobs.prepare pushes a SemVer bump commit to origin/main, enforces actor guard, and hands target_sha to preflight."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            origin, repo = self._create_bump_fixture_repo(base)
            gh_env, _ = _install_fake_gh(
                base,
                {"releases": [{"tag": "v0.2.0", "draft": False}]},
            )

            # 1. Tests-only commit on main -> bumped=false, no extra commit pushed
            _write_lf(repo / "tests/test_noop.py", "a = 1\n")
            _git(repo, "add", "tests/test_noop.py")
            _git(repo, "commit", "-m", "test: add noop test")
            noop_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "push", "origin", "HEAD:refs/heads/main")

            prep_noop, out_noop = self._run_prepare_step(
                repo,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "GITHUB_ACTOR_NAME": "contributor",
                    "WORKFLOW_SHA": noop_sha,
                },
            )
            self.assertEqual(0, prep_noop.returncode, prep_noop.stderr)
            self.assertEqual("false", out_noop.get("bumped"))
            self.assertEqual(noop_sha, out_noop.get("target_sha"))

            # 2. Catalog membership change on main -> minor bump 0.2.0 -> 0.3.0 pushed to origin/main
            packs_doc = json.loads(
                (repo / "catalog/packs.json").read_text(encoding="utf-8")
            )
            packs_doc["packs"]["starter"]["skills"].append("skill-beta")
            _write_lf(
                repo / "catalog/packs.json", json.dumps(packs_doc, indent=2) + "\n"
            )
            _git(repo, "add", "catalog/packs.json")
            _git(repo, "commit", "-m", "feat(packs): expand starter pack")
            merge_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "push", "origin", "HEAD:refs/heads/main")

            prep_bump, out_bump = self._run_prepare_step(
                repo,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "GITHUB_ACTOR_NAME": "contributor",
                    "WORKFLOW_SHA": merge_sha,
                },
            )
            self.assertEqual(0, prep_bump.returncode, prep_bump.stderr)
            self.assertEqual("true", out_bump.get("bumped"))
            self.assertEqual("v0.3.0", out_bump.get("release_tag"))
            pushed_sha = out_bump.get("target_sha", "")
            self.assertNotEqual(merge_sha, pushed_sha)
            self.assertEqual(_git(origin, "rev-parse", "refs/heads/main"), pushed_sha)
            self.assertEqual(
                "github-actions[bot]",
                _git(repo, "log", "-1", "--format=%an", pushed_sha),
            )

            # 3. Hand pushed target_sha directly to preflight in the same workflow run -> schedules v0.3.0 build & publish
            pf_proc, pf_out = self._run_preflight_script(
                repo,
                {
                    **gh_env,
                    "EVENT_NAME": "push",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "WORKFLOW_SHA": pushed_sha,
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                },
            )
            self.assertEqual(0, pf_proc.returncode, pf_proc.stderr)
            self.assertEqual("true", pf_out.get("should_build"))
            self.assertEqual("true", pf_out.get("should_publish"))
            self.assertEqual("v0.3.0", pf_out.get("release_tag"))
            self.assertEqual(pushed_sha, pf_out.get("target_sha"))

            # 4. Commit-actor guard prevents recursive bumps when triggered by github-actions[bot] commit
            prep_guard, out_guard = self._run_prepare_step(
                repo,
                {
                    "EVENT_NAME": "push",
                    "GITHUB_REF_VALUE": "refs/heads/main",
                    "GITHUB_ACTOR_NAME": "contributor",
                    "WORKFLOW_SHA": pushed_sha,
                },
            )
            self.assertEqual(0, prep_guard.returncode, prep_guard.stderr)
            self.assertEqual("false", out_guard.get("bumped"))
            self.assertEqual(pushed_sha, out_guard.get("target_sha"))

    def test_workflow_run_chaining_needs_validation_and_tag_publication(self) -> None:
        """workflow_run gates main pushes, skips duplicate validate on matching SHA, validates older drafts, and pushes missing tags."""
        from types import SimpleNamespace

        def to_ns(val: Any) -> Any:
            if isinstance(val, dict):
                return SimpleNamespace(**{k: to_ns(v) for k, v in val.items()})
            return val

        def eval_if(
            expr: str,
            github_ctx: dict[str, Any],
            needs_ctx: dict[str, Any] | None = None,
            cancelled: bool = False,
        ) -> bool:
            cleaned = expr.strip()
            if cleaned.startswith("${{") and cleaned.endswith("}}"):
                cleaned = cleaned[3:-2].strip()
            py_expr = (
                cleaned.replace("!cancelled()", "not cancelled()")
                .replace("&&", " and ")
                .replace("||", " or ")
            )
            return bool(
                eval(
                    py_expr,
                    {"__builtins__": {}},
                    {
                        "github": to_ns(github_ctx),
                        "needs": to_ns(needs_ctx or {}),
                        "cancelled": lambda: cancelled,
                    },
                )
            )

        jobs = self.workflow_data["jobs"]
        valid_wf_run_ctx = {
            "event_name": "workflow_run",
            "actor": "maintainer",
            "ref": "refs/heads/main",
            "event": {
                "workflow_run": {
                    "conclusion": "success",
                    "event": "push",
                    "head_branch": "main",
                    "head_sha": "a" * 40,
                }
            },
        }
        self.assertTrue(eval_if(str(jobs["prepare"]["if"]), valid_wf_run_ctx))
        self.assertFalse(
            eval_if(
                str(jobs["prepare"]["if"]),
                {**valid_wf_run_ctx, "actor": "github-actions[bot]"},
            )
        )

        needs_prep_ok = {"prepare": {"result": "success"}}
        self.assertTrue(
            eval_if(str(jobs["preflight"]["if"]), valid_wf_run_ctx, needs_prep_ok)
        )
        for bad_wf_run in (
            {"conclusion": "failure", "event": "push", "head_branch": "main"},
            {"conclusion": "cancelled", "event": "push", "head_branch": "main"},
            {"conclusion": "success", "event": "pull_request", "head_branch": "main"},
            {"conclusion": "success", "event": "push", "head_branch": "feature"},
        ):
            bad_ctx = {
                **valid_wf_run_ctx,
                "event": {"workflow_run": bad_wf_run},
            }
            self.assertFalse(eval_if(str(jobs["prepare"]["if"]), bad_ctx))
            self.assertFalse(
                eval_if(
                    str(jobs["preflight"]["if"]),
                    bad_ctx,
                    {"prepare": {"result": "skipped"}},
                )
            )

        # When needs_validation == 'false' (validated workflow_run SHA == target_sha),
        # jobs.validate skips while jobs.build and jobs.release execute.
        needs_skip_val = {
            "prepare": {"result": "success"},
            "preflight": {
                "result": "success",
                "outputs": {
                    "should_build": "true",
                    "should_publish": "true",
                    "needs_validation": "false",
                },
            },
            "validate": {"result": "skipped"},
            "build": {"result": "success"},
        }
        self.assertFalse(
            eval_if(str(jobs["validate"]["if"]), valid_wf_run_ctx, needs_skip_val)
        )
        self.assertTrue(
            eval_if(str(jobs["build"]["if"]), valid_wf_run_ctx, needs_skip_val)
        )
        self.assertTrue(
            eval_if(str(jobs["release"]["if"]), valid_wf_run_ctx, needs_skip_val)
        )

        # When needs_validation == 'true' (tag push, dispatch, or older draft),
        # jobs.validate runs and a skipped validate blocks build/release.
        needs_req_val = {
            **needs_skip_val,
            "preflight": {
                "result": "success",
                "outputs": {
                    "should_build": "true",
                    "should_publish": "true",
                    "needs_validation": "true",
                },
            },
        }
        self.assertTrue(
            eval_if(str(jobs["validate"]["if"]), valid_wf_run_ctx, needs_req_val)
        )
        self.assertFalse(
            eval_if(str(jobs["build"]["if"]), valid_wf_run_ctx, needs_req_val)
        )
        self.assertFalse(
            eval_if(str(jobs["release"]["if"]), valid_wf_run_ctx, needs_req_val)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            origin, workspace, head_sha = self._setup_origin_and_clone(
                base / "wf-run",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag=None,
            )
            gh_env, state_path = _install_fake_gh(base / "wf-run", {"releases": []})

            # 1. workflow_run on main with unpublished VERSION and matching head_sha -> needs_validation=false
            pf_unpub, out_unpub = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_run",
                    "WORKFLOW_SHA": head_sha,
                    "WORKFLOW_RUN_HEAD_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(0, pf_unpub.returncode, pf_unpub.stderr)
            self.assertEqual(
                {
                    "should_build": "true",
                    "should_publish": "true",
                    "needs_validation": "false",
                    "release_tag": "v0.2.0",
                    "target_sha": head_sha,
                },
                out_unpub,
            )

            # 2. Verify release prerequisites and Publish GitHub Release allow tag creation under workflow_run
            prereq_wf = self._run_verification_script(
                workspace,
                {
                    "EVENT_NAME": "workflow_run",
                    "GITHUB_SHA": head_sha,
                    "TARGET_SHA": head_sha,
                    "RELEASE_TAG": "v0.2.0",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(0, prereq_wf.returncode, prereq_wf.stderr)

            dist_dir = workspace / "dist/release"
            dist_dir.mkdir(parents=True)
            _write_lf(dist_dir / "pack.tar.gz", "archive-wf-run")
            pub_wf = self._run_publish_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_run",
                    "GH_REPO": "fderuiter/fred-skills",
                    "GITHUB_SHA": head_sha,
                    "TARGET_SHA": head_sha,
                    "RELEASE_TAG": "v0.2.0",
                    "REF_TYPE": "branch",
                    "REF_NAME": "main",
                    "INPUT_TAG": "",
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
            )
            self.assertEqual(0, pub_wf.returncode, pub_wf.stderr)
            self.assertIn(
                "refs/tags/v0.2.0",
                _git(
                    workspace,
                    "ls-remote",
                    "--refs",
                    origin.as_uri(),
                    "refs/tags/v0.2.0",
                ),
            )

            # 3. Already published VERSION on workflow_run -> should_build=false, should_publish=false, needs_validation=false
            pf_pub, out_pub = self._run_preflight_script(
                workspace,
                {
                    **gh_env,
                    "EVENT_NAME": "workflow_run",
                    "WORKFLOW_SHA": head_sha,
                    "WORKFLOW_RUN_HEAD_SHA": head_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(0, pf_pub.returncode, pf_pub.stderr)
            self.assertEqual("false", out_pub["should_build"])
            self.assertEqual("false", out_pub["should_publish"])
            self.assertEqual("false", out_pub["needs_validation"])

            # 4. Older draft commit on origin/main where workflow_run.head_sha advanced -> needs_validation=true
            _, draft_ws, old_draft_sha = self._setup_origin_and_clone(
                base / "older-draft",
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag="v0.2.0",
            )
            draft_seed = base / "older-draft/seed"
            _write_lf(draft_seed / "README.md", "# Updated main\n")
            _git(draft_seed, "add", "README.md")
            _git(draft_seed, "commit", "-m", "docs: advance main after draft tag")
            newer_main_sha = _git(draft_seed, "rev-parse", "HEAD")
            _git(draft_seed, "push", "origin", "HEAD:refs/heads/main")
            gh_draft_env, _ = _install_fake_gh(
                base / "older-draft",
                {"releases": [{"tag": "v0.2.0", "draft": True}]},
            )
            pf_older, out_older = self._run_preflight_script(
                draft_ws,
                {
                    **gh_draft_env,
                    "EVENT_NAME": "workflow_run",
                    "WORKFLOW_SHA": newer_main_sha,
                    "WORKFLOW_RUN_HEAD_SHA": newer_main_sha,
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                    "INPUT_PUBLISH": "",
                    "INPUT_TAG": "",
                    "REF_NAME": "main",
                    "REF_TYPE": "branch",
                },
            )
            self.assertEqual(0, pf_older.returncode, pf_older.stderr)
            self.assertEqual("true", out_older["should_build"])
            self.assertEqual("true", out_older["should_publish"])
            self.assertEqual("true", out_older["needs_validation"])
            self.assertEqual(old_draft_sha, out_older["target_sha"])

            # 5. Defense-in-depth shell rejections for invalid workflow_run metadata
            for bad_env in (
                {
                    "WORKFLOW_CONCLUSION": "failure",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "pull_request",
                    "WORKFLOW_HEAD_BRANCH": "main",
                },
                {
                    "WORKFLOW_CONCLUSION": "success",
                    "WORKFLOW_EVENT": "push",
                    "WORKFLOW_HEAD_BRANCH": "feature",
                },
            ):
                bad_pf, _ = self._run_preflight_script(
                    workspace,
                    {
                        **gh_env,
                        "EVENT_NAME": "workflow_run",
                        "WORKFLOW_SHA": head_sha,
                        "WORKFLOW_RUN_HEAD_SHA": head_sha,
                        "REF_NAME": "main",
                        "REF_TYPE": "branch",
                        **bad_env,
                    },
                )
                self.assertNotEqual(0, bad_pf.returncode)
            self.assertTrue(state_path.is_file())

    def test_workflow_script_runners_stay_under_windows_cmdline_limit(self) -> None:
        """Workflow test helpers must execute shell steps via script files rather than sh -c exceeding Windows 8191-char limit."""
        windows_cmdline_max = 8191
        real_run = subprocess.run
        recorded_sh_cmdlines: list[str] = []

        def checked_run(
            cmd: Any, *args: Any, **kwargs: Any
        ) -> subprocess.CompletedProcess[Any]:
            if isinstance(cmd, list) and cmd and cmd[0] == "sh":
                win_cmdline = subprocess.list2cmdline(
                    [r"C:\Program Files\Git\bin\sh.exe", *cmd[1:]]
                )
                recorded_sh_cmdlines.append(win_cmdline)
                if len(win_cmdline) > windows_cmdline_max and cmd[1:2] == ["-c"]:
                    over = len(win_cmdline) - windows_cmdline_max
                    cmd = ["sh", "-c", cmd[2][:-over]]
            return real_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            _, workspace, head_sha = self._setup_origin_and_clone(
                base,
                version_text="0.2.0\n",
                license_text="MIT License\n",
                remote_tag="v0.2.0",
            )
            gh_env, _ = _install_fake_gh(base, {"releases": []})
            with mock.patch("subprocess.run", side_effect=checked_run):
                preflight_proc, preflight_out = self._run_preflight_script(
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
                verify_proc = self._run_verification_script(
                    workspace,
                    {
                        "EVENT_NAME": "push",
                        "GITHUB_SHA": head_sha,
                        "REF_TYPE": "tag",
                        "REF_NAME": "v0.2.0",
                        "INPUT_TAG": "",
                        "RELEASE_TAG": "v0.2.0",
                        "TARGET_SHA": head_sha,
                    },
                )

            self.assertEqual(preflight_proc.returncode, 0, msg=preflight_proc.stderr)
            self.assertEqual(preflight_out.get("should_publish"), "true")
            self.assertEqual(verify_proc.returncode, 0, msg=verify_proc.stderr)
            self.assertGreaterEqual(len(recorded_sh_cmdlines), 2)
            for win_cmdline in recorded_sh_cmdlines:
                self.assertLessEqual(len(win_cmdline), windows_cmdline_max)


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
        self.assertIn("github.event_name != 'workflow_call'", condition)
        self.assertIn("matrix.os", condition)
        self.assertIn("matrix.python-version", condition)
        self.assertIn("--changed-from", online_steps[0].get("run", ""))
        self.assertIn("BASE_REF", online_steps[0].get("env", {}))

        audit_wf_path = ROOT / ".github/workflows/audit-action-pins.yml"
        self.assertTrue(audit_wf_path.is_file())
        audit_data = yaml.safe_load(audit_wf_path.read_text(encoding="utf-8"))
        audit_triggers = audit_data.get("on") or audit_data.get(True) or {}
        self.assertIn("schedule", audit_triggers)
        self.assertIn("workflow_dispatch", audit_triggers)
        self.assertNotIn("pull_request", audit_triggers)
        self.assertNotIn("push", audit_triggers)
        self.assertEqual({"contents": "read"}, audit_data.get("permissions"))
        audit_steps = audit_data["jobs"]["audit-pins"]["steps"]
        audit_cmds = [s.get("run", "").strip() for s in audit_steps if "run" in s]
        self.assertIn(
            "python scripts/check_workflow_pins.py --verify-remote",
            audit_cmds,
        )

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
        """The committed .github/action-pins.json lockfile must record all nine repository actions with node24/composite runtimes and pass check_workflow_pins.py."""
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
                    "composite",
                ),
                "actions/cache": (
                    "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                    "v6",
                    "node24",
                ),
                "actions/checkout": (
                    "d23441a48e516b6c34aea4fa41551a30e30af803",
                    "v6",
                    "node24",
                ),
                "actions/configure-pages": (
                    "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
                    "v6.0.0",
                    "node24",
                ),
                "actions/deploy-pages": (
                    "368f82528645a54fb793d4d04e342629a3f51346",
                    "v5.0.1",
                    "node24",
                ),
                "actions/download-artifact": (
                    "9000827ccba6bdab643e8b6fd33ac0654aef8333",
                    "v8",
                    "node24",
                ),
                "actions/setup-python": (
                    "ece7cb06caefa5fff74198d8649806c4678c61a1",
                    "v6",
                    "node24",
                ),
                "actions/upload-artifact": (
                    "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
                    "v7",
                    "node24",
                ),
                "actions/upload-pages-artifact": (
                    "fc324d3547104276b827a68afc52ff2a11cc49c9",
                    "v5.0.0",
                    "composite",
                ),
            },
            {repo: (pin.sha, pin.version, pin.runtime) for repo, pin in locked.items()},
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

    def test_lockfile_and_remote_reject_deprecated_node_runtimes_and_verify_composite_steps(
        self,
    ) -> None:
        """Offline lockfile and --verify-remote reject node20/node16/node12 runtimes, including composite child steps."""
        workflow_yaml = """\
name: RuntimeCheck
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6
      - uses: actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            wf_dir = tmp_root / ".github/workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(workflow_yaml, encoding="utf-8")
            lock_path = tmp_root / ".github/action-pins.json"

            # 1. Offline lockfile rejects node20, node16, node12, and missing sibling runtime.
            for bad_runtime in ("node20", "node16", "node12"):
                lock_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "actions": {
                                "actions/cache": {
                                    "sha": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                                    "version": "v6",
                                    "runtime": bad_runtime,
                                },
                                "actions/attest-build-provenance": {
                                    "sha": "4d101475d8b20a2381f78447822ac1eab6504dd8",
                                    "version": "v4.2.2",
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                errs = check_workflow_pins.check_pins(root=tmp_root)
                self.assertTrue(
                    any(f"deprecated runtime '{bad_runtime}'" in e for e in errs),
                    errs,
                )
                self.assertTrue(
                    any("must specify 'runtime'" in e for e in errs),
                    errs,
                )

            # 2. Valid node24 + composite lockfile passes offline, then --verify-remote checks upstream action.yml + child steps.
            lock_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "actions": {
                            "actions/cache": {
                                "sha": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                                "version": "v6",
                                "runtime": "node24",
                            },
                            "actions/attest-build-provenance": {
                                "sha": "4d101475d8b20a2381f78447822ac1eab6504dd8",
                                "version": "v4.2.2",
                                "runtime": "composite",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual([], check_workflow_pins.check_pins(root=tmp_root))

            ls_remote_by_url = {
                "https://github.com/actions/cache.git": (
                    "55cc8345863c7cc4c66a329aec7e433d2d1c52a9\trefs/tags/v6.1.0\n"
                ),
                "https://github.com/actions/attest-build-provenance.git": (
                    "4d101475d8b20a2381f78447822ac1eab6504dd8\trefs/tags/v4.2.2\n"
                ),
            }

            def fake_ls_remote(
                cmd: list[str], **_kwargs: Any
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=ls_remote_by_url[cmd[-1]],
                    stderr="",
                )

            good_manifests = {
                (
                    "actions/cache",
                    "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                    "",
                ): "runs:\n  using: node24\n  main: dist/index.js\n",
                (
                    "actions/attest-build-provenance",
                    "4d101475d8b20a2381f78447822ac1eab6504dd8",
                    "",
                ): (
                    "runs:\n"
                    "  using: composite\n"
                    "  steps:\n"
                    "    - uses: actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d\n"
                ),
                (
                    "actions/attest",
                    "508db95dd578ae2727ebd6217d5ba78e4fbda05d",
                    "",
                ): "runs:\n  using: node24\n  main: dist/index.js\n",
            }
            self.assertEqual(
                [],
                check_workflow_pins.check_pins(
                    root=tmp_root,
                    verify_remote=True,
                    run_fn=fake_ls_remote,
                    manifest_fetcher=lambda r, ref, sub: (
                        good_manifests.get((r, ref, sub)),
                        None
                        if (r, ref, sub) in good_manifests
                        else f"missing {(r, ref, sub)}",
                    ),
                ),
            )

            # Direct action declaring node20 or composite child declaring node20 fails --verify-remote.
            bad_manifests = {
                **good_manifests,
                (
                    "actions/cache",
                    "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
                    "",
                ): "runs:\n  using: node20\n  main: dist/index.js\n",
                (
                    "actions/attest",
                    "508db95dd578ae2727ebd6217d5ba78e4fbda05d",
                    "",
                ): "runs:\n  using: node20\n  main: dist/index.js\n",
            }
            remote_runtime_errs = check_workflow_pins.check_pins(
                root=tmp_root,
                verify_remote=True,
                run_fn=fake_ls_remote,
                manifest_fetcher=lambda r, ref, sub: (
                    bad_manifests.get((r, ref, sub)),
                    None,
                ),
            )
            self.assertEqual(2, len(remote_runtime_errs), remote_runtime_errs)
            self.assertTrue(
                any(
                    "actions/cache@" in e and "runs.using='node20'" in e
                    for e in remote_runtime_errs
                )
            )
            self.assertTrue(
                any(
                    "actions/attest@" in e and "runs.using='node20'" in e
                    for e in remote_runtime_errs
                )
            )

    def test_changed_from_scopes_remote_queries_to_modified_pins_and_rejects_bad_ref(
        self,
    ) -> None:
        """--changed-from skips remote queries when pins are unchanged and queries only modified action repos."""
        sha_checkout = "d23441a48e516b6c34aea4fa41551a30e30af803"
        sha_cache_old = "1111111111111111111111111111111111111111"
        sha_cache_new = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"

        def render_wf(cache_sha: str, extra_cmd: str = "echo ok") -> str:
            return (
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                f"      - uses: actions/checkout@{sha_checkout} # v6\n"
                f"      - uses: actions/cache@{cache_sha} # v6\n"
                f"      - run: {extra_cmd}\n"
            )

        def render_lock(cache_sha: str) -> str:
            return json.dumps(
                {
                    "schema_version": 1,
                    "actions": {
                        "actions/cache": {
                            "sha": cache_sha,
                            "version": "v6",
                            "runtime": "node24",
                        },
                        "actions/checkout": {
                            "sha": sha_checkout,
                            "version": "v6",
                            "runtime": "node24",
                        },
                    },
                },
                indent=2,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "repo"
            (repo / ".github/workflows").mkdir(parents=True)
            _git(repo, "init")
            _write_lf(repo / ".github/workflows/ci.yml", render_wf(sha_cache_old))
            _write_lf(repo / ".github/action-pins.json", render_lock(sha_cache_old))
            _write_lf(repo / "README.md", "# Demo\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "initial pins")

            # 1. Commit editing README.md and a workflow shell command without changing action pins -> 0 remote calls.
            _write_lf(
                repo / ".github/workflows/ci.yml",
                render_wf(sha_cache_old, extra_cmd="echo updated"),
            )
            _write_lf(repo / "README.md", "# Demo updated\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "non-pin change")

            queried_urls: list[str] = []

            def fake_ls_remote(
                cmd: list[str], **_kwargs: Any
            ) -> subprocess.CompletedProcess[str]:
                queried_urls.append(cmd[-1])
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=f"{sha_cache_new}\trefs/tags/v6.1.0\n",
                    stderr="",
                )

            self.assertEqual(
                [],
                check_workflow_pins.check_pins(
                    root=repo,
                    verify_remote=True,
                    changed_from="HEAD~1",
                    run_fn=fake_ls_remote,
                    manifest_fetcher=lambda *_a: ("runs:\n  using: node24\n", None),
                ),
            )
            self.assertEqual([], queried_urls)

            # 2. Commit modifying only actions/cache -> queries only actions/cache.git, skipping actions/checkout.git.
            _write_lf(repo / ".github/workflows/ci.yml", render_wf(sha_cache_new))
            _write_lf(repo / ".github/action-pins.json", render_lock(sha_cache_new))
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "upgrade actions/cache")

            self.assertEqual(
                [],
                check_workflow_pins.check_pins(
                    root=repo,
                    verify_remote=True,
                    changed_from="HEAD~1",
                    run_fn=fake_ls_remote,
                    manifest_fetcher=lambda *_a: ("runs:\n  using: node24\n", None),
                ),
            )
            self.assertEqual(["https://github.com/actions/cache.git"], queried_urls)

            # 3. Invalid --changed-from revision fails closed.
            bad_ref_errs = check_workflow_pins.check_pins(
                root=repo,
                verify_remote=True,
                changed_from="refs/heads/does-not-exist",
                run_fn=fake_ls_remote,
            )
            self.assertEqual(1, len(bad_ref_errs), bad_ref_errs)
            self.assertIn("Cannot resolve --changed-from", bad_ref_errs[0])

    def test_validate_browser_job_guards_playwright_deps_with_dry_run_and_pins_1_65_2(
        self,
    ) -> None:
        """requirements-browser.txt pins playwright==1.65.2 and validate.yml guards install-deps with --dry-run."""
        req_text = (ROOT / "requirements-browser.txt").read_text(encoding="utf-8")
        self.assertIn("playwright==1.65.2", req_text)

        validate_data = yaml.safe_load(VALIDATE_WORKFLOW.read_text(encoding="utf-8"))
        browser_steps = validate_data["jobs"]["browser"]["steps"]
        cache_step = next(s for s in browser_steps if s.get("id") == "playwright-cache")
        self.assertEqual(
            "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
            cache_step.get("uses"),
        )
        self.assertEqual("~/.cache/ms-playwright", cache_step["with"]["path"])
        self.assertIn("requirements-browser.txt", cache_step["with"]["key"])

        miss_step = next(
            s
            for s in browser_steps
            if s.get("if") == "steps.playwright-cache.outputs.cache-hit != 'true'"
        )
        hit_step = next(
            s
            for s in browser_steps
            if s.get("if") == "steps.playwright-cache.outputs.cache-hit == 'true'"
        )
        for dep_step in (miss_step, hit_step):
            run_script = dep_step.get("run", "")
            self.assertIn(
                "playwright install-deps --dry-run chromium",
                run_script,
            )
            self.assertIn("playwright install-deps chromium", run_script)
        self.assertNotIn("--with-deps", miss_step.get("run", ""))

        sh_bin = None if os.name == "nt" else shutil.which("sh")
        if sh_bin is None:
            return
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_dir = Path(tmp_dir) / "bin"
            bin_dir.mkdir()
            log_file = Path(tmp_dir) / "calls.log"
            fake_py = bin_dir / "python"
            _write_lf(
                fake_py,
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$FAKE_PY_LOG"\n'
                'case "$*" in\n'
                '  *"--dry-run"*)\n'
                '    exit "${FAKE_DRY_RUN_EXIT:-0}"\n'
                "    ;;\n"
                "esac\n"
                "exit 0\n",
            )
            fake_py.chmod(0o755)
            env_base = {
                **{k: os.environ[k] for k in _PASSTHROUGH_ENV_KEYS if k in os.environ},
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_PY_LOG": str(log_file),
            }

            # Dry-run exit 0 -> skips live install-deps chromium.
            ok_proc = subprocess.run(
                [sh_bin, "-e", "-c", hit_step["run"]],
                env={**env_base, "FAKE_DRY_RUN_EXIT": "0"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, ok_proc.returncode, ok_proc.stderr)
            self.assertEqual(
                ["-m playwright install-deps --dry-run chromium"],
                log_file.read_text(encoding="utf-8").splitlines(),
            )

            # Dry-run exit 1 -> falls back to live install-deps chromium.
            log_file.unlink()
            miss_proc = subprocess.run(
                [sh_bin, "-e", "-c", hit_step["run"]],
                env={**env_base, "FAKE_DRY_RUN_EXIT": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, miss_proc.returncode, miss_proc.stderr)
            self.assertEqual(
                [
                    "-m playwright install-deps --dry-run chromium",
                    "-m playwright install-deps chromium",
                ],
                log_file.read_text(encoding="utf-8").splitlines(),
            )

    def test_browser_check_prefers_bundled_chromium_isolates_temp_pngs_and_bypasses_loopback_proxies(
        self,
    ) -> None:
        """tests/browser_check.py launches bundled Chromium first, keeps PNGs inside TemporaryDirectory, and bypasses proxies on 127.0.0.1."""
        import http.server
        import threading

        from tests import browser_check

        source_text = (ROOT / "tests/browser_check.py").read_text(encoding="utf-8")
        self.assertNotIn('"/tmp/', source_text)
        self.assertNotIn("'/tmp/", source_text)
        parsed_args = browser_check.build_parser().parse_args(
            ["--screenshot", "/tmp/custom-shot.png"]
        )
        self.assertEqual(Path("/tmp/custom-shot.png"), parsed_args.screenshot)

        # 1. Bundled browser launches first even when system chromium exists on PATH.
        launch_calls: list[dict[str, Any]] = []

        class _FakeChromium:
            def __init__(self, fail_bundled: bool = False) -> None:
                self.fail_bundled = fail_bundled

            def launch(self, **kwargs: Any) -> str:
                launch_calls.append(kwargs)
                if self.fail_bundled and "executable_path" not in kwargs:
                    raise RuntimeError("Executable doesn't exist")
                return "browser-handle"

        class _FakePlaywright:
            def __init__(self, fail_bundled: bool = False) -> None:
                self.chromium = _FakeChromium(fail_bundled=fail_bundled)

        self.assertEqual(
            "browser-handle",
            browser_check.launch_chromium_browser(
                _FakePlaywright(fail_bundled=False),
                env={},
                which_fn=lambda name: (
                    "/usr/bin/chromium" if name == "chromium" else None
                ),
            ),
        )
        self.assertEqual([{"headless": True}], launch_calls)

        # 2. Missing bundled browser falls back to system chromium, and explicit override takes precedence.
        launch_calls.clear()
        self.assertEqual(
            "browser-handle",
            browser_check.launch_chromium_browser(
                _FakePlaywright(fail_bundled=True),
                env={},
                which_fn=lambda name: (
                    "/usr/bin/chromium" if name == "chromium" else None
                ),
            ),
        )
        self.assertEqual(
            [
                {"headless": True},
                {"headless": True, "executable_path": "/usr/bin/chromium"},
            ],
            launch_calls,
        )

        launch_calls.clear()
        self.assertEqual(
            "browser-handle",
            browser_check.launch_chromium_browser(
                _FakePlaywright(fail_bundled=False),
                env={"PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": "/opt/chrome/chrome"},
                which_fn=lambda _name: None,
            ),
        )
        self.assertEqual(
            [{"headless": True, "executable_path": "/opt/chrome/chrome"}],
            launch_calls,
        )

        # 3. NO_PROXY configuration and ProxyHandler({}) loopback opener bypass hostile proxy env vars.
        env_map = {"NO_PROXY": "corp.internal", "no_proxy": ""}
        browser_check.configure_loopback_no_proxy(env_map)
        self.assertEqual("corp.internal,127.0.0.1,localhost", env_map["NO_PROXY"])
        self.assertEqual("127.0.0.1,localhost", env_map["no_proxy"])

        class _OkHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>ok</h1>")

            def log_message(self, _fmt: str, *_args: object) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.dict(
                os.environ,
                {
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "http_proxy": "http://127.0.0.1:9",
                    "https_proxy": "http://127.0.0.1:9",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
                clear=False,
            ):
                with browser_check.open_loopback_url(
                    f"http://127.0.0.1:{port}/index.html", timeout=5.0
                ) as resp:
                    self.assertEqual(200, resp.status)
                    self.assertEqual(b"<h1>ok</h1>", resp.read())
                with self.assertRaises(ValueError):
                    browser_check.open_loopback_url("https://example.com/")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

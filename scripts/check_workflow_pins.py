#!/usr/bin/env python3
"""Verify GitHub Actions commit SHA pins, lockfile parity, and upstream tags."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
LOCKFILE_REL_PATH = ".github/action-pins.json"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_BACKOFF = 0.5

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_COMMENT_RE = re.compile(r"^v\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z._-]+)?$")
MAJOR_COMMENT_RE = re.compile(r"^v\d+$")
MINOR_COMMENT_RE = re.compile(r"^v\d+\.\d+$")

RunCommandFn = Callable[..., subprocess.CompletedProcess[str]]
SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class ActionPin:
    """A parsed GitHub Action reference from a workflow YAML file."""

    relative_path: str
    line_no: int
    repo: str
    sha: str
    version_comment: str


@dataclass(frozen=True)
class LockedActionPin:
    """An action pin recorded in `.github/action-pins.json`."""

    repo: str
    sha: str
    version: str


def _relative_display_path(file_path: Path, root: Path) -> str:
    try:
        return file_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


def extract_inline_comment(line: str) -> str | None:
    """Extract trailing YAML inline comment text (without leading '#'), if present."""
    in_single = False
    in_double = False
    escape = False
    for idx, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_double:
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif (
            ch == "#"
            and not in_single
            and not in_double
            and (idx == 0 or line[idx - 1].isspace())
        ):
            comment = line[idx + 1 :].strip()
            return comment if comment else None
    return None


def _mapping_lookup(node: yaml.MappingNode, key: str) -> yaml.Node | None:
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def _collect_uses_nodes(doc_node: yaml.Node | None) -> list[yaml.ScalarNode]:
    """Collect scalar `uses` nodes from workflow jobs and job steps."""
    if not isinstance(doc_node, yaml.MappingNode):
        return []
    jobs_node = _mapping_lookup(doc_node, "jobs")
    if not isinstance(jobs_node, yaml.MappingNode):
        return []

    found: list[yaml.ScalarNode] = []
    for _, job_node in jobs_node.value:
        if not isinstance(job_node, yaml.MappingNode):
            continue
        job_uses = _mapping_lookup(job_node, "uses")
        if isinstance(job_uses, yaml.ScalarNode):
            found.append(job_uses)
        steps_node = _mapping_lookup(job_node, "steps")
        if not isinstance(steps_node, yaml.SequenceNode):
            continue
        for step_node in steps_node.value:
            if not isinstance(step_node, yaml.MappingNode):
                continue
            step_uses = _mapping_lookup(step_node, "uses")
            if isinstance(step_uses, yaml.ScalarNode):
                found.append(step_uses)
    return found


def _is_valid_repo_target(target: str) -> tuple[str | None, bool]:
    parts = target.split("/")
    if len(parts) < 2:
        return None, False
    for part in parts:
        if not part or part in {".", ".."} or not REPO_PART_RE.fullmatch(part):
            return None, False
    return f"{parts[0]}/{parts[1]}", True


def _validate_single_uses(
    rel_path: str,
    line_no: int,
    uses_value: str,
    raw_line: str,
) -> tuple[ActionPin | None, list[str]]:
    """Validate offline syntax for one `uses` scalar and return (ActionPin, errors)."""
    uses = uses_value.strip()
    loc = f"{rel_path}:L{line_no}"
    if not uses:
        return None, [f"{loc}: Empty 'uses' reference"]

    if uses.startswith(("./", "docker://")):
        return None, []

    target, sep, ref = uses.rpartition("@")
    if not sep or not target or not ref:
        return None, [
            f"{loc}: Action '{uses}' must specify '<owner>/<repo>@<40-char-sha>'"
        ]

    repo, valid_target = _is_valid_repo_target(target)
    if not valid_target or repo is None:
        return None, [
            f"{loc}: Action '{uses}' has invalid repository identifier '{target}'"
        ]

    errors: list[str] = []
    if not SHA_RE.fullmatch(ref):
        errors.append(
            f"{loc}: Action '{uses}' must pin a 40-character lowercase hex commit SHA (got '{ref}')"
        )

    comment = extract_inline_comment(raw_line)
    if comment is None:
        errors.append(
            f"{loc}: Action '{uses}' is missing a trailing version comment (e.g. '# v4' or '# v4.2.2')"
        )
    elif not VERSION_COMMENT_RE.fullmatch(comment):
        errors.append(
            f"{loc}: Action '{uses}' has invalid version comment '# {comment}' (expected e.g. '# v4' or '# v4.2.2')"
        )

    if errors:
        return None, errors

    assert comment is not None
    return (
        ActionPin(
            relative_path=rel_path,
            line_no=line_no,
            repo=repo,
            sha=ref,
            version_comment=comment,
        ),
        [],
    )


def inspect_workflow_file(
    file_path: Path,
    root: Path = ROOT,
) -> tuple[list[ActionPin], list[str]]:
    """Parse and offline-validate all action pins in a single workflow YAML file."""
    rel_path = _relative_display_path(file_path, root)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"{rel_path}: Cannot read workflow file: {exc}"]

    try:
        doc_node = yaml.compose(text)
    except yaml.YAMLError as exc:
        return [], [f"{rel_path}: Invalid YAML syntax: {exc}"]

    lines = text.splitlines()
    pins: list[ActionPin] = []
    errors: list[str] = []

    for uses_node in _collect_uses_nodes(doc_node):
        line_idx = uses_node.start_mark.line
        line_no = line_idx + 1
        raw_line = lines[line_idx] if 0 <= line_idx < len(lines) else ""
        pin, pin_errors = _validate_single_uses(
            rel_path,
            line_no,
            uses_node.value,
            raw_line,
        )
        if pin_errors:
            errors.extend(pin_errors)
        elif pin is not None:
            pins.append(pin)

    return pins, errors


def discover_workflow_files(
    root: Path = ROOT,
    workflow_files: Sequence[Path | str] | None = None,
) -> tuple[list[Path], list[str]]:
    """Discover workflow YAML files under `.github/workflows` or validate explicit paths."""
    if workflow_files:
        resolved: list[Path] = []
        errors: list[str] = []
        for item in workflow_files:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = root / candidate
            if not candidate.is_file():
                errors.append(
                    f"{_relative_display_path(candidate, root)}: Workflow file does not exist"
                )
            else:
                resolved.append(candidate)
        return resolved, errors

    workflows_dir = root / ".github/workflows"
    if not workflows_dir.is_dir():
        return [], [f"{workflows_dir}: Workflows directory does not exist"]

    files = sorted(
        p
        for p in workflows_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".yml", ".yaml")
    )
    if not files:
        return [], [".github/workflows: No workflow YAML files found"]
    return files, []


def load_lockfile(
    lockfile_path: Path,
    root: Path = ROOT,
) -> tuple[dict[str, LockedActionPin], list[str]]:
    """Load and validate `.github/action-pins.json`."""
    rel_path = _relative_display_path(lockfile_path, root)
    if not lockfile_path.is_file():
        return {}, [f"{rel_path}: Action pin lockfile does not exist"]

    try:
        raw_text = lockfile_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"{rel_path}: Cannot read lockfile: {exc}"]

    try:
        payload: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return {}, [f"{rel_path}: Invalid JSON in lockfile: {exc}"]

    if not isinstance(payload, dict):
        return {}, [f"{rel_path}: Lockfile root must be a JSON object"]

    allowed_top_keys = {"schema_version", "actions"}
    extra_top = sorted(set(payload.keys()) - allowed_top_keys)
    if extra_top:
        return {}, [
            f"{rel_path}: Unexpected top-level lockfile key(s): {', '.join(extra_top)}"
        ]

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        return {}, [
            f"{rel_path}: Lockfile 'schema_version' must be integer 1 (got {schema_version!r})"
        ]

    actions_obj = payload.get("actions")
    if not isinstance(actions_obj, dict) or not actions_obj:
        return {}, [f"{rel_path}: Lockfile 'actions' must be a non-empty JSON object"]

    locked: dict[str, LockedActionPin] = {}
    errors: list[str] = []
    for repo_key, entry in actions_obj.items():
        if not isinstance(repo_key, str):
            errors.append(f"{rel_path}: Action key {repo_key!r} must be a string")
            continue
        parts = repo_key.split("/")
        if (
            len(parts) != 2
            or not REPO_PART_RE.fullmatch(parts[0])
            or not REPO_PART_RE.fullmatch(parts[1])
            or parts[0] in {".", ".."}
            or parts[1] in {".", ".."}
        ):
            errors.append(
                f"{rel_path}: Invalid action repository key '{repo_key}' (expected '<owner>/<repo>')"
            )
            continue
        if not isinstance(entry, dict):
            errors.append(
                f"{rel_path}: Entry for '{repo_key}' must be a JSON object with 'sha' and 'version'"
            )
            continue

        extra_entry_keys = sorted(set(entry.keys()) - {"sha", "version"})
        if extra_entry_keys:
            errors.append(
                f"{rel_path}: Entry for '{repo_key}' has unexpected key(s): {', '.join(extra_entry_keys)}"
            )
            continue

        sha = entry.get("sha")
        version = entry.get("version")
        entry_valid = True
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            errors.append(
                f"{rel_path}: Entry for '{repo_key}' must specify a 40-character lowercase hex 'sha' (got {sha!r})"
            )
            entry_valid = False
        if not isinstance(version, str) or not VERSION_COMMENT_RE.fullmatch(version):
            errors.append(
                f"{rel_path}: Entry for '{repo_key}' must specify a valid 'version' tag like 'v4' or 'v4.2.2' (got {version!r})"
            )
            entry_valid = False

        if entry_valid:
            assert isinstance(sha, str) and isinstance(version, str)
            locked[repo_key] = LockedActionPin(repo=repo_key, sha=sha, version=version)

    return locked, errors


def verify_lockfile_parity(
    pins: Sequence[ActionPin],
    locked: Mapping[str, LockedActionPin],
    lockfile_display: str = LOCKFILE_REL_PATH,
    check_unused: bool = True,
) -> list[str]:
    """Verify workflow action pins match `.github/action-pins.json` entries."""
    errors: list[str] = []
    seen_repos: set[str] = set()

    for pin in pins:
        seen_repos.add(pin.repo)
        loc = f"{pin.relative_path}:L{pin.line_no}"
        entry = locked.get(pin.repo)
        if entry is None:
            errors.append(
                f"{loc}: Action '{pin.repo}' is missing from {lockfile_display}"
            )
            continue
        if pin.sha != entry.sha:
            errors.append(
                f"{loc}: Action '{pin.repo}' SHA {pin.sha} does not match {lockfile_display} ({entry.sha})"
            )
        if pin.version_comment != entry.version:
            errors.append(
                f"{loc}: Action '{pin.repo}' version comment '# {pin.version_comment}' does not match {lockfile_display} ('{entry.version}')"
            )

    if check_unused:
        for repo in sorted(set(locked.keys()) - seen_repos):
            errors.append(
                f"{lockfile_display}: Unused lockfile entry '{repo}' is not referenced in any workflow"
            )

    return errors


def parse_ls_remote_output(stdout: str) -> tuple[dict[str, str], set[str]]:
    """Parse `git ls-remote` output into resolved tag->commit map and known commit SHAs.

    Annotated tags advertise both `refs/tags/<name>` (the tag object SHA) and
    `refs/tags/<name>^{}` (the peeled commit SHA). Peeled commit SHAs take precedence.
    """
    raw_tags: dict[str, str] = {}
    peeled_tags: dict[str, str] = {}
    all_commits: set[str] = set()

    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if not SHA_RE.fullmatch(sha):
            continue
        if ref.startswith("refs/tags/"):
            tag_name = ref[len("refs/tags/") :]
            if tag_name.endswith("^{}"):
                peeled_name = tag_name[:-3]
                if peeled_name:
                    peeled_tags[peeled_name] = sha
                    all_commits.add(sha)
            elif tag_name:
                raw_tags[tag_name] = sha
        else:
            all_commits.add(sha)

    tag_commits: dict[str, str] = {}
    for tag_name, sha in raw_tags.items():
        if tag_name in peeled_tags:
            tag_commits[tag_name] = peeled_tags[tag_name]
        else:
            tag_commits[tag_name] = sha
            all_commits.add(sha)

    for tag_name, sha in peeled_tags.items():
        tag_commits.setdefault(tag_name, sha)

    return tag_commits, all_commits


def tag_matches_version_comment(tag: str, comment: str) -> bool:
    """Return True if upstream `refs/tags/<tag>` satisfies `# <comment>`."""
    if tag == comment:
        return True
    if MAJOR_COMMENT_RE.fullmatch(comment):
        return bool(re.fullmatch(rf"{re.escape(comment)}\.\d+(?:\.\d+)?", tag))
    if MINOR_COMMENT_RE.fullmatch(comment):
        return bool(re.fullmatch(rf"{re.escape(comment)}\.\d+", tag))
    return False


def fetch_remote_refs(
    repo: str,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    run_fn: RunCommandFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> tuple[dict[str, str] | None, set[str] | None, str | None]:
    """Query upstream repository refs via `git ls-remote`, retrying transient failures."""
    effective_run = subprocess.run if run_fn is None else run_fn
    max_attempts = max(1, retries + 1)
    url = f"https://github.com/{repo}.git"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    last_error = f"Unable to query upstream refs for {repo}"

    for attempt in range(max_attempts):
        if attempt > 0 and retry_backoff > 0:
            sleep_fn(retry_backoff * (2 ** (attempt - 1)))

        try:
            proc = effective_run(
                ["git", "ls-remote", url],
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_error = f"git ls-remote timed out after {timeout:g}s for {repo}"
            continue
        except FileNotFoundError as exc:
            return None, None, f"Failed to execute git ls-remote for {repo}: {exc}"
        except OSError as exc:
            last_error = f"Failed to execute git ls-remote for {repo}: {exc}"
            continue

        if proc.returncode == 0:
            tag_commits, all_commits = parse_ls_remote_output(proc.stdout)
            return tag_commits, all_commits, None

        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        last_error = f"git ls-remote failed for {repo}: {detail}"

    return None, None, last_error


def verify_pins_online(
    pins: Sequence[ActionPin],
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    run_fn: RunCommandFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> list[str]:
    """Verify action commit SHAs and version comments against upstream repositories."""
    unique_repos = sorted({pin.repo for pin in pins})
    remote_cache: dict[str, tuple[dict[str, str], set[str]]] = {}
    errors: list[str] = []

    for repo in unique_repos:
        tag_commits, all_commits, err = fetch_remote_refs(
            repo,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            run_fn=run_fn,
            sleep_fn=sleep_fn,
        )
        if err is not None or tag_commits is None or all_commits is None:
            errors.append(err or f"Unable to query upstream refs for {repo}")
            continue
        remote_cache[repo] = (tag_commits, all_commits)

    for pin in pins:
        if pin.repo not in remote_cache:
            continue
        tag_commits, all_commits = remote_cache[pin.repo]
        loc = f"{pin.relative_path}:L{pin.line_no}"
        sha_tags = sorted(
            tag for tag, commit in tag_commits.items() if commit == pin.sha
        )
        if any(
            tag_matches_version_comment(tag, pin.version_comment) for tag in sha_tags
        ):
            continue

        expected_commit = tag_commits.get(pin.version_comment)
        if pin.sha not in all_commits:
            hint = (
                f"; tag '{pin.version_comment}' resolves to {expected_commit}"
                if expected_commit
                else ""
            )
            errors.append(
                f"{loc}: Commit SHA {pin.sha} for '{pin.repo}' not found in upstream refs{hint}"
            )
        elif sha_tags:
            errors.append(
                f"{loc}: Commit SHA {pin.sha} for '{pin.repo}' resolves to tag(s) {sha_tags}, which do not match comment '# {pin.version_comment}'"
            )
        else:
            hint = (
                f" (tag '{pin.version_comment}' resolves to {expected_commit})"
                if expected_commit
                else ""
            )
            errors.append(
                f"{loc}: Commit SHA {pin.sha} for '{pin.repo}' does not resolve to any tag matching '# {pin.version_comment}'{hint}"
            )

    return errors


def check_pins(
    root: Path = ROOT,
    workflow_files: Sequence[Path | str] | None = None,
    lockfile: Path | str | None = None,
    require_lockfile: bool = True,
    verify_remote: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    run_fn: RunCommandFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> list[str]:
    """Validate workflow action pins, offline lockfile parity, and optional remote SHAs."""
    files, errors = discover_workflow_files(root=root, workflow_files=workflow_files)
    if errors:
        return errors

    all_pins: list[ActionPin] = []
    for wf_file in files:
        pins, file_errors = inspect_workflow_file(wf_file, root=root)
        all_pins.extend(pins)
        errors.extend(file_errors)

    lockfile_path: Path | None
    if lockfile is not None:
        candidate = Path(lockfile)
        lockfile_path = candidate if candidate.is_absolute() else root / candidate
    else:
        default_lockfile = root / LOCKFILE_REL_PATH
        lockfile_path = (
            default_lockfile
            if (require_lockfile or default_lockfile.exists())
            else None
        )

    if lockfile_path is not None:
        locked, lock_errors = load_lockfile(lockfile_path, root=root)
        errors.extend(lock_errors)
        if not lock_errors:
            errors.extend(
                verify_lockfile_parity(
                    all_pins,
                    locked,
                    lockfile_display=_relative_display_path(lockfile_path, root),
                    check_unused=(workflow_files is None),
                )
            )

    if errors or not verify_remote:
        return errors

    errors.extend(
        verify_pins_online(
            all_pins,
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            run_fn=run_fn,
            sleep_fn=sleep_fn,
        )
    )
    return errors


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for `scripts/check_workflow_pins.py`."""
    parser = argparse.ArgumentParser(
        description=(
            "Verify GitHub Actions commit SHA pins, .github/action-pins.json lockfile "
            "parity, and optional upstream git ls-remote tag resolution."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate 40-character hex SHAs, version comments, and lockfile parity offline (default mode).",
    )
    parser.add_argument(
        "--verify-remote",
        "--online",
        dest="verify_remote",
        action="store_true",
        help="Also query upstream action repositories via git ls-remote to verify commit and tag resolution.",
    )
    parser.add_argument(
        "--lockfile",
        type=Path,
        default=None,
        help=f"Path to action pin lockfile (default: {LOCKFILE_REL_PATH} relative to --root).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-repository timeout in seconds for git ls-remote (default: {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Number of retries for transient git ls-remote failures (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional specific workflow YAML files to check (defaults to .github/workflows/*.yml).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for `scripts/check_workflow_pins.py`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.retries < 0:
        parser.error("--retries must be >= 0")

    errors = check_pins(
        root=args.root,
        workflow_files=args.files if args.files else None,
        lockfile=args.lockfile,
        require_lockfile=True,
        verify_remote=args.verify_remote,
        timeout=args.timeout,
        retries=args.retries,
    )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(
            f"FAILED: Found {len(errors)} workflow action pin error(s).",
            file=sys.stderr,
        )
        return 1

    mode_label = (
        "offline lockfile and upstream remote"
        if args.verify_remote
        else "offline lockfile"
    )
    print(f"PASS: Verified workflow action pins ({mode_label}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

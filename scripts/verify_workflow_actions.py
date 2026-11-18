#!/usr/bin/env python3
"""Verify GitHub Actions commit SHA pins and version comments across workflows."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 30.0

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DOCKER_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPO_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_COMMENT_RE = re.compile(r"^v\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z._-]+)?$")
MAJOR_COMMENT_RE = re.compile(r"^v\d+$")
MINOR_COMMENT_RE = re.compile(r"^v\d+\.\d+$")


@dataclass(frozen=True)
class ActionPin:
    """A parsed GitHub Action reference from a workflow YAML file."""

    workflow_path: Path
    relative_path: str
    line_no: int
    uses: str
    repo: str
    sha: str
    version_comment: str


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


def _validate_single_uses(
    rel_path: str,
    file_path: Path,
    line_no: int,
    uses_value: str,
    raw_line: str,
) -> tuple[ActionPin | None, list[str]]:
    """Validate offline syntax for one `uses` scalar and return (ActionPin, errors)."""
    uses = uses_value.strip()
    loc = f"{rel_path}:L{line_no}"
    if not uses:
        return None, [f"{loc}: Empty 'uses' reference"]

    if uses.startswith("./"):
        return None, []

    if uses.startswith("docker://"):
        _, sep, digest = uses.rpartition("@")
        if not sep or not DOCKER_DIGEST_RE.fullmatch(digest):
            return None, [
                f"{loc}: Docker action '{uses}' must be pinned to @sha256:<64-hex>"
            ]
        return None, []

    target, sep, ref = uses.rpartition("@")
    if not sep or not target or not ref:
        return None, [
            f"{loc}: Action '{uses}' must specify '<owner>/<repo>@<40-char-sha>'"
        ]

    parts = target.split("/")
    if (
        len(parts) < 2
        or not REPO_PART_RE.fullmatch(parts[0])
        or not REPO_PART_RE.fullmatch(parts[1])
    ):
        return None, [
            f"{loc}: Action '{uses}' has invalid repository identifier '{target}'"
        ]
    repo = f"{parts[0]}/{parts[1]}"

    errors: list[str] = []
    if not SHA_RE.fullmatch(ref):
        errors.append(
            f"{loc}: Action '{uses}' must pin a 40-character lowercase hex commit SHA (got '{ref}')"
        )

    comment = extract_inline_comment(raw_line)
    if comment is None:
        errors.append(
            f"{loc}: Action '{uses}' is missing a trailing version comment (e.g. '# v4' or '# v2.2.3')"
        )
    elif not VERSION_COMMENT_RE.fullmatch(comment):
        errors.append(
            f"{loc}: Action '{uses}' has invalid version comment '# {comment}' (expected e.g. '# v4' or '# v2.2.3')"
        )

    if errors:
        return None, errors

    assert comment is not None
    return (
        ActionPin(
            workflow_path=file_path,
            relative_path=rel_path,
            line_no=line_no,
            uses=uses,
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
            file_path,
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
) -> tuple[dict[str, str] | None, set[str] | None, str | None]:
    """Query upstream repository refs once via `git ls-remote`."""
    url = f"https://github.com/{repo}.git"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "ls-remote", url],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, None, f"git ls-remote timed out after {timeout:g}s for {repo}"
    except OSError as exc:
        return None, None, f"Failed to execute git ls-remote for {repo}: {exc}"

    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        return None, None, f"git ls-remote failed for {repo}: {detail}"

    tag_commits, all_commits = parse_ls_remote_output(proc.stdout)
    return tag_commits, all_commits, None


def verify_pins_online(
    pins: Sequence[ActionPin],
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Verify action commit SHAs and version comments against upstream repositories."""
    unique_repos = sorted({pin.repo for pin in pins})
    remote_cache: dict[str, tuple[dict[str, str], set[str]]] = {}
    errors: list[str] = []

    for repo in unique_repos:
        tag_commits, all_commits, err = fetch_remote_refs(repo, timeout=timeout)
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


def verify_workflows(
    root: Path = ROOT,
    workflow_files: Sequence[Path | str] | None = None,
    online: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[str]:
    """Verify workflow action pins offline and optionally online against upstream tags."""
    files, errors = discover_workflow_files(root=root, workflow_files=workflow_files)
    if errors:
        return errors

    all_pins: list[ActionPin] = []
    for wf_file in files:
        pins, file_errors = inspect_workflow_file(wf_file, root=root)
        all_pins.extend(pins)
        errors.extend(file_errors)

    if errors or not online:
        return errors

    errors.extend(verify_pins_online(all_pins, timeout=timeout))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify GitHub Actions commit SHA pins and version comments across workflows."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate 40-character lowercase hex SHAs and version comments offline (default mode).",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Also query upstream action repositories via git ls-remote to verify commit and tag resolution.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-repository timeout in seconds for git ls-remote in --online mode (default: {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional specific workflow YAML files to check (defaults to .github/workflows/*.yml).",
    )

    args = parser.parse_args(argv)
    errors = verify_workflows(
        root=ROOT,
        workflow_files=args.files if args.files else None,
        online=args.online,
        timeout=args.timeout,
    )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(
            f"FAILED: Found {len(errors)} workflow action pin error(s).",
            file=sys.stderr,
        )
        return 1

    mode_label = "offline and upstream online" if args.online else "offline"
    print(f"PASS: Verified workflow action pins ({mode_label}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

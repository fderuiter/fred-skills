#!/usr/bin/env python3
"""Verify GitHub Actions commit SHA pins and version comments across workflows."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

sys.dont_write_bytecode = True

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import check_workflow_pins
from check_workflow_pins import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    ActionPin,
    LockedActionPin,
    discover_workflow_files,
    extract_inline_comment,
    inspect_workflow_file,
    load_lockfile,
    parse_ls_remote_output,
    tag_matches_version_comment,
    verify_lockfile_parity,
)

ROOT = check_workflow_pins.ROOT

__all__ = [
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "ROOT",
    "ActionPin",
    "LockedActionPin",
    "discover_workflow_files",
    "extract_inline_comment",
    "fetch_remote_refs",
    "inspect_workflow_file",
    "load_lockfile",
    "main",
    "parse_ls_remote_output",
    "tag_matches_version_comment",
    "verify_lockfile_parity",
    "verify_pins_online",
    "verify_workflows",
]


def fetch_remote_refs(
    repo: str,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 0,
) -> tuple[dict[str, str] | None, set[str] | None, str | None]:
    """Query upstream repository refs via `git ls-remote`."""
    return check_workflow_pins.fetch_remote_refs(
        repo,
        timeout=timeout,
        retries=retries,
        run_fn=subprocess.run,
    )


def verify_pins_online(
    pins: Sequence[ActionPin],
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 0,
) -> list[str]:
    """Verify action commit SHAs and version comments against upstream repositories."""
    return check_workflow_pins.verify_pins_online(
        pins,
        timeout=timeout,
        retries=retries,
        run_fn=subprocess.run,
    )


def verify_workflows(
    root: Path = ROOT,
    workflow_files: Sequence[Path | str] | None = None,
    online: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    lockfile: Path | str | None = None,
    require_lockfile: bool = False,
    retries: int = 0,
) -> list[str]:
    """Verify workflow action pins offline (including lockfile when present) and optionally online."""
    return check_workflow_pins.check_pins(
        root=root,
        workflow_files=workflow_files,
        lockfile=lockfile,
        require_lockfile=require_lockfile,
        verify_remote=online,
        timeout=timeout,
        retries=retries,
        run_fn=subprocess.run,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify GitHub Actions commit SHA pins and version comments across workflows."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate 40-character lowercase hex SHAs, version comments, and lockfile parity offline (default mode).",
    )
    parser.add_argument(
        "--online",
        "--verify-remote",
        dest="online",
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
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Number of retries for transient git ls-remote failures in --online mode (default: {DEFAULT_RETRIES}).",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional specific workflow YAML files to check (defaults to .github/workflows/*.yml).",
    )

    args = parser.parse_args(argv)
    if args.retries < 0:
        parser.error("--retries must be >= 0")

    errors = verify_workflows(
        root=ROOT,
        workflow_files=args.files if args.files else None,
        online=args.online,
        timeout=args.timeout,
        require_lockfile=not bool(args.files),
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

    mode_label = "offline and upstream online" if args.online else "offline"
    print(f"PASS: Verified workflow action pins ({mode_label}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

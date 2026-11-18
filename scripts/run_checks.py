#!/usr/bin/env python3
"""Unified cross-platform validation runner for repository checks."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

ALL_PHASES: tuple[str, ...] = ("lint", "format", "packages", "docs", "tests")
DEFAULT_PHASES: tuple[str, ...] = ("lint", "format", "packages", "tests")


def build_check_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child-process environment with bytecode caching disabled."""
    env = dict(os.environ if base_env is None else base_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def resolve_ruff_command(
    python_executable: str = sys.executable,
    ruff_override: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Resolve a cross-platform Ruff invocation command."""
    effective_env = os.environ if env is None else env
    configured = ruff_override or effective_env.get("RUFF")
    if configured:
        resolved = shutil.which(configured)
        return [resolved or configured]

    exe_name = "ruff.exe" if os.name == "nt" else "ruff"
    venv_ruff = Path(python_executable).resolve().parent / exe_name
    if venv_ruff.is_file():
        return [str(venv_ruff)]

    which_ruff = shutil.which("ruff")
    if which_ruff:
        return [which_ruff]

    return [python_executable, "-m", "ruff"]


def resolve_phases(requested: Sequence[str] | None = None) -> list[str]:
    """Resolve requested phases in canonical execution order.

    Full runs (when no explicit phases are supplied) omit standalone doc scripts
    because tests/test_doc_verification.py already executes link and snippet checks.
    """
    if not requested:
        return list(DEFAULT_PHASES)

    unknown = [phase for phase in requested if phase not in ALL_PHASES]
    if unknown:
        joined = ", ".join(unknown)
        valid = ", ".join(ALL_PHASES)
        raise ValueError(f"Unknown check phase(s): {joined} (expected one of: {valid})")

    requested_set = set(requested)
    return [phase for phase in ALL_PHASES if phase in requested_set]


def build_phase_commands(
    phase: str,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    ruff_cmd: Sequence[str] | None = None,
    telemetry_path: str | None = None,
) -> list[list[str]]:
    """Build the ordered command list for a single check phase."""
    resolved_ruff = (
        list(ruff_cmd)
        if ruff_cmd is not None
        else resolve_ruff_command(python_executable=python_executable)
    )
    scripts_dir = root / "scripts"

    if phase == "lint":
        return [[*resolved_ruff, "check", "."]]
    if phase == "format":
        return [[*resolved_ruff, "format", "--check", "."]]
    if phase == "packages":
        validate_cmd = [python_executable, str(scripts_dir / "validate_skills.py")]
        if telemetry_path:
            validate_cmd.extend(["--telemetry", telemetry_path])
        return [
            [
                python_executable,
                str(scripts_dir / "sync_audit_resources.py"),
                "--check",
            ],
            [python_executable, str(scripts_dir / "build_catalog.py"), "--check"],
            [python_executable, str(scripts_dir / "build_experience.py"), "--check"],
            [
                python_executable,
                str(scripts_dir / "verify_workflow_actions.py"),
                "--check",
            ],
            validate_cmd,
        ]
    if phase == "docs":
        return [
            [python_executable, str(scripts_dir / "check_doc_links.py")],
            [python_executable, str(scripts_dir / "verify_doc_snippets.py")],
        ]
    if phase == "tests":
        return [[python_executable, "-m", "unittest", "discover", "-s", "tests", "-v"]]

    raise ValueError(f"Unsupported check phase: {phase}")


def _format_command_display(cmd: Sequence[str], root: Path) -> str:
    """Format a command cleanly with repo-relative paths for readable logs."""
    display_parts: list[str] = []
    resolved_root = root.resolve()
    for idx, token in enumerate(cmd):
        if idx == 0 and Path(token).name.lower().startswith("python"):
            display_parts.append("python")
            continue
        if idx == 0 and Path(token).stem.lower() == "ruff":
            display_parts.append("ruff")
            continue
        token_path = Path(token)
        if token_path.is_absolute():
            try:
                rel = token_path.resolve().relative_to(resolved_root).as_posix()
                display_parts.append(rel)
                continue
            except ValueError:
                pass
        display_parts.append(token)
    return shlex.join(display_parts)


def run_checks(
    phases: Sequence[str] | None = None,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    ruff_override: str | None = None,
    telemetry_path: str | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
) -> int:
    """Execute the selected validation phases and return an exit status code."""
    selected_phases = resolve_phases(phases)
    check_env = build_check_env(env)
    ruff_cmd = resolve_ruff_command(
        python_executable=python_executable,
        ruff_override=ruff_override,
        env=check_env,
    )

    for phase in selected_phases:
        commands = build_phase_commands(
            phase=phase,
            root=root,
            python_executable=python_executable,
            ruff_cmd=ruff_cmd,
            telemetry_path=telemetry_path,
        )
        for cmd in commands:
            display = _format_command_display(cmd, root)
            if dry_run:
                print(f"[dry-run:{phase}] {display}", flush=True)
                continue
            print(f"==> [{phase}] {display}", flush=True)
            proc = subprocess.run(cmd, cwd=root, env=check_env, check=False)
            if proc.returncode != 0:
                print(
                    f"FAILED: phase '{phase}' command exited with status {proc.returncode}: {display}",
                    file=sys.stderr,
                    flush=True,
                )
                return proc.returncode

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for scripts/run_checks.py."""
    parser = argparse.ArgumentParser(
        description=(
            "Run cross-platform repository validation checks. "
            "Defaults to lint, format, packages, and tests (omitting standalone doc "
            "scripts covered by tests/test_doc_verification.py)."
        )
    )
    parser.add_argument(
        "--phase",
        dest="phases",
        action="append",
        choices=ALL_PHASES,
        metavar="PHASE",
        help=f"Check phase to execute ({', '.join(ALL_PHASES)}); repeatable.",
    )
    parser.add_argument(
        "positional_phases",
        nargs="*",
        metavar="PHASE",
        help="Optional phase names to execute.",
    )
    parser.add_argument(
        "--telemetry",
        nargs="?",
        const="validation-telemetry.json",
        default=None,
        metavar="PATH",
        help="Write validation telemetry JSON from validate_skills.py to PATH.",
    )
    parser.add_argument(
        "--ruff",
        default=None,
        metavar="BIN",
        help="Explicit Ruff executable path or command name.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print resolved check commands without executing them.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for scripts/run_checks.py."""
    parser = build_parser()
    args = parser.parse_args(argv)

    combined_phases: list[str] = []
    if args.phases:
        combined_phases.extend(args.phases)
    if args.positional_phases:
        invalid = [p for p in args.positional_phases if p not in ALL_PHASES]
        if invalid:
            parser.error(
                f"argument PHASE: invalid choice: {invalid[0]!r} "
                f"(choose from {', '.join(repr(p) for p in ALL_PHASES)})"
            )
        combined_phases.extend(args.positional_phases)

    return run_checks(
        phases=combined_phases or None,
        root=args.root,
        ruff_override=args.ruff,
        telemetry_path=args.telemetry,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Unified cross-platform validation runner for repository checks."""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

ALL_PHASES: tuple[str, ...] = (
    "lint",
    "format",
    "typecheck",
    "packages",
    "docs",
    "tests",
)
DEFAULT_PHASES: tuple[str, ...] = (
    "lint",
    "format",
    "typecheck",
    "packages",
    "docs",
    "tests",
)
FAST_PHASE_TIMEOUT = 300
TESTS_PHASE_TIMEOUT = 600
DEFAULT_TEST_WORKERS = 4
PHASE_TIMEOUTS: dict[str, int] = {
    "lint": FAST_PHASE_TIMEOUT,
    "format": FAST_PHASE_TIMEOUT,
    "typecheck": FAST_PHASE_TIMEOUT,
    "packages": FAST_PHASE_TIMEOUT,
    "docs": FAST_PHASE_TIMEOUT,
    "tests": TESTS_PHASE_TIMEOUT,
}
SLOWEST_TEST_MODULES: tuple[str, ...] = (
    "tests.test_fred_cli",
    "tests.test_evaluation_cli",
    "tests.test_packaging",
    "tests.test_installation_release",
    "tests.test_doc_verification",
    "tests.test_package_skills",
    "tests.test_runbooks_integration",
    "tests.test_scaffolder_and_telemetry",
    "tests.test_release_workflow",
    "tests.test_diagnostics",
    "tests.test_toolkit",
    "tests.test_vetting",
    "tests.test_suite_validators",
    "tests.test_build_site",
    "tests.test_pages_workflow",
    "tests.test_audit_to_sarif",
    "tests.test_audit_validator",
    "tests.test_api_guide",
    "tests.test_catalog_experience",
)
_SLOWEST_MODULE_RANK: dict[str, int] = {
    mod: idx for idx, mod in enumerate(SLOWEST_TEST_MODULES)
}


@dataclass(frozen=True)
class _ModuleRunResult:
    index: int
    display: str
    returncode: int
    duration: float
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def _coerce_text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def build_check_env(
    base_env: Mapping[str, str] | None = None,
    temp_dir: Path | None = None,
) -> dict[str, str]:
    """Return a child-process environment with bytecode caching disabled."""
    env = dict(os.environ if base_env is None else base_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if temp_dir is not None:
        resolved_tmp = str(temp_dir)
        env["TMPDIR"] = resolved_tmp
        env["TEMP"] = resolved_tmp
        env["TMP"] = resolved_tmp
        env["RUNNER_TEMP"] = resolved_tmp
    return env


def resolve_workers(workers: int | None = None) -> int:
    """Resolve the parallel worker count for the tests phase."""
    if workers is not None:
        if workers < 1:
            raise ValueError(f"Worker count must be >= 1, got {workers}")
        return workers
    return min(DEFAULT_TEST_WORKERS, max(1, os.cpu_count() or 1))


def discover_test_modules(root: Path = ROOT) -> list[str]:
    """Discover unittest modules under tests/ ordered slowest-first."""
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return []

    discovered: list[tuple[str, int]] = []
    for candidate in sorted(tests_dir.rglob("test*.py")):
        if not candidate.is_file():
            continue
        rel_parent = candidate.parent.relative_to(tests_dir)
        if rel_parent != Path("."):
            current = tests_dir
            importable = True
            for part in rel_parent.parts:
                current = current / part
                if not (current / "__init__.py").is_file():
                    importable = False
                    break
            if not importable:
                continue
        rel_mod = candidate.relative_to(root).with_suffix("")
        module_name = ".".join(rel_mod.parts)
        try:
            file_size = candidate.stat().st_size
        except OSError:
            file_size = 0
        discovered.append((module_name, file_size))

    def _sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
        mod_name, size = item
        if mod_name in _SLOWEST_MODULE_RANK:
            return (0, _SLOWEST_MODULE_RANK[mod_name], mod_name)
        return (1, -size, mod_name)

    discovered.sort(key=_sort_key)
    return [mod_name for mod_name, _ in discovered]


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
    """Resolve requested phases in canonical execution order."""
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
    base_ref: str | None = None,
    step_summary: str | None = None,
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
    if phase == "typecheck":
        return [[python_executable, "-m", "mypy"]]
    if phase == "packages":
        validate_cmd = [python_executable, str(scripts_dir / "validate_skills.py")]
        if base_ref is not None:
            validate_cmd.extend(["--base-ref", base_ref])
        if telemetry_path:
            validate_cmd.extend(["--telemetry", telemetry_path])
        if step_summary is not None:
            if step_summary:
                validate_cmd.extend(["--step-summary", step_summary])
            else:
                validate_cmd.append("--step-summary")
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
            [
                python_executable,
                str(scripts_dir / "build_site.py"),
                "--output",
                str(root / "dist" / "site"),
            ],
            [python_executable, str(scripts_dir / "check_doc_links.py")],
            [python_executable, str(scripts_dir / "verify_doc_snippets.py")],
        ]
    if phase == "tests":
        return [
            [python_executable, "-B", "-m", "unittest", "-v", module_name]
            for module_name in discover_test_modules(root)
        ]

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


def _run_tests_phase(
    commands: Sequence[Sequence[str]],
    root: Path,
    base_env: Mapping[str, str],
    phase_timeout: int,
    workers: int,
) -> int:
    """Execute unittest modules across isolated parallel workers with ordered logs."""
    if not commands:
        print(
            f"FAILED: phase 'tests' discovered no test modules under {root / 'tests'}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    effective_workers = min(workers, len(commands))
    print(
        f"==> [tests] Running {len(commands)} test module(s) with {effective_workers} worker(s)",
        flush=True,
    )

    phase_start = time.monotonic()
    deadline = phase_start + phase_timeout
    cancel_event = threading.Event()
    worker_slots: queue.Queue[int] = queue.Queue()
    for slot_id in range(effective_workers):
        worker_slots.put(slot_id)

    with tempfile.TemporaryDirectory(prefix="fred-checks-tests-") as temp_root_str:
        temp_root = Path(temp_root_str).resolve()

        def _execute_module(idx: int, cmd: Sequence[str]) -> _ModuleRunResult:
            display = _format_command_display(cmd, root)
            if cancel_event.is_set():
                return _ModuleRunResult(
                    index=idx,
                    display=display,
                    returncode=124,
                    duration=0.0,
                    stdout="",
                    stderr="",
                    cancelled=True,
                )

            slot_id = worker_slots.get()
            try:
                if cancel_event.is_set():
                    return _ModuleRunResult(
                        index=idx,
                        display=display,
                        returncode=124,
                        duration=0.0,
                        stdout="",
                        stderr="",
                        cancelled=True,
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cancel_event.set()
                    return _ModuleRunResult(
                        index=idx,
                        display=display,
                        returncode=124,
                        duration=0.0,
                        stdout="",
                        stderr="",
                        timed_out=True,
                    )

                call_timeout = min(phase_timeout, max(1, math.ceil(remaining)))
                module_slug = cmd[-1].replace(".", "_")
                worker_tmp = (
                    temp_root / f"worker-{slot_id}" / f"{idx:02d}-{module_slug}"
                )
                worker_tmp.mkdir(parents=True, exist_ok=True)
                worker_env = build_check_env(base_env, temp_dir=worker_tmp)

                t0 = time.monotonic()
                try:
                    proc = subprocess.run(
                        list(cmd),
                        cwd=root,
                        env=worker_env,
                        capture_output=True,
                        text=True,
                        timeout=call_timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    cancel_event.set()
                    return _ModuleRunResult(
                        index=idx,
                        display=display,
                        returncode=124,
                        duration=time.monotonic() - t0,
                        stdout=_coerce_text(exc.stdout),
                        stderr=_coerce_text(exc.stderr),
                        timed_out=True,
                    )

                return _ModuleRunResult(
                    index=idx,
                    display=display,
                    returncode=proc.returncode,
                    duration=time.monotonic() - t0,
                    stdout=_coerce_text(proc.stdout),
                    stderr=_coerce_text(proc.stderr),
                )
            finally:
                worker_slots.put(slot_id)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=effective_workers
        ) as executor:
            futures = [
                executor.submit(_execute_module, idx, cmd)
                for idx, cmd in enumerate(commands)
            ]
            ordered_results = [future.result() for future in futures]

    for res in ordered_results:
        if res.cancelled:
            continue
        status = (
            "TIMEOUT"
            if res.timed_out
            else ("PASS" if res.returncode == 0 else f"EXIT {res.returncode}")
        )
        print(
            f"==> [tests] {res.display} ({status}, {res.duration:.1f}s)",
            flush=True,
        )

    has_timeout = False
    first_failure_rc = 0
    for res in ordered_results:
        if res.cancelled or (not res.timed_out and res.returncode == 0):
            continue
        print(f"--- [tests] log: {res.display} ---", file=sys.stderr, flush=True)
        if res.stdout:
            sys.stderr.write(res.stdout)
            if not res.stdout.endswith("\n"):
                sys.stderr.write("\n")
        if res.stderr:
            sys.stderr.write(res.stderr)
            if not res.stderr.endswith("\n"):
                sys.stderr.write("\n")
        sys.stderr.flush()
        if res.timed_out:
            has_timeout = True
            print(
                f"FAILED: phase 'tests' command timed out after {phase_timeout}s: {res.display}",
                file=sys.stderr,
                flush=True,
            )
        else:
            if first_failure_rc == 0:
                first_failure_rc = res.returncode
            print(
                f"FAILED: phase 'tests' command exited with status {res.returncode}: {res.display}",
                file=sys.stderr,
                flush=True,
            )

    if has_timeout:
        return 124
    return first_failure_rc


def run_checks(
    phases: Sequence[str] | None = None,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    ruff_override: str | None = None,
    telemetry_path: str | None = None,
    base_ref: str | None = None,
    step_summary: str | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    workers: int | None = None,
) -> int:
    """Execute the selected validation phases and return an exit status code."""
    selected_phases = resolve_phases(phases)
    resolved_workers = resolve_workers(workers)
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
            base_ref=base_ref,
            step_summary=step_summary,
        )
        phase_timeout = PHASE_TIMEOUTS.get(phase, FAST_PHASE_TIMEOUT)
        if phase == "tests" and not dry_run:
            rc = _run_tests_phase(
                commands=commands,
                root=root,
                base_env=check_env,
                phase_timeout=phase_timeout,
                workers=resolved_workers,
            )
            if rc != 0:
                return rc
            continue

        for cmd in commands:
            display = _format_command_display(cmd, root)
            if dry_run:
                print(f"[dry-run:{phase}] {display}", flush=True)
                continue
            print(f"==> [{phase}] {display}", flush=True)
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=root,
                    env=check_env,
                    timeout=phase_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"FAILED: phase '{phase}' command timed out after {phase_timeout}s: {display}",
                    file=sys.stderr,
                    flush=True,
                )
                return 124
            if proc.returncode != 0:
                print(
                    f"FAILED: phase '{phase}' command exited with status {proc.returncode}: {display}",
                    file=sys.stderr,
                    flush=True,
                )
                return proc.returncode

    return 0


def _positive_int(raw: str) -> int:
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {raw!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {parsed}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for scripts/run_checks.py."""
    parser = argparse.ArgumentParser(
        description=(
            "Run cross-platform repository validation checks. "
            "Defaults to lint, format, typecheck, packages, docs, and tests."
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
        "--workers",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            f"Number of parallel test module workers "
            f"(default: up to {DEFAULT_TEST_WORKERS} based on CPU count)."
        ),
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
        "--base-ref",
        default=None,
        metavar="REF",
        help="Git base ref or SHA forwarded to validate_skills.py for diff telemetry.",
    )
    parser.add_argument(
        "--step-summary",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Append Markdown telemetry summary to PATH or $GITHUB_STEP_SUMMARY.",
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
        base_ref=args.base_ref,
        step_summary=args.step_summary,
        dry_run=args.dry_run,
        workers=args.workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())

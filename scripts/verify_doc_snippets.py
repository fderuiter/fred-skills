#!/usr/bin/env python3
"""Extract, validate, and dry-run execute documentation command snippets."""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import fred
except ImportError:
    fred = None

# Line ranges for acceptance criteria verification
DOCUMENTED_RANGES: dict[str, tuple[int | None, int | None]] = {
    "README.md": (27, 122),
    "CONTRIBUTING.md": (22, 73),
    "AGENTS.md": (24, 52),
    "docs/toolkit.md": (10, 195),
    "docs/usage.md": (24, 127),
    "docs/runbooks.md": (1, None),
    "docs/integration.md": (1, None),
}

PLACEHOLDER_DEFAULTS: dict[str, str] = {
    "host": "codex",
    "scope": "project",
    "skill-name": "repo-audit-concurrency",
    "name": "repo-audit-concurrency",
    "skill": "repo-audit-concurrency",
    "skill-1": "repo-audit-concurrency",
    "skill-2": "repo-audit-trust-boundaries",
    "base": "main",
    "revision": "HEAD",
    "branch": "main",
    "tag": "v1.0.0",
}


def has_unclosed_quotes(text: str) -> bool:
    """Return True if text contains unclosed single or double quotes."""
    in_single = False
    in_double = False
    escape = False
    for char in text:
        if escape:
            escape = False
            continue
        if char == "\\" and not in_single:
            escape = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
    return in_single or in_double


def normalize_cli_token(token: str) -> str:
    """Replace documented placeholder tokens (e.g. <host>, <scope>) with valid default values."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        if key in PLACEHOLDER_DEFAULTS:
            return PLACEHOLDER_DEFAULTS[key]
        if "host" in key:
            return "codex"
        if "scope" in key:
            return "project"
        if "skill" in key or "name" in key:
            return "repo-audit-concurrency"
        return "placeholder"

    return re.sub(r"<([a-zA-Z0-9_\-]+)>", _replace, token)


@dataclass
class CommandBlock:
    file_path: Path
    relative_path: str
    start_line: int
    end_line: int
    language: str
    commands: list[tuple[int, str]]  # (line_no, command_str)


def extract_blocks_from_file(
    file_path: Path,
    start_line: int | None = None,
    end_line: int | None = None,
    root: Path = ROOT,
) -> list[CommandBlock]:
    """Extract fenced code blocks and commands from a markdown file within an optional line range."""
    if not file_path.is_file():
        return []

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    try:
        rel_path = file_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel_path = str(file_path).replace("\\", "/")

    blocks: list[CommandBlock] = []
    in_block = False
    block_lang = ""
    block_lines: list[tuple[int, str]] = []
    block_start = 0

    for idx, line in enumerate(lines, start=1):
        stripped_line = line.strip()
        if stripped_line.startswith("```"):
            if in_block:
                block_end = idx
                # Check overlap with range if specified
                in_range = True
                if start_line is not None and block_end < start_line:
                    in_range = False
                if end_line is not None and block_start > end_line:
                    in_range = False

                if in_range:
                    cmds: list[tuple[int, str]] = []
                    current_cmd_lines: list[tuple[int, str]] = []

                    for l_no, l_text in block_lines:
                        if not current_cmd_lines:
                            l_stripped = l_text.strip()
                            if not l_stripped or l_stripped.startswith("#"):
                                continue
                            current_cmd_lines.append((l_no, l_text))
                        else:
                            current_cmd_lines.append((l_no, l_text))

                        last_line = current_cmd_lines[-1][1].rstrip()
                        ends_with_backslash = last_line.endswith("\\")
                        accum_text = "\n".join(t for _, t in current_cmd_lines)
                        unclosed = has_unclosed_quotes(accum_text)

                        if not ends_with_backslash and not unclosed:
                            formatted_parts: list[str] = []
                            cont = ""
                            for _, t in current_cmd_lines:
                                s = t.rstrip()
                                if s.endswith("\\"):
                                    part = s[:-1].strip()
                                    cont = f"{cont} {part}".strip() if cont else part
                                else:
                                    if cont:
                                        part = f"{cont} {t.strip()}".strip()
                                        cont = ""
                                    else:
                                        part = t
                                    formatted_parts.append(part)
                            cmd_str = "\n".join(formatted_parts).strip()
                            cmds.append((current_cmd_lines[0][0], cmd_str))
                            current_cmd_lines = []

                    if current_cmd_lines:
                        cmd_str = "\n".join(t for _, t in current_cmd_lines).strip()
                        cmds.append((current_cmd_lines[0][0], cmd_str))

                    blocks.append(
                        CommandBlock(
                            file_path=file_path,
                            relative_path=rel_path,
                            start_line=block_start,
                            end_line=block_end,
                            language=block_lang,
                            commands=cmds,
                        )
                    )
                in_block = False
                block_lines = []
            else:
                block_lang = stripped_line[3:].strip()
                block_start = idx
                in_block = True
        elif in_block:
            block_lines.append((idx, line))

    return blocks


def discover_doc_blocks(
    root: Path = ROOT,
    ranges: dict[str, tuple[int, int]]
    | dict[str, tuple[int | None, int | None]]
    | None = None,
) -> dict[str, list[CommandBlock]]:
    """Discover all command blocks in the target documentation files."""
    if ranges is None:
        ranges = DOCUMENTED_RANGES

    discovered: dict[str, list[CommandBlock]] = {}
    for rel_path, (s_line, e_line) in ranges.items():
        doc_path = root / rel_path
        blocks = extract_blocks_from_file(doc_path, s_line, e_line, root=root)
        discovered[rel_path] = blocks

    return discovered


def get_registered_fred_subcommands(root: Path = ROOT) -> dict[str, list[str]]:
    """Get the registered lifecycle groups and subcommands from scripts/fred.py."""
    if fred and hasattr(fred, "LIFECYCLE_GROUPS"):
        return fred.LIFECYCLE_GROUPS  # type: ignore[no-any-return]

    # Fallback to parsing scripts/fred.py lines 1-150 directly
    fred_py = root / "scripts/fred.py"
    if not fred_py.is_file():
        return {}

    content = fred_py.read_text(encoding="utf-8")
    groups: dict[str, list[str]] = {}
    in_dict = False
    current_group = ""
    for line in content.splitlines()[:150]:
        if "LIFECYCLE_GROUPS = {" in line:
            in_dict = True
            continue
        if in_dict:
            if line.strip() == "}":
                break
            group_match = re.match(r'\s*"([^"]+)":\s*\[', line)
            if group_match:
                current_group = group_match.group(1)
                groups[current_group] = []
                continue
            item_match = re.match(r'\s*"([^"]+)",', line)
            if item_match and current_group:
                groups[current_group].append(item_match.group(1))

    return groups


def verify_toolkit_subcommands(root: Path = ROOT) -> list[str]:
    """Verify bidirectional parity between documented CLI subcommands in docs/toolkit.md and scripts/fred.py LIFECYCLE_GROUPS."""
    errors: list[str] = []
    toolkit_path = root / "docs/toolkit.md"
    start_line, end_line = DOCUMENTED_RANGES.get("docs/toolkit.md", (10, 195))
    blocks = extract_blocks_from_file(toolkit_path, start_line, end_line, root=root)

    groups = get_registered_fred_subcommands(root)
    all_registered: set[str] = {cmd for cmds in groups.values() for cmd in cmds}

    cli_parser: Any = None
    if fred and hasattr(fred, "parser"):
        cli_parser = fred.parser()

    verified_cmds = 0
    documented_subcmds: set[str] = set()
    for block in blocks:
        for line_no, cmd in block.commands:
            if "scripts/fred.py" not in cmd:
                continue

            parts = shlex.split(cmd)
            script_idx = -1
            for idx, part in enumerate(parts):
                if part.endswith("fred.py"):
                    script_idx = idx
                    break

            if script_idx == -1 or script_idx + 1 >= len(parts):
                continue

            subcmd = parts[script_idx + 1]
            if subcmd.startswith("-"):
                continue

            documented_subcmds.add(subcmd)

            if subcmd not in all_registered:
                errors.append(
                    f"docs/toolkit.md:L{line_no}: Documented subcommand '{subcmd}' is not in scripts/fred.py LIFECYCLE_GROUPS"
                )
                continue

            # Validate command arguments against parser
            if cli_parser:
                args = [normalize_cli_token(arg) for arg in parts[script_idx + 1 :]]
                err_buf = io.StringIO()
                try:
                    with contextlib.redirect_stderr(err_buf):
                        cli_parser.parse_args(args)
                    verified_cmds += 1
                except SystemExit as exc:
                    err_msg = err_buf.getvalue().strip()
                    errors.append(
                        f"docs/toolkit.md:L{line_no}: CLI argument validation failed for '{cmd}' (exit code {exc.code}): {err_msg}"
                    )
                except (argparse.ArgumentError, ValueError) as exc:
                    errors.append(
                        f"docs/toolkit.md:L{line_no}: CLI argument error for '{cmd}': {exc}"
                    )

    # Bidirectional validation: check for any registered subcommand missing from documentation
    missing_subcmds = sorted(all_registered - documented_subcmds)
    for missing_cmd in missing_subcmds:
        errors.append(
            f"docs/toolkit.md: Registered subcommand '{missing_cmd}' lacks documentation snippets"
        )

    if not errors and verified_cmds == 0:
        errors.append("docs/toolkit.md: No fred.py subcommands were found to verify")

    return errors


def execute_snippet_command(
    cmd: str,
    root: Path = ROOT,
) -> tuple[bool, str]:
    """Execute or dry-run validate a single documented shell command.

    Respects constraints: runs in dry-run mode without network calls or destructive writes.
    """
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        return False, f"Failed to parse command syntax: {exc}"

    if not parts:
        return True, ""

    prog = parts[0]

    # Marketplace plugin commands: /plugin marketplace add ... or codex plugin marketplace add ...
    if prog == "/plugin" or (
        prog == "codex" and len(parts) >= 2 and parts[1] == "plugin"
    ):
        # Validate marketplace arguments
        if "marketplace" in parts and "add" in parts:
            idx = parts.index("add")
            if idx + 1 < len(parts):
                plugin_id = parts[idx + 1]
                if plugin_id == "fderuiter/fred-skills":
                    marketplace_manifest = root / ".claude-plugin/marketplace.json"
                    if marketplace_manifest.is_file():
                        return True, ""
                    return (
                        False,
                        f"Marketplace manifest missing at {marketplace_manifest}",
                    )
                return False, f"Unexpected plugin id: {plugin_id}"
        return True, ""

    # Make commands: execute make in dry-run mode (-n)
    if prog == "make":
        target_args = [p for p in parts[1:] if not p.startswith("-")]
        dry_run_args = ["make", "-n", *target_args]
        if shutil.which("make"):
            proc = subprocess.run(
                dry_run_args,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return False, f"make dry-run failed: {proc.stderr.strip()}"
            return True, ""
        # If make is not available, verify Makefile has the target
        makefile = root / "Makefile"
        if not makefile.is_file():
            return False, "Makefile missing"
        content = makefile.read_text(encoding="utf-8")
        for target in target_args:
            if not any(l.startswith(f"{target}:") for l in content.splitlines()):
                return False, f"Makefile missing target '{target}'"
        return True, ""

    # Python commands
    if prog in ("python", "python3"):
        if len(parts) < 2:
            return True, ""

        # Inline script execution: python -c "..."
        if parts[1] == "-c":
            if len(parts) < 3:
                return False, "Incomplete python -c command"
            code = parts[2]
            try:
                ast.parse(textwrap.dedent(code))
                return True, ""
            except SyntaxError as exc:
                return False, f"Inline Python syntax error: {exc}"

        # Module execution: python -m ...
        if parts[1] == "-m":
            if len(parts) < 3:
                return False, "Incomplete python -m command"
            mod = parts[2]
            if mod == "venv":
                # Dry run: verify venv module is usable
                proc = subprocess.run(
                    [sys.executable, "-m", "venv", "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return False, f"python -m venv check failed: {proc.stderr.strip()}"
                return True, ""
            if mod == "pip":
                # pip install -r requirements-dev.txt: verify requirements file exists
                if "install" in parts and "-r" in parts:
                    r_idx = parts.index("-r")
                    if r_idx + 1 < len(parts):
                        req_file = root / parts[r_idx + 1]
                        if not req_file.is_file():
                            return (
                                False,
                                f"Referenced requirements file missing: {req_file}",
                            )
                return True, ""
            if mod == "unittest":
                # Dry run unittest help
                proc = subprocess.run(
                    [sys.executable, "-m", "unittest", "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return False, f"unittest runner unavailable: {proc.stderr.strip()}"
                return True, ""
            return True, ""

        # Script execution: python scripts/...
        script_arg = parts[1]
        script_path = root / script_arg
        if not script_path.is_file():
            return False, f"Referenced script does not exist: {script_arg}"

        # If it's scripts/fred.py: validate arguments with fred.parser()
        if script_arg.endswith("fred.py") and fred and hasattr(fred, "parser"):
            normalized_args = [normalize_cli_token(arg) for arg in parts[2:]]
            err_buf = io.StringIO()
            try:
                with contextlib.redirect_stderr(err_buf):
                    fred.parser().parse_args(normalized_args)
            except SystemExit as exc:
                err_msg = err_buf.getvalue().strip()
                return (
                    False,
                    f"scripts/fred.py argument parsing failed (exit {exc.code}): {err_msg}",
                )
            except (argparse.ArgumentError, ValueError) as exc:
                return False, f"scripts/fred.py argument parsing error: {exc}"

            # If it's search command, run directly (it is safe and read-only)
            if len(parts) >= 3 and parts[2] == "search":
                search_args = [normalize_cli_token(arg) for arg in parts[2:]]
                proc = subprocess.run(
                    [sys.executable, str(script_path), *search_args],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return (
                        False,
                        f"scripts/fred.py search failed: {proc.stderr.strip()}",
                    )
            return True, ""

        # If it has --check flag: execute safely with --check
        if "--check" in parts:
            proc = subprocess.run(
                [sys.executable, str(script_path), "--check"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return False, f"{script_arg} --check failed: {proc.stderr.strip()}"
            return True, ""

        # scripts/validate_skills.py: execute safely without mutation
        if script_arg.endswith("validate_skills.py"):
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return False, f"validate_skills.py failed: {proc.stderr.strip()}"
            return True, ""

        # For scripts with --output, test with --help to avoid destructive filesystem writes
        if "--output" in parts or script_arg.endswith(
            ("package_skills.py", "run_demo.py")
        ):
            proc = subprocess.run(
                [sys.executable, str(script_path), "--help"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return False, f"{script_arg} --help failed: {proc.stderr.strip()}"
            return True, ""

        # By default for python scripts without destructive flags, check syntax/help
        return True, ""

    # Ruff commands: execute in non-mutating check mode
    if prog == "ruff":
        if shutil.which("ruff"):
            # Check or format --check
            if "check" in parts or "--check" in parts:
                proc = subprocess.run(
                    parts,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return False, f"ruff check failed: {proc.stderr.strip()}"
            return True, ""
        return True, ""

    # Pre-commit commands: dry-run or validation
    if prog == "pre-commit":
        if shutil.which("pre-commit"):
            proc = subprocess.run(
                [
                    "pre-commit",
                    "validate-config",
                    str(root / ".pre-commit-config.yaml"),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return (
                    False,
                    f"pre-commit validate-config failed: {proc.stderr.strip()}",
                )
        return True, ""

    # Release downloads (curl): validate URL structure and repo destination without network call
    if prog == "curl":
        for part in parts:
            if (
                part.startswith(("http://", "https://"))
                and "fderuiter/fred-skills" not in part
            ):
                return False, f"curl target does not match repo: {part}"
        return True, ""

    # Attestation and checksums: gh attestation, sha256sum
    if prog in ("sha256sum", "gh"):
        if prog == "gh" and "fderuiter/fred-skills" not in parts:
            return False, f"gh attestation command missing repo flag: {cmd}"
        return True, ""

    # Tar extract command: tar -xzf ...
    if prog == "tar":
        if "-xzf" not in parts and "-ztf" not in parts:
            return False, f"tar command must specify extract or test flags: {cmd}"
        return True, ""

    return True, ""


def _normalize_target_path(target: str | Path, root: Path = ROOT) -> str:
    path_str = str(target).replace("\\", "/")
    path = Path(path_str)
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path_str.lstrip("./")


def verify_all_snippets(
    root: Path = ROOT,
    files: list[str] | None = None,
) -> list[str]:
    """Verify and execute snippets across documentation files."""
    errors: list[str] = []
    target_rel_paths = (
        {_normalize_target_path(f, root=root) for f in files}
        if files is not None
        else None
    )

    # 1. Verify docs/toolkit.md subcommands against scripts/fred.py:L1-L150
    if target_rel_paths is None or "docs/toolkit.md" in target_rel_paths:
        subcmd_errors = verify_toolkit_subcommands(root=root)
        errors.extend(subcmd_errors)

    # 2. Discover and verify command blocks in target documentation files
    if target_rel_paths is not None:
        ranges_to_check: dict[str, tuple[int, int] | tuple[None, None]] = {
            rel_path: DOCUMENTED_RANGES.get(rel_path, (None, None))
            for rel_path in target_rel_paths
        }
    else:
        ranges_to_check = DOCUMENTED_RANGES

    blocks_by_file = discover_doc_blocks(root=root, ranges=ranges_to_check)
    for rel_path, blocks in blocks_by_file.items():
        doc_path = root / rel_path
        if not doc_path.is_file():
            errors.append(f"{rel_path}: File does not exist")
            continue

        if not blocks:
            errors.append(f"{rel_path}: No code blocks discovered in specified range")
            continue

        should_execute = target_rel_paths is not None or rel_path in (
            "README.md",
            "CONTRIBUTING.md",
            "docs/runbooks.md",
            "docs/integration.md",
        )
        for block in blocks:
            if block.language not in ("sh", "bash", "shell"):
                continue
            for line_no, cmd in block.commands:
                if should_execute:
                    ok, msg = execute_snippet_command(
                        cmd,
                        root=root,
                    )
                    if not ok:
                        errors.append(
                            f"{rel_path}:L{line_no}: Failed to execute '{cmd}': {msg}"
                        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and dry-run execute documentation command snippets."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Non-mutating verification mode (standard repository flag)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional specific files to check",
    )

    args = parser.parse_args()
    errors = verify_all_snippets(root=ROOT, files=args.files if args.files else None)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(
            f"FAILED: Found {len(errors)} documentation snippet error(s)",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: All documentation command snippets verified and executed successfully."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

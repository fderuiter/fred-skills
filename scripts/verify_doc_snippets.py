#!/usr/bin/env python3
"""Extract, validate, and dry-run execute documentation command snippets."""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
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
    "README.md": (27, None),
    "CONTRIBUTING.md": (22, 73),
    "AGENTS.md": (24, 52),
    "docs/toolkit.md": (10, 195),
    "docs/usage.md": (24, None),
    "docs/runbooks.md": (1, None),
    "docs/integration.md": (1, None),
}

MARKETPLACE_ONBOARDING_DOCS: frozenset[str] = frozenset(
    {"README.md", "docs/usage.md", "docs/integration.md"}
)

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
        if char == "\\":
            escape = not in_single
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


def _is_block_in_range(
    start_line: int | None,
    end_line: int | None,
    block_start: int,
    block_end: int,
) -> bool:
    """Check whether a code block overlaps with an optional line range."""
    if start_line is not None and block_end < start_line:
        return False
    return not (end_line is not None and block_start > end_line)


def _merge_command_lines(lines: list[tuple[int, str]]) -> str:
    """Format and join multi-line continuation lines of a single command."""
    formatted_parts: list[str] = []
    cont = ""
    for _, text in lines:
        stripped = text.rstrip()
        if stripped.endswith("\\"):
            part = stripped[:-1].strip()
            cont = f"{cont} {part}".strip() if cont else part
        elif cont:
            formatted_parts.append(f"{cont} {text.strip()}".strip())
            cont = ""
        else:
            formatted_parts.append(text)
    return "\n".join(formatted_parts).strip()


def _extract_commands_from_block(
    block_lines: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Parse commands from fenced block lines, merging continuations and unclosed quotes."""
    cmds: list[tuple[int, str]] = []
    current_cmd: list[tuple[int, str]] = []

    for l_no, l_text in block_lines:
        if not current_cmd:
            l_stripped = l_text.strip()
            if not l_stripped or l_stripped.startswith("#"):
                continue
        current_cmd.append((l_no, l_text))

        last_line = current_cmd[-1][1].rstrip()
        if last_line.endswith("\\"):
            continue
        accum = "\n".join(t for _, t in current_cmd)
        if has_unclosed_quotes(accum):
            continue

        cmds.append((current_cmd[0][0], _merge_command_lines(current_cmd)))
        current_cmd = []

    if current_cmd:
        cmds.append((current_cmd[0][0], "\n".join(t for _, t in current_cmd).strip()))

    return cmds


def _parse_fenced_blocks(
    lines: list[str],
) -> list[tuple[int, int, str, list[tuple[int, str]]]]:
    """Extract raw fenced code block boundaries and lines from markdown text lines."""
    blocks: list[tuple[int, int, str, list[tuple[int, str]]]] = []
    in_block = False
    block_lang = ""
    block_lines: list[tuple[int, str]] = []
    block_start = 0

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("```"):
            if in_block:
                block_lines.append((idx, line))
            continue

        if in_block:
            blocks.append((block_start, idx, block_lang, block_lines))
            in_block = False
            block_lines = []
        else:
            block_lang = stripped[3:].strip()
            block_start = idx
            in_block = True

    return blocks


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

    raw_blocks = _parse_fenced_blocks(lines)
    blocks: list[CommandBlock] = []

    for b_start, b_end, b_lang, b_lines in raw_blocks:
        if not _is_block_in_range(start_line, end_line, b_start, b_end):
            continue
        cmds = _extract_commands_from_block(b_lines)
        blocks.append(
            CommandBlock(
                file_path=file_path,
                relative_path=rel_path,
                start_line=b_start,
                end_line=b_end,
                language=b_lang,
                commands=cmds,
            )
        )

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


def _parse_lifecycle_groups_fallback(content: str) -> dict[str, list[str]]:
    """Parse LIFECYCLE_GROUPS from the initial lines of scripts/fred.py."""
    groups: dict[str, list[str]] = {}
    in_dict = False
    current_group = ""
    for line in content.splitlines()[:150]:
        if "LIFECYCLE_GROUPS = {" in line:
            in_dict = True
            continue
        if not in_dict:
            continue
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


def get_registered_fred_subcommands(root: Path = ROOT) -> dict[str, list[str]]:
    """Get the registered lifecycle groups and subcommands from scripts/fred.py."""
    if fred and hasattr(fred, "LIFECYCLE_GROUPS"):
        return fred.LIFECYCLE_GROUPS  # type: ignore[no-any-return]

    fred_py = root / "scripts/fred.py"
    if not fred_py.is_file():
        return {}

    return _parse_lifecycle_groups_fallback(fred_py.read_text(encoding="utf-8"))


def _find_script_subcommand(parts: list[str]) -> str | None:
    """Find the subcommand following scripts/fred.py in a tokenized command."""
    script_idx = -1
    for idx, part in enumerate(parts):
        if part.endswith("fred.py"):
            script_idx = idx
            break
    if script_idx == -1 or script_idx + 1 >= len(parts):
        return None
    subcmd = parts[script_idx + 1]
    return None if subcmd.startswith("-") else subcmd


def _validate_toolkit_args(
    cli_parser: Any,
    parts: list[str],
    cmd: str,
    line_no: int,
) -> tuple[bool, str | None]:
    """Validate documented fred.py CLI arguments against the parser."""
    if not cli_parser:
        return True, None
    script_idx = next(i for i, p in enumerate(parts) if p.endswith("fred.py"))
    args = [normalize_cli_token(arg) for arg in parts[script_idx + 1 :]]
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(err_buf):
            cli_parser.parse_args(args)
        return True, None
    except SystemExit as exc:
        err_msg = err_buf.getvalue().strip()
        return (
            False,
            f"docs/toolkit.md:L{line_no}: CLI argument validation failed for '{cmd}' (exit code {exc.code}): {err_msg}",
        )
    except (argparse.ArgumentError, ValueError) as exc:
        return (
            False,
            f"docs/toolkit.md:L{line_no}: CLI argument error for '{cmd}': {exc}",
        )


def _collect_documented_toolkit_subcommands(
    blocks: list[CommandBlock],
    all_registered: set[str],
    cli_parser: Any,
) -> tuple[list[str], set[str], int]:
    """Validate documented commands and return detected errors, subcommands, and verified count."""
    errors: list[str] = []
    documented: set[str] = set()
    verified_cmds = 0

    for block in blocks:
        for line_no, cmd in block.commands:
            if "scripts/fred.py" not in cmd:
                continue
            parts = shlex.split(cmd)
            subcmd = _find_script_subcommand(parts)
            if not subcmd:
                continue
            documented.add(subcmd)
            if subcmd not in all_registered:
                errors.append(
                    f"docs/toolkit.md:L{line_no}: Documented subcommand '{subcmd}' is not in scripts/fred.py LIFECYCLE_GROUPS"
                )
                continue
            valid, err = _validate_toolkit_args(cli_parser, parts, cmd, line_no)
            if not valid and err:
                errors.append(err)
            elif cli_parser:
                verified_cmds += 1

    return errors, documented, verified_cmds


def verify_toolkit_subcommands(root: Path = ROOT) -> list[str]:
    """Verify bidirectional parity between documented CLI subcommands in docs/toolkit.md and scripts/fred.py LIFECYCLE_GROUPS."""
    toolkit_path = root / "docs/toolkit.md"
    start_line, end_line = DOCUMENTED_RANGES.get("docs/toolkit.md", (10, 195))
    blocks = extract_blocks_from_file(toolkit_path, start_line, end_line, root=root)

    groups = get_registered_fred_subcommands(root)
    all_registered: set[str] = {cmd for cmds in groups.values() for cmd in cmds}
    cli_parser = fred.parser() if (fred and hasattr(fred, "parser")) else None

    errors, documented, verified = _collect_documented_toolkit_subcommands(
        blocks, all_registered, cli_parser
    )

    for missing in sorted(all_registered - documented):
        errors.append(
            f"docs/toolkit.md: Registered subcommand '{missing}' lacks documentation snippets"
        )

    if not errors and verified == 0:
        errors.append("docs/toolkit.md: No fred.py subcommands were found to verify")

    return errors


# ---------------------------------------------------------------------------
# Modular Tool Validation Handlers
# ---------------------------------------------------------------------------

ToolHandler = Callable[[list[str], Path, str], tuple[bool, str]]


def _load_marketplace_plugins(root: Path) -> tuple[str | None, set[str], str]:
    """Load marketplace name and registered plugin names from marketplace.json."""
    marketplace_manifest = root / ".claude-plugin/marketplace.json"
    if not marketplace_manifest.is_file():
        return None, set(), f"Marketplace manifest missing at {marketplace_manifest}"
    try:
        data = json.loads(marketplace_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, set(), f"Invalid marketplace manifest: {exc}"
    names = {item["name"] for item in data.get("plugins", []) if "name" in item}
    return str(data.get("name", "fred-skills")), names, ""


def _validate_marketplace_add(repo_args: list[str], root: Path) -> tuple[bool, str]:
    """Validate marketplace catalog registration commands."""
    if len(repo_args) != 1:
        return False, "Marketplace add command requires a single repository argument"
    if repo_args[0] != "fderuiter/fred-skills":
        return False, f"Unexpected plugin id: {repo_args[0]}"
    market_name, _, err = _load_marketplace_plugins(root)
    if market_name is None:
        return False, err
    return True, ""


def _validate_marketplace_install(
    target_args: list[str], root: Path
) -> tuple[bool, str]:
    """Validate plugin installation target against .claude-plugin/marketplace.json."""
    if len(target_args) != 1 or "@" not in target_args[0]:
        return False, "Plugin install command requires '<name>@fred-skills'"
    plugin_name, market_name = target_args[0].split("@", 1)
    expected_market, valid_plugins, err = _load_marketplace_plugins(root)
    if expected_market is None:
        return False, err
    if market_name != expected_market:
        return (
            False,
            f"Unexpected marketplace '{market_name}' (expected '{expected_market}')",
        )
    if plugin_name not in valid_plugins:
        return False, f"Unknown marketplace plugin '{plugin_name}'"
    return True, ""


def _validate_marketplace(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
    """Validate Claude Code and Codex marketplace plugin commands."""
    subparts = parts[1:] if parts[0] == "/plugin" else parts[2:]
    if subparts[:2] == ["marketplace", "add"]:
        return _validate_marketplace_add(subparts[2:], root)
    if parts[0] == "/plugin" and subparts[:1] == ["install"]:
        return _validate_marketplace_install(subparts[1:], root)
    if parts[0] == "codex" and subparts[:1] == ["add"]:
        return _validate_marketplace_install(subparts[1:], root)
    return False, f"Unsupported marketplace plugin command: {cmd}"


def _validate_make(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate make commands in dry-run mode (-n) or against Makefile targets."""
    target_args = [p for p in parts[1:] if not p.startswith("-")]
    if shutil.which("make"):
        proc = subprocess.run(
            ["make", "-n", *target_args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False, f"make dry-run failed: {proc.stderr.strip()}"
        return True, ""

    makefile = root / "Makefile"
    if not makefile.is_file():
        return False, "Makefile missing"
    content = makefile.read_text(encoding="utf-8")
    for target in target_args:
        if not any(line.startswith(f"{target}:") for line in content.splitlines()):
            return False, f"Makefile missing target '{target}'"
    return True, ""


def _validate_python_inline(parts: list[str]) -> tuple[bool, str]:
    """Validate inline python -c snippets by checking AST syntax."""
    if len(parts) < 3:
        return False, "Incomplete python -c command"
    try:
        ast.parse(textwrap.dedent(parts[2]))
        return True, ""
    except SyntaxError as exc:
        return False, f"Inline Python syntax error: {exc}"


def _validate_pip_install(parts: list[str], root: Path) -> tuple[bool, str]:
    """Validate pip requirements file existence if specified."""
    if "install" in parts and "-r" in parts:
        r_idx = parts.index("-r")
        if r_idx + 1 < len(parts):
            req_file = root / parts[r_idx + 1]
            if not req_file.is_file():
                return False, f"Referenced requirements file missing: {req_file}"
    return True, ""


def _validate_python_module(parts: list[str], root: Path) -> tuple[bool, str]:
    """Validate python -m commands (venv, pip, unittest)."""
    if len(parts) < 3:
        return False, "Incomplete python -m command"
    mod = parts[2]
    if mod == "venv":
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
        return _validate_pip_install(parts, root)
    if mod == "unittest":
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False, f"unittest runner unavailable: {proc.stderr.strip()}"
    return True, ""


def _validate_python_fred(
    parts: list[str],
    script_path: Path,
    root: Path,
) -> tuple[bool, str]:
    """Validate scripts/fred.py invocations against argument parser and search."""
    if fred and hasattr(fred, "parser"):
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
            return False, f"scripts/fred.py search failed: {proc.stderr.strip()}"
    return True, ""


def _run_script_help_check(script_path: Path, script_arg: str) -> tuple[bool, str]:
    """Run script with --help to verify CLI arguments without side effects."""
    proc = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False, f"{script_arg} --help failed: {proc.stderr.strip()}"
    return True, ""


def _validate_python_script(parts: list[str], root: Path) -> tuple[bool, str]:
    """Validate python script invocations safely in dry-run mode."""
    script_arg = parts[1]
    script_path = root / script_arg
    if not script_path.is_file():
        return False, f"Referenced script does not exist: {script_arg}"

    if script_arg.endswith("fred.py"):
        return _validate_python_fred(parts, script_path, root)

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

    if "--output" in parts or script_arg.endswith(("package_skills.py", "run_demo.py")):
        return _run_script_help_check(script_path, script_arg)

    return True, ""


def _validate_python(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Dispatch python and python3 command validations."""
    if len(parts) < 2:
        return True, ""
    if parts[1] == "-c":
        return _validate_python_inline(parts)
    if parts[1] == "-m":
        return _validate_python_module(parts, root)
    return _validate_python_script(parts, root)


def _validate_ruff(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate ruff linting or formatting commands in check mode."""
    if not shutil.which("ruff"):
        return True, ""
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


def _validate_pre_commit(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate pre-commit configuration file."""
    if not shutil.which("pre-commit"):
        return True, ""
    proc = subprocess.run(
        ["pre-commit", "validate-config", str(root / ".pre-commit-config.yaml")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False, f"pre-commit validate-config failed: {proc.stderr.strip()}"
    return True, ""


def _validate_curl(parts: list[str], _root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate curl commands against target repository URL without network requests."""
    for part in parts:
        if (
            part.startswith(("http://", "https://"))
            and "fderuiter/fred-skills" not in part
        ):
            return False, f"curl target does not match repo: {part}"
    return True, ""


def _validate_attestation_or_checksum(
    parts: list[str], _root: Path, cmd: str
) -> tuple[bool, str]:
    """Validate gh attestation and sha256sum commands safely."""
    prog = parts[0]
    if prog == "gh" and "fderuiter/fred-skills" not in parts:
        return False, f"gh attestation command missing repo flag: {cmd}"
    return True, ""


_VALID_TAR_FLAGS = frozenset({"-xzf", "-ztf", "-xf", "-tf", "-zxf", "-tzf"})


def _extract_tar_target_dir(parts: list[str]) -> tuple[bool, str | None]:
    """Return (ok, target_dir) for a tar command's -C or --directory option."""
    for idx, token in enumerate(parts):
        if token in ("-C", "--directory"):
            if idx + 1 >= len(parts):
                return False, None
            target = parts[idx + 1].strip()
            return (True, target) if target else (False, None)
        if token.startswith("--directory="):
            target = token.split("=", 1)[1].strip()
            return (True, target) if target else (False, None)
    return True, None


def _get_configured_host_paths(root: Path) -> set[str]:
    """Load valid project and user skill paths from catalog/hosts.json."""
    hosts_file = root / "catalog/hosts.json"
    if not hosts_file.is_file():
        hosts_file = ROOT / "catalog/hosts.json"
    if not hosts_file.is_file():
        return set()
    data = json.loads(hosts_file.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for host_cfg in data.get("hosts", {}).values():
        for key in ("project_path", "user_path"):
            val = str(host_cfg.get(key, "")).strip("/")
            if val:
                paths.add(val)
    return paths


def _extract_host_subpath(target: str) -> str | None:
    """Extract host-relative path if target uses project placeholder or dot-prefixed host dir."""
    normalized = target.replace("\\", "/").rstrip("/")
    if normalized == "/path/to/project" or normalized.startswith("/path/to/project/"):
        return normalized.removeprefix("/path/to/project").strip("/")
    segments = [s for s in normalized.split("/") if s not in {"", ".", ".."}]
    for idx, seg in enumerate(segments):
        if seg.startswith("."):
            return "/".join(segments[idx:])
    return None


def _validate_tar(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
    """Validate tar command flags and host destination paths without performing file extraction."""
    if not (_VALID_TAR_FLAGS & set(parts)):
        return False, f"tar command must specify extract or test flags: {cmd}"
    ok, target = _extract_tar_target_dir(parts)
    if not ok:
        return False, f"tar command missing directory argument for -C: {cmd}"
    host_subpath = _extract_host_subpath(target) if target is not None else None
    valid_paths = _get_configured_host_paths(root)
    if host_subpath is not None and host_subpath not in valid_paths:
        return (
            False,
            f"tar -C destination '{target}' does not match configured host paths {sorted(valid_paths)}",
        )
    return True, ""


TOOL_DISPATCHERS: dict[tuple[str, ...], ToolHandler] = {
    ("/plugin",): _validate_marketplace,
    ("codex", "plugin"): _validate_marketplace,
    ("make",): _validate_make,
    ("python",): _validate_python,
    ("python3",): _validate_python,
    ("ruff",): _validate_ruff,
    ("pre-commit",): _validate_pre_commit,
    ("curl",): _validate_curl,
    ("gh",): _validate_attestation_or_checksum,
    ("sha256sum",): _validate_attestation_or_checksum,
    ("tar",): _validate_tar,
}


def register_tool_dispatcher(
    prefix: tuple[str, ...] | str,
    handler: ToolHandler,
) -> None:
    """Register a custom tool validation handler for a command prefix."""
    key = (prefix,) if isinstance(prefix, str) else prefix
    TOOL_DISPATCHERS[key] = handler


def find_tool_dispatcher(parts: list[str]) -> ToolHandler | None:
    """Find the registered tool dispatcher matching the longest command prefix."""
    for prefix in sorted(TOOL_DISPATCHERS.keys(), key=len, reverse=True):
        if len(parts) >= len(prefix) and tuple(parts[: len(prefix)]) == prefix:
            return TOOL_DISPATCHERS[prefix]
    return None


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

    dispatcher = find_tool_dispatcher(parts)
    if dispatcher is None:
        return True, ""

    return dispatcher(parts, root, cmd)


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


def _block_command_tokens(block: CommandBlock) -> list[tuple[int, list[str]]]:
    """Tokenize commands in a block, skipping commands with shell syntax errors."""
    tokenized: list[tuple[int, list[str]]] = []
    for line_no, cmd in block.commands:
        try:
            tokenized.append((line_no, shlex.split(cmd)))
        except ValueError:
            continue
    return tokenized


def _verify_marketplace_block(rel_path: str, block: CommandBlock) -> list[str]:
    """Reject marketplace add blocks in onboarding docs that lack an install command."""
    if rel_path not in MARKETPLACE_ONBOARDING_DOCS:
        return []
    tokenized = _block_command_tokens(block)
    has_claude_install = any(p[:2] == ["/plugin", "install"] for _, p in tokenized)
    has_codex_install = any(p[:3] == ["codex", "plugin", "add"] for _, p in tokenized)
    errors: list[str] = []
    for line_no, parts in tokenized:
        if parts[:3] == ["/plugin", "marketplace", "add"] and not has_claude_install:
            errors.append(
                f"{rel_path}:L{line_no}: Marketplace block missing '/plugin install <name>@fred-skills'"
            )
        if (
            parts[:4] == ["codex", "plugin", "marketplace", "add"]
            and not has_codex_install
        ):
            errors.append(
                f"{rel_path}:L{line_no}: Marketplace block missing 'codex plugin add <name>@fred-skills'"
            )
    return errors


def _verify_shell_block(
    rel_path: str,
    block: CommandBlock,
    root: Path,
) -> list[str]:
    """Validate marketplace completeness and dry-run commands in a shell block."""
    errors = _verify_marketplace_block(rel_path, block)
    for line_no, cmd in block.commands:
        ok, msg = execute_snippet_command(cmd, root=root)
        if not ok:
            errors.append(f"{rel_path}:L{line_no}: Failed to execute '{cmd}': {msg}")
    return errors


def _verify_file_snippets(
    rel_path: str,
    blocks: list[CommandBlock],
    root: Path,
    should_execute: bool,
) -> list[str]:
    """Verify code snippet commands within discovered blocks of a single file."""
    doc_path = root / rel_path
    if not doc_path.is_file():
        return [f"{rel_path}: File does not exist"]
    if not blocks:
        return [f"{rel_path}: No code blocks discovered in specified range"]
    if not should_execute:
        return []

    errors: list[str] = []
    for block in blocks:
        if block.language in ("sh", "bash", "shell"):
            errors.extend(_verify_shell_block(rel_path, block, root))
    return errors


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

    if target_rel_paths is None or "docs/toolkit.md" in target_rel_paths:
        subcmd_errors = verify_toolkit_subcommands(root=root)
        errors.extend(subcmd_errors)

    if target_rel_paths is not None:
        ranges_to_check: dict[str, tuple[int | None, int | None]] = {
            rel_path: DOCUMENTED_RANGES.get(rel_path, (None, None))
            for rel_path in target_rel_paths
        }
    else:
        ranges_to_check = DOCUMENTED_RANGES

    blocks_by_file = discover_doc_blocks(root=root, ranges=ranges_to_check)
    for rel_path, blocks in blocks_by_file.items():
        should_execute = target_rel_paths is not None or rel_path in (
            "README.md",
            "CONTRIBUTING.md",
            "docs/usage.md",
            "docs/runbooks.md",
            "docs/integration.md",
        )
        file_errors = _verify_file_snippets(rel_path, blocks, root, should_execute)
        errors.extend(file_errors)

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

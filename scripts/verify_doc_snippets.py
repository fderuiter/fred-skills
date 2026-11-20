#!/usr/bin/env python3
"""Extract, validate, and dry-run execute documentation command snippets."""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))

from _doc_invariants import (
    CORE_INVARIANT_DOCS,
    DocCountExpectations,
    build_prose_count_rules,
    discover_untagged_release_notes,
    find_prose_count_mismatches,
)
from _markdown_utils import match_atx_heading, scan_markdown_fences
from skill_tools import FredCliError

fred: ModuleType | None
try:
    import fred as _fred_module

    fred = _fred_module
except ImportError:
    fred = None

audit_to_sarif_mod: ModuleType | None
try:
    import audit_to_sarif as _audit_to_sarif_module

    audit_to_sarif_mod = _audit_to_sarif_module
except ImportError:
    audit_to_sarif_mod = None


def _discover_default_doc_ranges(
    root: Path = ROOT,
) -> dict[str, tuple[int | None, int | None]]:
    """Discover all root Markdown guides and every Markdown file under docs/."""
    ranges: dict[str, tuple[int | None, int | None]] = {}
    for md_path in sorted(root.glob("*.md")):
        if md_path.is_file():
            ranges[md_path.name] = (1, None)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for md_path in sorted(docs_dir.rglob("*.md")):
            if md_path.is_file():
                ranges[md_path.relative_to(root).as_posix()] = (1, None)
    return ranges


# Line ranges for acceptance criteria verification
DOCUMENTED_RANGES: dict[str, tuple[int | None, int | None]] = (
    _discover_default_doc_ranges(ROOT)
)

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
    "profile": "web-app",
    "case": "concurrency-a",
    "pack": "starter",
    "task": "review-pr",
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
    start_line: int
    end_line: int
    language: str
    commands: list[tuple[int, str]]  # (line_no, command_str)
    content: str = ""
    headings: tuple[str, ...] = ()


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


_YAML_BLOCK_RUN_RE = re.compile(r"^\s*(?:-\s+)?run:\s*[|>][-+]?\s*(?:#.*)?$")
_YAML_INLINE_RUN_RE = re.compile(r"^\s*(?:-\s+)?run:\s+(.+?)\s*$")


def _unquote_yaml_scalar(value: str) -> str:
    """Strip matching outer YAML quotes from a single-line scalar run command."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def _parse_yaml_run_header(text: str) -> tuple[str, int, str]:
    """Classify a YAML line as block run, inline run, or non-run line."""
    if _YAML_BLOCK_RUN_RE.match(text):
        return "block", len(text) - len(text.lstrip()), ""
    inline_match = _YAML_INLINE_RUN_RE.match(text)
    if inline_match:
        return "inline", 0, _unquote_yaml_scalar(inline_match.group(1))
    return "none", 0, ""


def _is_yaml_step_continuation(text: str, base_indent: int) -> bool:
    """Return True if a line remains inside an indented YAML block scalar."""
    if not text.strip():
        return True
    return (len(text) - len(text.lstrip())) > base_indent


def _collect_yaml_block_step(
    b_lines: list[tuple[int, str]],
    start_idx: int,
    base_indent: int,
) -> tuple[list[tuple[int, str]], int]:
    """Collect indented lines belonging to a YAML block-scalar run step."""
    step_lines: list[tuple[int, str]] = []
    idx = start_idx
    while idx < len(b_lines) and _is_yaml_step_continuation(
        b_lines[idx][1], base_indent
    ):
        step_lines.append(b_lines[idx])
        idx += 1
    return step_lines, idx


def _extract_yaml_run_commands(
    b_lines: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Extract shell commands and exact file line numbers from YAML workflow run steps."""
    cmds: list[tuple[int, str]] = []
    idx = 0
    while idx < len(b_lines):
        line_no, text = b_lines[idx]
        kind, indent, inline_cmd = _parse_yaml_run_header(text)
        if kind == "block":
            step_lines, idx = _collect_yaml_block_step(b_lines, idx + 1, indent)
            cmds.extend(_extract_commands_from_block(step_lines))
        elif kind == "inline":
            cmds.extend(_extract_commands_from_block([(line_no, inline_cmd)]))
            idx += 1
        else:
            idx += 1
    return cmds


def _extract_block_commands(
    b_lang: str,
    b_lines: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Extract commands from a fenced code block according to its language tag."""
    if b_lang in ("yaml", "yml"):
        return _extract_yaml_run_commands(b_lines)
    return _extract_commands_from_block(b_lines)


def _parse_fenced_blocks(
    lines: list[str],
) -> list[tuple[int, int, str, list[tuple[int, str]]]]:
    """Extract raw fenced code block boundaries and lines from markdown text lines."""
    _, scanned = scan_markdown_fences(lines)
    return [
        (block.start_line, block.end_line, block.fence.info.strip(), list(block.lines))
        for block in scanned
        if block.closed
    ]


_CURSOR_HEADING_RE = re.compile(r"\bcursor\b", re.IGNORECASE)


def _update_heading_stack(active: dict[int, str], line: str) -> None:
    """Update active ATX heading hierarchy when line is a Markdown heading."""
    heading = match_atx_heading(line)
    if heading is None:
        return
    level, title = heading
    for deeper in [k for k in active if k >= level]:
        del active[deeper]
    active[level] = title


def _headings_by_block_start(
    lines: list[str],
    block_ranges: list[tuple[int, int]],
) -> dict[int, tuple[str, ...]]:
    """Map each fenced block start line to its active Markdown heading hierarchy."""
    fenced_lines = {
        ln for b_start, b_end in block_ranges for ln in range(b_start, b_end + 1)
    }
    block_starts = {b_start for b_start, _ in block_ranges}
    active: dict[int, str] = {}
    result: dict[int, tuple[str, ...]] = {}
    for idx, line in enumerate(lines, start=1):
        if idx in block_starts:
            result[idx] = tuple(active[k] for k in sorted(active))
        if idx not in fenced_lines:
            _update_heading_stack(active, line)
    return result


def extract_blocks_from_file(
    file_path: Path,
    start_line: int | None = None,
    end_line: int | None = None,
    root: Path = ROOT,
) -> list[CommandBlock]:
    """Extract fenced code blocks and commands from a markdown file within an optional line range."""
    del root
    if not file_path.is_file():
        return []

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    raw_blocks = _parse_fenced_blocks(lines)
    heading_map = _headings_by_block_start(
        lines, [(b_start, b_end) for b_start, b_end, _, _ in raw_blocks]
    )
    blocks: list[CommandBlock] = []

    for b_start, b_end, b_lang, b_lines in raw_blocks:
        if not _is_block_in_range(start_line, end_line, b_start, b_end):
            continue
        cmds = _extract_block_commands(b_lang, b_lines)
        raw_content = "\n".join(text for _, text in b_lines)
        blocks.append(
            CommandBlock(
                start_line=b_start,
                end_line=b_end,
                language=b_lang,
                commands=cmds,
                content=raw_content,
                headings=heading_map.get(b_start, ()),
            )
        )

    return blocks


def discover_doc_blocks(
    root: Path = ROOT,
    ranges: Mapping[str, tuple[int | None, int | None]] | None = None,
) -> dict[str, list[CommandBlock]]:
    """Discover all command blocks in the target documentation files."""
    if ranges is None:
        ranges = (
            DOCUMENTED_RANGES
            if root.resolve() == ROOT.resolve()
            else _discover_default_doc_ranges(root)
        )

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
    if fred is not None and hasattr(fred, "LIFECYCLE_GROUPS"):
        groups: dict[str, list[str]] = fred.LIFECYCLE_GROUPS
        return groups

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
    root: Path = ROOT,
) -> tuple[bool, str | None]:
    """Validate documented fred.py CLI arguments against parser and catalogs."""
    script_idx = next(i for i, p in enumerate(parts) if p.endswith("fred.py"))
    raw_args = parts[script_idx + 1 :]
    cat_ok, cat_err = _validate_fred_catalog_flags(raw_args, root)
    if not cat_ok:
        return False, f"docs/toolkit.md:L{line_no}: {cat_err} in '{cmd}'"
    if not cli_parser:
        return True, None
    args = [normalize_cli_token(arg) for arg in raw_args]
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
    root: Path = ROOT,
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
            valid, err = _validate_toolkit_args(
                cli_parser, parts, cmd, line_no, root=root
            )
            if not valid and err:
                errors.append(err)
            elif cli_parser:
                verified_cmds += 1

    return errors, documented, verified_cmds


def verify_toolkit_subcommands(root: Path = ROOT) -> list[str]:
    """Verify bidirectional parity between documented CLI subcommands in docs/toolkit.md and scripts/fred.py LIFECYCLE_GROUPS."""
    toolkit_path = root / "docs/toolkit.md"
    start_line, end_line = DOCUMENTED_RANGES.get("docs/toolkit.md", (1, None))
    blocks = extract_blocks_from_file(toolkit_path, start_line, end_line, root=root)

    groups = get_registered_fred_subcommands(root)
    all_registered: set[str] = {cmd for cmds in groups.values() for cmd in cmds}
    cli_parser = fred.parser() if (fred and hasattr(fred, "parser")) else None

    errors, documented, verified = _collect_documented_toolkit_subcommands(
        blocks, all_registered, cli_parser, root=root
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

_HELP_FLAGS_CACHE: dict[tuple[str, ...], set[str] | None] = {}

MAKE_VALUE_SHORT_FLAGS = {"-C", "-f", "-I", "-j", "-l", "-o", "-O", "-W"}
MAKE_SEPARATE_VALUE_FLAGS = {
    "-C",
    "--directory",
    "-f",
    "--file",
    "--makefile",
    "-I",
    "--include-dir",
    "-o",
    "--old-file",
    "-W",
    "--what-if",
}
MAKE_ALLOWED_FLAGS = {
    "-n",
    "--dry-run",
    "--just-print",
    "--recon",
    "-B",
    "--always-make",
    "-C",
    "--directory",
    "-d",
    "-e",
    "--environment-overrides",
    "-f",
    "--file",
    "--makefile",
    "-i",
    "--ignore-errors",
    "-I",
    "--include-dir",
    "-j",
    "--jobs",
    "-k",
    "--keep-going",
    "-l",
    "--load-average",
    "-o",
    "--old-file",
    "-O",
    "--output-sync",
    "-p",
    "--print-data-base",
    "-q",
    "--question",
    "-r",
    "--no-builtin-rules",
    "-R",
    "--no-builtin-variables",
    "-s",
    "--silent",
    "--quiet",
    "-S",
    "--no-keep-going",
    "--stop",
    "-t",
    "--touch",
    "--trace",
    "-v",
    "--version",
    "-w",
    "--print-directory",
    "--no-print-directory",
    "-W",
    "--what-if",
    "--warn-undefined-variables",
    "-h",
    "--help",
}

PYTHON_INTERPRETER_FLAGS = {
    "-B",
    "-E",
    "-I",
    "-O",
    "-OO",
    "-P",
    "-S",
    "-V",
    "--version",
    "-b",
    "-d",
    "-h",
    "--help",
    "-i",
    "-q",
    "-s",
    "-u",
    "-v",
    "-x",
    "--check-hash-based-pycs",
}

RUFF_SUBCOMMANDS = {
    "check",
    "format",
    "rule",
    "config",
    "linter",
    "clean",
    "version",
    "server",
    "analyze",
    "help",
}
RUFF_TOP_FLAGS = {
    "-h",
    "--help",
    "-V",
    "--version",
    "-v",
    "--verbose",
    "-q",
    "--quiet",
    "-s",
    "--silent",
}
RUFF_FALLBACK_FLAGS = {
    "-h",
    "--help",
    "--check",
    "--diff",
    "--fix",
    "--fix-only",
    "--unsafe-fixes",
    "--show-fixes",
    "--select",
    "--extend-select",
    "--ignore",
    "--config",
    "--target-version",
    "--line-length",
    "--preview",
    "--output-format",
    "--isolated",
    "--no-cache",
    "--statistics",
    "--quiet",
    "-q",
    "--verbose",
    "-v",
    "--silent",
    "-s",
    "--exit-zero",
    "--exit-non-zero-on-fix",
}

PRE_COMMIT_SUBCOMMANDS = {
    "autoupdate",
    "clean",
    "gc",
    "hazmat",
    "init-templatedir",
    "install",
    "install-hooks",
    "migrate-config",
    "run",
    "sample-config",
    "try-repo",
    "uninstall",
    "validate-config",
    "validate-manifest",
    "help",
}
PRE_COMMIT_TOP_FLAGS = {"-h", "--help", "-V", "--version"}
PRE_COMMIT_FALLBACK_FLAGS = {
    "-h",
    "--help",
    "-a",
    "--all-files",
    "--files",
    "-v",
    "--verbose",
    "-c",
    "--config",
    "--color",
    "--show-diff-on-failure",
    "--fail-fast",
    "--hook-stage",
    "--from-ref",
    "--to-ref",
    "-f",
    "--overwrite",
    "--install-hooks",
    "-t",
    "--hook-type",
    "--allow-missing-config",
}

SHA256SUM_ALLOWED_FLAGS = {
    "-b",
    "--binary",
    "-c",
    "--check",
    "--tag",
    "-t",
    "--text",
    "-z",
    "--zero",
    "--ignore-missing",
    "--quiet",
    "--status",
    "--strict",
    "-w",
    "--warn",
    "--help",
    "--version",
}

GH_SUBCOMMANDS = {
    "attestation",
    "release",
    "pr",
    "issue",
    "repo",
    "run",
    "workflow",
    "auth",
    "api",
    "version",
    "help",
}
GH_GLOBAL_FLAGS = {"-R", "--repo", "-h", "--help"}
GH_ATTESTATION_FLAGS = {
    "-R",
    "--repo",
    "-o",
    "--owner",
    "-b",
    "--bundle",
    "--bundle-from-oci",
    "--cert-identity",
    "-i",
    "--cert-identity-regex",
    "--cert-oidc-issuer",
    "--custom-trusted-root",
    "--deny-self-hosted-runners",
    "-d",
    "--digest-alg",
    "--format",
    "--hostname",
    "-q",
    "--jq",
    "--predicate-type",
    "--signer-repo",
    "--signer-workflow",
    "--source-digest",
    "--source-ref",
    "-t",
    "--template",
    "-h",
    "--help",
}
GH_RELEASE_FLAGS = GH_GLOBAL_FLAGS | {
    "-d",
    "--draft",
    "-p",
    "--prerelease",
    "-t",
    "--title",
    "-n",
    "--notes",
    "-F",
    "--notes-file",
    "--generate-notes",
    "--notes-start-tag",
    "--notes-from-tag",
    "--target",
    "--verify-tag",
    "--latest",
    "--discussion-category",
    "-A",
    "--archive",
    "--clobber",
    "-D",
    "--dir",
    "-O",
    "--output",
    "--pattern",
    "--skip-existing",
    "-q",
    "--jq",
    "--json",
    "-w",
    "--web",
    "-L",
    "--limit",
    "--exclude-drafts",
    "--exclude-pre-releases",
    "-y",
    "--yes",
    "--cleanup-tag",
}
GH_ISSUE_FLAGS = GH_GLOBAL_FLAGS | {
    "-t",
    "--title",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-l",
    "--label",
    "-a",
    "--assignee",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "-T",
    "--template",
    "--recover",
    "-w",
    "--web",
    "-s",
    "--state",
    "-L",
    "--limit",
    "-S",
    "--search",
    "-A",
    "--app",
    "--author",
    "--mention",
    "-q",
    "--jq",
    "--json",
    "-c",
    "--comment",
    "-r",
    "--reason",
    "-e",
    "--editor",
}
GH_PR_FLAGS = GH_GLOBAL_FLAGS | {
    "-t",
    "--title",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-B",
    "--base",
    "-H",
    "--head",
    "-d",
    "--draft",
    "-f",
    "--fill",
    "--fill-first",
    "--fill-verbose",
    "-l",
    "--label",
    "-a",
    "--assignee",
    "-r",
    "--reviewer",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "-T",
    "--template",
    "-w",
    "--web",
    "-s",
    "--state",
    "-L",
    "--limit",
    "-S",
    "--search",
    "-c",
    "--comments",
    "-q",
    "--jq",
    "--json",
    "--merge",
    "--squash",
    "--rebase",
    "--auto",
    "--delete-branch",
}
GH_SUBCOMMAND_FLAGS: dict[str, set[str]] = {
    "attestation": GH_ATTESTATION_FLAGS,
    "release": GH_RELEASE_FLAGS,
    "issue": GH_ISSUE_FLAGS,
    "pr": GH_PR_FLAGS,
    "repo": GH_GLOBAL_FLAGS
    | {
        "--clone",
        "--public",
        "--private",
        "--internal",
        "-d",
        "--description",
        "-y",
        "--yes",
        "-q",
        "--jq",
        "--json",
        "-w",
        "--web",
        "-L",
        "--limit",
        "--source",
        "--remote",
    },
    "run": GH_GLOBAL_FLAGS
    | {
        "-b",
        "--branch",
        "-c",
        "--commit",
        "-e",
        "--event",
        "-s",
        "--status",
        "-u",
        "--user",
        "-w",
        "--workflow",
        "-L",
        "--limit",
        "-q",
        "--jq",
        "--json",
        "--log",
        "--log-failed",
        "--exit-status",
        "-i",
        "--interval",
        "-n",
        "--name",
        "-D",
        "--dir",
        "-p",
        "--pattern",
    },
    "workflow": GH_GLOBAL_FLAGS
    | {
        "-a",
        "--all",
        "-L",
        "--limit",
        "-q",
        "--jq",
        "--json",
        "-r",
        "--ref",
        "-f",
        "--raw-field",
        "-F",
        "--field",
        "-w",
        "--web",
        "--yaml",
    },
    "auth": {
        "-h",
        "--help",
        "--hostname",
        "-p",
        "--git-protocol",
        "-s",
        "--scopes",
        "--web",
        "--with-token",
        "--show-token",
        "-u",
        "--user",
    },
    "api": {
        "-X",
        "--method",
        "-F",
        "--field",
        "-f",
        "--raw-field",
        "-H",
        "--header",
        "-i",
        "--include",
        "--input",
        "-q",
        "--jq",
        "--paginate",
        "--slurp",
        "--cache",
        "--hostname",
        "--preview",
        "--silent",
        "-t",
        "--template",
        "--verbose",
        "-h",
        "--help",
    },
    "version": {"-h", "--help"},
    "help": {"-h", "--help"},
}

CURL_FALLBACK_FLAGS = {
    "-L",
    "--location",
    "-O",
    "--remote-name",
    "-o",
    "--output",
    "-s",
    "--silent",
    "-S",
    "--show-error",
    "-f",
    "--fail",
    "--fail-with-body",
    "-I",
    "--head",
    "-i",
    "--include",
    "-X",
    "--request",
    "-H",
    "--header",
    "-d",
    "--data",
    "--data-raw",
    "--data-binary",
    "-u",
    "--user",
    "-k",
    "--insecure",
    "-v",
    "--verbose",
    "--retry",
    "--connect-timeout",
    "--max-time",
    "-C",
    "--continue-at",
    "-A",
    "--user-agent",
    "--compressed",
    "-h",
    "--help",
    "-V",
    "--version",
}

GIT_GLOBAL_VALUE_FLAGS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
}
GIT_GLOBAL_BOOL_FLAGS = {
    "-v",
    "--version",
    "-h",
    "--help",
    "-p",
    "--paginate",
    "-P",
    "--no-pager",
    "--no-replace-objects",
    "--no-lazy-fetch",
    "--no-optional-locks",
    "--no-advice",
    "--bare",
    "--exec-path",
    "--html-path",
    "--man-path",
    "--info-path",
}
GIT_SUBCOMMANDS = {
    "add",
    "bisect",
    "branch",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "config",
    "describe",
    "diff",
    "fetch",
    "grep",
    "help",
    "init",
    "log",
    "ls-files",
    "ls-remote",
    "merge",
    "merge-base",
    "mv",
    "pull",
    "push",
    "rebase",
    "remote",
    "reset",
    "restore",
    "rev-list",
    "rev-parse",
    "revert",
    "rm",
    "show",
    "stash",
    "status",
    "submodule",
    "switch",
    "tag",
    "worktree",
}
GIT_REV_PARSE_FLAGS = {
    "--verify",
    "-q",
    "--quiet",
    "--short",
    "--not",
    "--symbolic",
    "--symbolic-full-name",
    "--all",
    "--branches",
    "--tags",
    "--remotes",
    "--glob",
    "--exclude",
    "--disambiguate",
    "--local-env-vars",
    "--path-format",
    "--git-dir",
    "--git-common-dir",
    "--is-inside-git-dir",
    "--is-inside-work-tree",
    "--is-bare-repository",
    "--is-shallow-repository",
    "--shared-index-path",
    "--git-path",
    "--resolve-git-dir",
    "--show-prefix",
    "--show-cdup",
    "--show-toplevel",
    "--show-superproject-working-tree",
    "--show-object-format",
    "--abbrev-ref",
    "--default",
    "--prefix",
    "--end-of-options",
    "--parseopt",
    "--sq-quote",
    "-h",
    "--help",
}
GIT_COMMON_SUB_FLAGS = {
    "-h",
    "--help",
    "-v",
    "--verbose",
    "-q",
    "--quiet",
    "-s",
    "--short",
    "-b",
    "--branch",
    "--porcelain",
    "--long",
    "-u",
    "--untracked-files",
    "-a",
    "--all",
    "--octopus",
    "--is-ancestor",
    "--independent",
    "--fork-point",
    "--unshallow",
    "--deepen",
    "--depth",
    "--tags",
    "--no-tags",
    "-p",
    "--prune",
    "-f",
    "--force",
    "--dry-run",
    "--no-verify",
    "-m",
    "--message",
    "--amend",
}
GIT_SUBCOMMAND_FLAGS: dict[str, set[str]] = {
    "rev-parse": GIT_REV_PARSE_FLAGS,
}

TAR_GZ_MODES = {"-xzf", "-ztf", "-zxf", "-tzf"}
TAR_PLAIN_MODES = {"-xf", "-tf"}
TAR_ALL_MODES = TAR_GZ_MODES | TAR_PLAIN_MODES
TAR_VALUE_FLAGS = {"-C", "--directory", "--strip-components"}


def _build_cli_parser() -> argparse.ArgumentParser:
    """Construct the CLI ArgumentParser for verify_doc_snippets.py."""
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
    return parser


def _parse_option_line_flags(line: str) -> set[str]:
    """Extract short and long option flags from a single help option line."""
    match = re.match(r"^\s{1,6}(-[^\s].*)$", line)
    if not match:
        return set()
    spec = re.split(r"\s{2,}", match.group(1).strip())[0]
    flags: set[str] = set()
    for name in re.findall(r"--\[no-\]([a-zA-Z0-9][a-zA-Z0-9_-]*)", spec):
        flags.add(f"--{name}")
        flags.add(f"--no-{name}")
    cleaned = re.sub(r"--\[no-\][a-zA-Z0-9][a-zA-Z0-9_-]*", "", spec)
    flags.update(re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9_-]*|-[a-zA-Z0-9]", cleaned))
    return flags


_SUBPROCESS_CACHE: dict[
    tuple[tuple[str, ...], str], subprocess.CompletedProcess[str]
] = {}


def clear_subprocess_cache() -> None:
    """Clear cached read-only subprocess and --help flag check results."""
    _SUBPROCESS_CACHE.clear()
    _HELP_FLAGS_CACHE.clear()


DEFAULT_SNIPPET_TIMEOUT = 60


def _run_cached_subprocess(
    cmd: list[str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a read-only subprocess check once per (argv, cwd) and cache it."""
    cwd_key = str(cwd.resolve()) if cwd is not None else ""
    key = (tuple(cmd), cwd_key)
    cached = _SUBPROCESS_CACHE.get(key)
    if cached is not None:
        return cached
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=DEFAULT_SNIPPET_TIMEOUT,
        check=False,
    )
    _SUBPROCESS_CACHE[key] = proc
    return proc


def _run_readonly_check(
    cmd: list[str],
    cwd: Path | None,
    label: str,
) -> tuple[bool, str]:
    """Run a cached read-only subprocess command and format failure messages."""
    proc = _run_cached_subprocess(cmd, cwd=cwd)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        return False, f"{label} failed: {detail}"
    return True, ""


def _get_help_flags(
    cmd: list[str],
    accept_nonzero: bool = False,
) -> set[str] | None:
    """Run a command with --help/-h and cache its advertised CLI option flags."""
    key = (*cmd, str(accept_nonzero))
    if key in _HELP_FLAGS_CACHE:
        return _HELP_FLAGS_CACHE[key]
    proc = _run_cached_subprocess(cmd, cwd=None)
    if proc.returncode != 0 and not accept_nonzero:
        _HELP_FLAGS_CACHE[key] = None
        return None
    combined = f"{proc.stdout}\n{proc.stderr}"
    flags: set[str] = set()
    for line in combined.splitlines():
        flags.update(_parse_option_line_flags(line))
    _HELP_FLAGS_CACHE[key] = flags
    return flags


def _is_valid_short_flag(
    token: str,
    allowed: set[str],
    allow_bundled: bool,
    value_shorts: set[str],
) -> bool:
    """Check whether a single-dash CLI option token is valid."""
    if token in allowed:
        return True
    if token[:2] in value_shorts:
        return True
    if not allow_bundled:
        return False
    return all(f"-{ch}" in allowed for ch in token[1:])


def _check_flag_token(
    arg: str,
    allowed: set[str],
    allow_bundled: bool,
    val_shorts: set[str],
) -> str | None:
    """Return offending flag string if arg is an unrecognized option, else None."""
    if not arg.startswith("-") or arg == "-":
        return None
    if arg.startswith("--"):
        flag = arg.split("=", 1)[0]
        return None if flag in allowed else flag
    if _is_valid_short_flag(arg, allowed, allow_bundled, val_shorts):
        return None
    return arg


def _find_unknown_flag(
    args: list[str],
    allowed: set[str],
    allow_bundled: bool = False,
    value_shorts: set[str] | None = None,
    stop_tokens: set[str] | None = None,
) -> str | None:
    """Return the first unrecognized CLI flag in args, or None if all are valid."""
    stops = stop_tokens if stop_tokens is not None else {"--"}
    val_shorts = value_shorts if value_shorts is not None else set()
    for arg in args:
        if arg in stops:
            break
        bad = _check_flag_token(arg, allowed, allow_bundled, val_shorts)
        if bad is not None:
            return bad
    return None


def _parse_with_argparse(
    cli_parser: argparse.ArgumentParser,
    raw_args: list[str],
    label: str,
) -> tuple[bool, str]:
    """Validate normalized arguments against an in-process ArgumentParser."""
    normalized_args = [normalize_cli_token(arg) for arg in raw_args]
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            cli_parser.parse_args(normalized_args)
        return True, ""
    except SystemExit as exc:
        if exc.code == 0:
            return True, ""
        return (
            False,
            f"{label} argument parsing failed (exit {exc.code}): {err_buf.getvalue().strip()}",
        )
    except (argparse.ArgumentError, ValueError) as exc:
        return False, f"{label} argument parsing error: {exc}"


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
    """Validate Claude Code marketplace plugin commands."""
    bad = _find_unknown_flag(parts[1:], set())
    if bad:
        return False, f"Invalid marketplace plugin flag: {bad}"
    subparts = parts[1:]
    if subparts[:2] == ["marketplace", "add"]:
        return _validate_marketplace_add(subparts[2:], root)
    if subparts[:1] == ["install"]:
        return _validate_marketplace_install(subparts[1:], root)
    return False, f"Unsupported marketplace plugin command: {cmd}"


def _extract_make_targets(args: list[str]) -> list[str]:
    """Extract target names from make arguments, skipping flags and assignments."""
    targets: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in MAKE_SEPARATE_VALUE_FLAGS:
            skip_next = True
            continue
        if not arg.startswith("-") and "=" not in arg:
            targets.append(arg)
    return targets


def _parse_makefile_targets(content: str) -> set[str]:
    """Extract target names from Makefile text."""
    targets: set[str] = set()
    for line in content.splitlines():
        if ":" not in line or line.startswith(("\t", " ", ".", "#")):
            continue
        head = line.split(":", 1)[0].strip()
        if head and "=" not in head:
            targets.add(head)
    return targets


def _validate_make_fallback(args: list[str], root: Path) -> tuple[bool, str]:
    """Validate make targets directly against Makefile when GNU make is absent."""
    makefile = root / "Makefile"
    if not makefile.is_file():
        return False, "Makefile missing"
    defined = _parse_makefile_targets(makefile.read_text(encoding="utf-8"))
    for target in _extract_make_targets(args):
        if target not in defined:
            return False, f"Makefile missing target '{target}'"
    return True, ""


def _validate_make(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate make flags and targets in dry-run mode (-n) or against Makefile."""
    bad = _find_unknown_flag(
        parts[1:],
        MAKE_ALLOWED_FLAGS,
        allow_bundled=True,
        value_shorts=MAKE_VALUE_SHORT_FLAGS,
    )
    if bad:
        return False, f"Invalid make flag: {bad}"
    if not shutil.which("make"):
        return _validate_make_fallback(parts[1:], root)
    return _run_readonly_check(["make", "-n", *parts[1:]], root, "make dry-run")


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
    for flag in ("-r", "--requirement"):
        if flag in parts:
            r_idx = parts.index(flag)
            if r_idx + 1 >= len(parts) or not (root / parts[r_idx + 1]).is_file():
                return False, f"Referenced requirements file missing after {flag}"
    return True, ""


def _resolve_module_help_spec(
    mod: str,
    rest: list[str],
) -> tuple[list[str], list[str]]:
    """Determine --help command and remaining arguments for a python -m module."""
    if mod == "pip" and rest and not rest[0].startswith("-"):
        return [sys.executable, "-m", "pip", rest[0], "--help"], rest[1:]
    if mod == "unittest" and rest and rest[0] == "discover":
        return [sys.executable, "-m", "unittest", "discover", "--help"], rest[1:]
    return [sys.executable, "-m", mod, "--help"], rest


def _validate_python_module(mod_parts: list[str], root: Path) -> tuple[bool, str]:
    """Validate python -m commands and their CLI flags."""
    if not mod_parts or mod_parts[0].startswith("-"):
        return False, "Incomplete python -m command"
    mod, rest = mod_parts[0], mod_parts[1:]
    help_cmd, flag_args = _resolve_module_help_spec(mod, rest)
    allowed = _get_help_flags(help_cmd)
    if allowed is None:
        return False, f"python -m {mod} help check failed"
    bad = _find_unknown_flag(flag_args, allowed, allow_bundled=True)
    if bad:
        return False, f"Invalid flag for python -m {mod}: {bad}"
    if mod == "pip" and rest and rest[0] == "install":
        return _validate_pip_install(mod_parts, root)
    return True, ""


def _load_json_section_keys(
    root: Path,
    rel_file: str,
    section: str,
) -> set[str]:
    """Load top-level keys of a named mapping section from a JSON catalog file."""
    candidate = root / rel_file
    source = candidate if candidate.is_file() else ROOT / rel_file
    if not source.is_file():
        return set()
    raw = json.loads(source.read_text(encoding="utf-8")).get(section, {})
    return {str(key) for key in raw} if isinstance(raw, Mapping) else set()


def _load_known_cases(root: Path) -> set[str]:
    """Load canonical evaluation case IDs from evals/cases.json."""
    candidate = root / "evals/cases.json"
    source = candidate if candidate.is_file() else ROOT / "evals/cases.json"
    if not source.is_file():
        return set()
    cases = json.loads(source.read_text(encoding="utf-8")).get("cases", [])
    return {
        str(item["id"]) for item in cases if isinstance(item, Mapping) and "id" in item
    }


def _extract_cli_option_values(args: list[str], flag: str) -> list[str]:
    """Extract all values supplied to a long CLI option (--flag val or --flag=val)."""
    values: list[str] = []
    prefix = f"{flag}="
    idx = 0
    while idx < len(args):
        tok = args[idx]
        if tok == "--":
            break
        if tok == flag and idx + 1 < len(args):
            values.append(args[idx + 1])
            idx += 2
            continue
        if tok.startswith(prefix):
            values.append(tok[len(prefix) :])
        idx += 1
    return values


def _validate_fred_catalog_flags(
    raw_args: list[str],
    root: Path,
) -> tuple[bool, str]:
    """Verify --skill, --profile, --case, --pack, --task, and --host against repository catalogs."""
    norm_args = [normalize_cli_token(arg) for arg in raw_args]
    catalog_specs: tuple[tuple[str, str, set[str]], ...] = (
        ("--skill", "skill", _load_known_skills(root)),
        (
            "--profile",
            "profile",
            _load_json_section_keys(root, "catalog/profiles.json", "profiles"),
        ),
        ("--case", "evaluation case", _load_known_cases(root)),
        ("--pack", "pack", set(_load_known_packs(root).keys())),
        (
            "--task",
            "task",
            _load_json_section_keys(root, "catalog/recipes.json", "recipes"),
        ),
        (
            "--host",
            "host",
            _load_json_section_keys(root, "catalog/hosts.json", "hosts"),
        ),
    )
    for flag, label, allowed_names in catalog_specs:
        if not allowed_names:
            continue
        for val in _extract_cli_option_values(norm_args, flag):
            if val not in allowed_names:
                return False, f"Unknown {label} '{val}' for {flag}"
    return True, ""


def _stage_verify_release_fixture(
    fixture_root: Path,
    manifest_arg: str,
    expected_ver: str,
) -> None:
    """Create a release verification fixture simulating either a local pack build or partial release download."""
    rel = Path(manifest_arg)
    if rel.is_absolute():
        return
    bundle_dir = (
        (fixture_root / rel).parent
        if rel.name == "manifest.json"
        else (fixture_root / rel)
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)
    is_local_pack_dir = rel.parts[:1] == ("dist",) and len(rel.parts) >= 2
    if is_local_pack_dir:
        pack_label = rel.parts[1] if rel.parts[1] != "manifest.json" else "starter"
        file_specs: list[tuple[str, bool]] = [
            (f"fred-skills-{expected_ver}-{pack_label}.tar", True),
            (f"fred-skills-{expected_ver}-{pack_label}.zip", True),
        ]
    else:
        file_specs = [
            (f"fred-skills-{expected_ver}-starter.tar", True),
            (f"fred-skills-{expected_ver}-starter.zip", False),
            (f"fred-skills-{expected_ver}-complete.tar", False),
            (f"fred-skills-{expected_ver}-cli.tar.gz", True),
        ]

    archives_manifest: list[dict[str, Any]] = []
    checksum_lines: list[str] = []
    for filename, present_on_disk in file_specs:
        payload = f"fixture:{filename}\n".encode()
        digest = hashlib.sha256(payload).hexdigest()
        if present_on_disk:
            (bundle_dir / filename).write_bytes(payload)
        archives_manifest.append(
            {
                "file": filename,
                "sha256": digest,
                "bytes": len(payload),
                "skills": [],
            }
        )
        checksum_lines.append(f"{digest}  {filename}\n")

    manifest_doc = {
        "schema_version": "1.0",
        "version": expected_ver,
        "pack": "starter" if is_local_pack_dir else "all",
        "licensing": "See repository LICENSE",
        "archives": archives_manifest,
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest_doc, indent=2) + "\n",
        encoding="utf-8",
    )
    joined_checksums = "".join(checksum_lines)
    (bundle_dir / "SHA256SUMS").write_text(joined_checksums, encoding="utf-8")
    (bundle_dir / "checksums.sha256").write_text(joined_checksums, encoding="utf-8")


def _execute_fred_verify_release(
    norm_args: list[str],
    script_path: Path,
    root: Path,
) -> tuple[bool, str]:
    """Execute scripts/fred.py verify-release against an existing bundle or temporary partial fixture."""
    manifest_arg = next(
        (arg for arg in norm_args[1:] if not arg.startswith("-")),
        "manifest.json",
    )
    candidate_target = root / manifest_arg
    resolved_script = script_path.resolve()
    if not Path(manifest_arg).is_absolute() and candidate_target.exists():
        return _run_readonly_check(
            [sys.executable, "-S", str(resolved_script), *norm_args],
            root,
            "scripts/fred.py verify-release",
        )
    expected_ver = _read_expected_version(root)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        _stage_verify_release_fixture(tmp_path, manifest_arg, expected_ver)
        proc = subprocess.run(
            [sys.executable, "-S", str(resolved_script), *norm_args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=DEFAULT_SNIPPET_TIMEOUT,
            check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            return False, f"scripts/fred.py verify-release failed: {detail}"
    return True, ""


def _validate_python_fred(
    parts: list[str],
    script_path: Path,
    root: Path,
) -> tuple[bool, str]:
    """Validate scripts/fred.py invocations against argument parser, catalogs, and search."""
    if fred and hasattr(fred, "parser"):
        ok, err = _parse_with_argparse(fred.parser(), parts[2:], "scripts/fred.py")
        if not ok:
            return False, err

    cat_ok, cat_err = _validate_fred_catalog_flags(parts[2:], root)
    if not cat_ok:
        return False, cat_err

    if len(parts) >= 3 and parts[2] == "search":
        search_args = [normalize_cli_token(arg) for arg in parts[2:]]
        return _run_readonly_check(
            [sys.executable, str(script_path), *search_args],
            root,
            "scripts/fred.py search",
        )
    if len(parts) >= 3 and parts[2] == "verify-release":
        verify_args = [normalize_cli_token(arg) for arg in parts[2:]]
        return _execute_fred_verify_release(verify_args, script_path, root)
    return True, ""


def _validate_script_cli_args(
    script_path: Path,
    script_arg: str,
    script_args: list[str],
) -> tuple[bool, str]:
    """Validate CLI flags for a repository Python script via parser or --help."""
    if script_arg.endswith("verify_doc_snippets.py"):
        return _parse_with_argparse(_build_cli_parser(), script_args, script_arg)
    if script_arg.endswith("audit_to_sarif.py") and audit_to_sarif_mod is not None:
        return _parse_with_argparse(
            audit_to_sarif_mod.build_parser(), script_args, script_arg
        )
    allowed = _get_help_flags([sys.executable, str(script_path), "--help"])
    if allowed is None:
        return False, f"{script_arg} --help failed"
    bad = _find_unknown_flag(script_args, allowed)
    if bad:
        return False, f"Invalid CLI flag '{bad}' for {script_arg}"
    return True, ""


def _validate_run_checks_script(
    parts: list[str],
    script_path: Path,
    root: Path,
) -> tuple[bool, str]:
    """Validate scripts/run_checks.py invocations in dry-run mode."""
    extra_args = [
        normalize_cli_token(arg) for arg in parts[2:] if arg not in ("-n", "--dry-run")
    ]
    return _run_readonly_check(
        [sys.executable, str(script_path), "--dry-run", *extra_args],
        root,
        "run_checks.py",
    )


def _try_direct_readonly_script(
    script_path: Path,
    script_arg: str,
    raw_args: list[str],
    root: Path,
) -> tuple[bool, str] | None:
    """Execute argparse-backed non-mutating scripts directly when safe."""
    if script_arg.endswith("verify_doc_snippets.py"):
        return None
    norm_args = [normalize_cli_token(arg) for arg in raw_args]
    if "--check" in norm_args:
        return _run_readonly_check(
            [sys.executable, str(script_path), *norm_args],
            root,
            f"{script_arg} --check",
        )
    if script_arg.endswith("validate_skills.py") and "--telemetry" not in norm_args:
        return _run_readonly_check(
            [sys.executable, str(script_path), *norm_args],
            root,
            "validate_skills.py",
        )
    return None


def _validate_python_script(parts: list[str], root: Path) -> tuple[bool, str]:
    """Validate python script invocations and CLI flags safely in dry-run mode."""
    script_arg = parts[1]
    script_path = root / script_arg
    if not script_path.is_file():
        return False, f"Referenced script does not exist: {script_arg}"

    if script_arg.endswith("fred.py"):
        return _validate_python_fred(parts, script_path, root)

    if script_arg.endswith("run_checks.py"):
        return _validate_run_checks_script(parts, script_path, root)

    if script_arg.endswith("bump_version.py"):
        extra_args = [
            normalize_cli_token(arg) for arg in parts[2:] if arg != "--dry-run"
        ]
        return _run_readonly_check(
            [sys.executable, str(script_path), "--dry-run", *extra_args],
            root,
            "bump_version.py",
        )

    direct = _try_direct_readonly_script(script_path, script_arg, parts[2:], root)
    if direct is not None:
        return direct

    return _validate_script_cli_args(script_path, script_arg, parts[2:])


def _validate_python(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Dispatch python and python3 command validations."""
    idx = 1
    while idx < len(parts):
        tok = parts[idx]
        if tok == "-c":
            return _validate_python_inline(["python", *parts[idx:]])
        if tok == "-m":
            return _validate_python_module(parts[idx + 1 :], root)
        if tok in PYTHON_INTERPRETER_FLAGS:
            idx += 1
            continue
        if tok.startswith("-"):
            return False, f"Invalid python flag: {tok}"
        return _validate_python_script(["python", *parts[idx:]], root)
    return True, ""


def _is_safe_ruff_check(parts: list[str]) -> bool:
    """Return True when ruff is invoked in a non-mutating check mode."""
    if parts[1] == "check":
        return "--fix" not in parts and "--add-noqa" not in parts
    return parts[1] == "format" and "--check" in parts


def _validate_ruff_flags(subcmd: str, args: list[str]) -> tuple[bool, str]:
    """Validate ruff subcommand flags via --help or fallback allowlist."""
    allowed = (
        _get_help_flags(["ruff", subcmd, "--help"])
        if shutil.which("ruff")
        else RUFF_FALLBACK_FLAGS
    )
    bad = _find_unknown_flag(args, allowed or RUFF_FALLBACK_FLAGS)
    if bad:
        return False, f"Invalid ruff {subcmd} flag: {bad}"
    return True, ""


def _validate_ruff(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate ruff subcommands and CLI flags in non-mutating mode."""
    if len(parts) < 2:
        return False, "ruff requires a subcommand or flag"
    subcmd = parts[1]
    if subcmd in RUFF_TOP_FLAGS:
        bad = _find_unknown_flag(parts[1:], RUFF_TOP_FLAGS)
        return (False, f"Invalid ruff flag: {bad}") if bad else (True, "")
    if subcmd not in RUFF_SUBCOMMANDS:
        return False, f"Invalid ruff subcommand: {subcmd}"
    if _is_safe_ruff_check(parts) and shutil.which("ruff"):
        return _run_readonly_check(parts, root, "ruff check")
    return _validate_ruff_flags(subcmd, parts[2:])


def _validate_pre_commit_config(root: Path) -> tuple[bool, str]:
    """Validate .pre-commit-config.yaml when pre-commit is installed."""
    if not shutil.which("pre-commit"):
        return True, ""
    return _run_readonly_check(
        ["pre-commit", "validate-config", str(root / ".pre-commit-config.yaml")],
        root,
        "pre-commit validate-config",
    )


def _validate_pre_commit(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate pre-commit subcommands, CLI flags, and configuration file."""
    if len(parts) < 2:
        return False, "pre-commit requires a subcommand or flag"
    subcmd = parts[1]
    if subcmd in PRE_COMMIT_TOP_FLAGS:
        bad = _find_unknown_flag(parts[1:], PRE_COMMIT_TOP_FLAGS)
        return (False, f"Invalid pre-commit flag: {bad}") if bad else (True, "")
    if subcmd not in PRE_COMMIT_SUBCOMMANDS:
        return False, f"Invalid pre-commit subcommand: {subcmd}"
    allowed = (
        _get_help_flags(["pre-commit", subcmd, "--help"])
        if shutil.which("pre-commit")
        else PRE_COMMIT_FALLBACK_FLAGS
    )
    bad = _find_unknown_flag(parts[2:], allowed or PRE_COMMIT_FALLBACK_FLAGS)
    if bad:
        return False, f"Invalid pre-commit {subcmd} flag: {bad}"
    return _validate_pre_commit_config(root)


STATIC_RELEASE_ASSETS: frozenset[str] = frozenset(
    {
        "SHA256SUMS",
        "checksums.sha256",
        "manifest.json",
        "fred-skills-suite.tar.gz",
        "darwin.fred-skills.tar.gz",
        "linux.fred-skills.tar.gz",
        "win32.fred-skills.zip",
    }
)
_PACK_ARCHIVE_RE = re.compile(
    r"^fred-skills-(?P<ver>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)-(?P<pack>[a-z0-9-]+)\.(?:tar\.gz|tar|zip)$"
)


def _read_expected_version(root: Path) -> str:
    """Read expected VERSION from root, falling back to repository ROOT."""
    candidate = root / "VERSION"
    source = candidate if candidate.is_file() else ROOT / "VERSION"
    return source.read_text(encoding="utf-8").strip() if source.is_file() else "0.0.0"


def _load_known_packs(root: Path) -> dict[str, list[str]]:
    """Load canonical pack definitions from catalog/packs.json."""
    candidate = root / "catalog/packs.json"
    source = candidate if candidate.is_file() else ROOT / "catalog/packs.json"
    if not source.is_file():
        return {}
    raw = json.loads(source.read_text(encoding="utf-8")).get("packs", {})
    return {str(name): list(cfg.get("skills", [])) for name, cfg in raw.items()}


def _load_known_skills(root: Path) -> set[str]:
    """Load standalone skill names from skills/*/SKILL.md."""
    candidate = root / "skills"
    skills_dir = candidate if candidate.is_dir() else ROOT / "skills"
    if not skills_dir.is_dir():
        return set()
    return {entry.parent.name for entry in skills_dir.glob("*/SKILL.md")}


def _split_pack_archive_stem(
    filename: str,
    expected_version: str,
) -> tuple[str, str] | None:
    """Extract (version, pack_name) from a fred-skills-<version>-<pack|cli>.(tar.gz|tar|zip) filename."""
    if filename.endswith(".tar.gz"):
        stem = filename.removesuffix(".tar.gz")
    elif filename.endswith(".tar"):
        stem = filename.removesuffix(".tar")
    elif filename.endswith(".zip"):
        stem = filename.removesuffix(".zip")
    else:
        stem = filename
    exact_prefix = f"fred-skills-{expected_version}-"
    if stem != filename and stem.startswith(exact_prefix):
        pack_part = stem.removeprefix(exact_prefix)
        return (expected_version, pack_part) if pack_part else None
    match = _PACK_ARCHIVE_RE.match(filename)
    return (match.group("ver"), match.group("pack")) if match else None


def _validate_pack_archive_name(filename: str, root: Path) -> tuple[bool, str]:
    """Verify pack or CLI archive version and canonical pack name against VERSION and catalog/packs.json."""
    expected_ver = _read_expected_version(root)
    parsed = _split_pack_archive_stem(filename, expected_ver)
    if parsed is None:
        return False, f"Malformed release pack archive filename: {filename}"
    found_ver, pack_name = parsed
    if found_ver != expected_ver:
        return (
            False,
            f"Pack archive '{filename}' version '{found_ver}' does not match VERSION '{expected_ver}'",
        )
    if pack_name == "cli" and filename.endswith((".tar.gz", ".zip")):
        return True, ""
    if filename.endswith(".tar.gz"):
        return False, f"Pack archive '{filename}' must use .tar or .zip extension"
    known_packs = _load_known_packs(root)
    if known_packs and pack_name not in known_packs:
        return (
            False,
            f"Unknown pack '{pack_name}' in archive '{filename}' (not in catalog/packs.json)",
        )
    return True, ""


def _validate_release_asset_name(asset: str, root: Path) -> tuple[bool, str]:
    """Validate a release asset filename in curl or tar snippets."""
    if asset in STATIC_RELEASE_ASSETS:
        return True, ""
    if asset.startswith("fred-skills-"):
        return _validate_pack_archive_name(asset, root)
    if asset.endswith(".tar.gz"):
        skill_name = asset.removesuffix(".tar.gz")
        known_skills = _load_known_skills(root)
        if known_skills and skill_name not in known_skills:
            return False, f"Unknown standalone skill archive: {asset}"
        return True, ""
    return False, f"Unrecognized release asset filename: {asset}"


def _validate_curl_release_url(url: str, root: Path) -> tuple[bool, str]:
    """Verify GitHub release download URL tag and asset name against VERSION and catalog."""
    if "fderuiter/fred-skills" not in url:
        return False, f"curl target does not match repo: {url}"
    marker = "/releases/download/"
    if marker not in url:
        return True, ""
    tag, sep, asset = url.split(marker, 1)[1].partition("/")
    expected_tag = f"v{_read_expected_version(root)}"
    if not sep or not asset:
        return False, f"Incomplete release download URL: {url}"
    if tag != expected_tag:
        return (
            False,
            f"Release URL tag '{tag}' does not match expected '{expected_tag}' from VERSION",
        )
    return _validate_release_asset_name(asset, root)


def _validate_curl_urls(args: list[str], root: Path) -> tuple[bool, str]:
    """Verify curl targets match the repository, VERSION tag, and catalog pack/skill names."""
    if any(flag in args for flag in ("-h", "--help", "-V", "--version")):
        return True, ""
    urls = [part for part in args if part.startswith(("http://", "https://"))]
    if not urls:
        return False, "curl command missing target URL"
    for url in urls:
        ok, err = _validate_curl_release_url(url, root)
        if not ok:
            return False, err
    return True, ""


def _validate_curl(parts: list[str], root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate curl CLI flags and target repository URL without network requests."""
    dynamic = (
        _get_help_flags(["curl", "--help", "all"]) if shutil.which("curl") else None
    )
    allowed = CURL_FALLBACK_FLAGS | (dynamic or set())
    bad = _find_unknown_flag(parts[1:], allowed, allow_bundled=True)
    if bad:
        return False, f"Invalid curl flag: {bad}"
    return _validate_curl_urls(parts[1:], root)


def _validate_gh_attestation(parts: list[str], cmd: str) -> tuple[bool, str]:
    """Validate gh attestation subcommand and repository target."""
    if len(parts) < 3 or parts[2] not in {"verify", "download", "trusted-root"}:
        return False, f"Invalid gh attestation action: {cmd}"
    if "fderuiter/fred-skills" not in parts:
        return False, f"gh attestation command missing repo flag: {cmd}"
    return True, ""


def _get_gh_subcommand_flags(subcmd: str, sub_args: list[str]) -> set[str]:
    """Resolve valid CLI flags for a specific gh subcommand and optional action."""
    fallback = GH_SUBCOMMAND_FLAGS.get(subcmd, GH_GLOBAL_FLAGS)
    if not shutil.which("gh") or subcmd == "attestation":
        return fallback
    if sub_args and not sub_args[0].startswith("-") and subcmd != "api":
        action_flags = _get_help_flags(["gh", subcmd, sub_args[0], "--help"])
        if action_flags:
            return action_flags | GH_GLOBAL_FLAGS
    top_flags = _get_help_flags(["gh", subcmd, "--help"])
    return fallback | (top_flags or set())


def _validate_gh(parts: list[str], _root: Path, cmd: str) -> tuple[bool, str]:
    """Validate GitHub CLI subcommands and per-subcommand flags safely."""
    if len(parts) < 2:
        return False, "gh requires a subcommand or flag"
    subcmd = parts[1]
    if subcmd in {"-h", "--help", "--version"}:
        bad = _find_unknown_flag(parts[1:], {"-h", "--help", "--version"})
        return (False, f"Invalid gh flag: {bad}") if bad else (True, "")
    if subcmd not in GH_SUBCOMMANDS:
        return False, f"Invalid gh subcommand: {subcmd}"
    allowed = _get_gh_subcommand_flags(subcmd, parts[2:])
    bad = _find_unknown_flag(parts[2:], allowed, allow_bundled=True)
    if bad:
        return False, f"Invalid gh {subcmd} flag: {bad}"
    if subcmd == "attestation":
        return _validate_gh_attestation(parts, cmd)
    return True, ""


def _validate_sha256sum(parts: list[str], _root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate sha256sum CLI flags and target file arguments."""
    bad = _find_unknown_flag(parts[1:], SHA256SUM_ALLOWED_FLAGS, allow_bundled=True)
    if bad:
        return False, f"Invalid sha256sum flag: {bad}"
    if "--help" in parts or "--version" in parts:
        return True, ""
    if not any(not p.startswith("-") for p in parts[1:]):
        return False, "sha256sum missing target file"
    return True, ""


def _consume_git_global_option(
    args: list[str],
    idx: int,
) -> tuple[int, str | None]:
    """Consume a single global git flag before the subcommand and return next index."""
    tok = args[idx]
    if tok in GIT_GLOBAL_VALUE_FLAGS:
        if idx + 1 >= len(args):
            return idx, f"Missing argument for git {tok}"
        return idx + 2, None
    if tok.split("=", 1)[0] in GIT_GLOBAL_VALUE_FLAGS | {"--exec-path"}:
        return idx + 1, None
    if tok in GIT_GLOBAL_BOOL_FLAGS:
        return idx + 1, None
    return idx, f"Invalid global git flag: {tok}"


def _extract_git_subcommand(
    args: list[str],
) -> tuple[str | None, list[str], str | None]:
    """Extract git subcommand and subcommand arguments after global git flags."""
    idx = 0
    while idx < len(args):
        if not args[idx].startswith("-"):
            return args[idx], args[idx + 1 :], None
        idx, err = _consume_git_global_option(args, idx)
        if err:
            return None, [], err
    if any(flag in args for flag in ("-v", "--version", "-h", "--help")):
        return None, [], None
    return None, [], "git command missing subcommand"


def _get_git_subcommand_flags(subcmd: str) -> set[str]:
    """Return valid CLI flags for a git subcommand."""
    base = GIT_SUBCOMMAND_FLAGS.get(subcmd, GIT_COMMON_SUB_FLAGS)
    if not shutil.which("git") or subcmd == "rev-parse":
        return base
    dynamic = _get_help_flags(["git", subcmd, "-h"], accept_nonzero=True)
    return base | (dynamic or set())


def _validate_git(parts: list[str], _root: Path, _cmd: str) -> tuple[bool, str]:
    """Validate git global flags, subcommand, and subcommand CLI flags."""
    subcmd, sub_args, err = _extract_git_subcommand(parts[1:])
    if err:
        return False, err
    if subcmd is None:
        return True, ""
    if subcmd not in GIT_SUBCOMMANDS:
        return False, f"Invalid git subcommand: {subcmd}"
    allowed = _get_git_subcommand_flags(subcmd)
    bad = _find_unknown_flag(
        sub_args,
        allowed,
        allow_bundled=True,
        stop_tokens={"--", "--end-of-options"},
    )
    if bad:
        return False, f"Invalid git {subcmd} flag: {bad}"
    return True, ""


def _parse_tar_tokens(
    args: list[str],
) -> tuple[str | None, list[str], str | None]:
    """Parse tar arguments into mode flag, positional arguments, and optional error."""
    mode_flag: str | None = None
    positionals: list[str] = []
    skip_next = False
    for tok in args:
        if skip_next:
            skip_next = False
            continue
        if tok in TAR_ALL_MODES:
            if mode_flag is not None:
                return None, [], f"Duplicate tar mode flag: {tok}"
            mode_flag = tok
        elif tok.split("=", 1)[0] in TAR_VALUE_FLAGS:
            skip_next = "=" not in tok
        elif tok.startswith("-"):
            return None, [], f"Invalid tar flag: {tok}"
        else:
            positionals.append(tok)
    if skip_next:
        return None, [], "Missing value for tar option"
    return mode_flag, positionals, None


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


def _validate_tar_archive_mode(archive: str, mode_flag: str) -> tuple[bool, str]:
    """Verify tar archive extension matches compression flags."""
    if archive.endswith((".tar.gz", ".tgz")):
        if mode_flag not in TAR_GZ_MODES:
            return False, f"Compressed archive '{archive}' requires -xzf or -ztf"
        return True, ""
    if archive.endswith(".tar"):
        if mode_flag not in TAR_PLAIN_MODES:
            return False, f"Uncompressed archive '{archive}' requires -xf or -tf"
        return True, ""
    return False, f"Unsupported tar archive format: {archive}"


def _validate_tar_host_dir(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
    """Validate tar -C/--directory destination against configured host paths."""
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


def _validate_tar_archive_identity(archive: str, root: Path) -> tuple[bool, str]:
    """Validate tar archive version, pack name, or skill name when targeting toolkit archives."""
    name = Path(archive).name
    if name in STATIC_RELEASE_ASSETS:
        return True, ""
    if name.startswith("fred-skills-"):
        return _validate_pack_archive_name(name, root)
    has_repo_context = (root / "skills").is_dir() or (
        root / "catalog/packs.json"
    ).is_file()
    if name.endswith(".tar.gz") and has_repo_context:
        return _validate_release_asset_name(name, root)
    return True, ""


def _validate_tar(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
    """Validate tar command flags, archive compression, archive identity, and host paths."""
    mode_flag, positionals, err = _parse_tar_tokens(parts[1:])
    if err:
        return False, err
    if mode_flag is None or not positionals:
        return False, f"tar command must specify extract or test flags: {cmd}"
    ok, mode_err = _validate_tar_archive_mode(positionals[0], mode_flag)
    if not ok:
        return False, mode_err
    id_ok, id_err = _validate_tar_archive_identity(positionals[0], root)
    if not id_ok:
        return False, id_err
    return _validate_tar_host_dir(parts, root, cmd)


TOOL_DISPATCHERS: dict[tuple[str, ...], ToolHandler] = {
    ("/plugin",): _validate_marketplace,
    ("make",): _validate_make,
    ("python",): _validate_python,
    ("python3",): _validate_python,
    ("ruff",): _validate_ruff,
    ("pre-commit",): _validate_pre_commit,
    ("curl",): _validate_curl,
    ("gh",): _validate_gh,
    ("sha256sum",): _validate_sha256sum,
    ("git",): _validate_git,
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


def _toggle_quote(ch: str, in_single: bool, in_double: bool) -> tuple[bool, bool, bool]:
    """Update quote state if ch is an active quote delimiter."""
    if ch == "'" and not in_double:
        return not in_single, in_double, True
    if ch == '"' and not in_single:
        return in_single, not in_double, True
    return in_single, in_double, False


def _mask_quoted(text: str) -> str:
    """Mask quoted and backslash-escaped characters with 'Q' preserving string length."""
    chars: list[str] = []
    in_single = False
    in_double = False
    escape = False
    for ch in text:
        if escape:
            chars.append("Q")
            escape = False
            continue
        if ch == "\\" and not in_single:
            chars.append("Q")
            escape = True
            continue
        in_single, in_double, is_delim = _toggle_quote(ch, in_single, in_double)
        is_masked = is_delim or in_single or in_double
        chars.append("Q" if is_masked else ch)
    return "".join(chars)


def _split_shell_operators(cmd: str) -> list[str]:
    """Split a shell command on unquoted logical operators, pipes, and semicolons."""
    masked = _mask_quoted(cmd)
    segments: list[str] = []
    last = 0
    for match in re.finditer(r"&&|\|\||\|&|\||;", masked):
        seg = cmd[last : match.start()].strip()
        if seg:
            segments.append(seg)
        last = match.end()
    tail = cmd[last:].strip()
    if tail:
        segments.append(tail)
    return segments


def _strip_shell_redirections(segment: str) -> str:
    """Strip unquoted file/fd redirections while preserving <placeholder> tokens."""
    masked = _mask_quoted(segment)
    pattern = r"(?<!\S)(?:\d*|&)>>?\s*(?:&\d+|\S+)|(?<!\S)<(?![a-zA-Z0-9_-]+>)\s*\S+"
    spans = [m.span() for m in re.finditer(pattern, masked)]
    if not spans:
        return segment
    result = segment
    for start, end in reversed(spans):
        result = f"{result[:start]} {result[end:]}"
    return result.strip()


SHELL_CONTROL_PREFIXES: frozenset[str] = frozenset(
    {"if", "then", "elif", "else", "while", "until", "do", "!"}
)
SHELL_CLOSING_KEYWORDS: frozenset[str] = frozenset({"fi", "done", "esac"})
ENV_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
)
ENV_BOOL_FLAGS: frozenset[str] = frozenset(
    {"-i", "-", "--ignore-environment", "-0", "--null"}
)
_SHELL_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _consume_env_token(parts: list[str], idx: int) -> tuple[int, bool]:
    """Consume a single option or variable assignment following env."""
    tok = parts[idx]
    if tok == "--":
        return idx + 1, False
    if tok in ENV_VALUE_FLAGS:
        return min(len(parts), idx + 2), True
    is_inline_env = (
        tok in ENV_BOOL_FLAGS
        or tok.startswith(("--unset=", "--chdir=", "--split-string="))
        or bool(_SHELL_VAR_ASSIGN_RE.match(tok))
    )
    if is_inline_env:
        return idx + 1, True
    return idx, False


def _strip_env_wrapper(parts: list[str]) -> list[str]:
    """Strip an env command wrapper and its flags/assignments from parts."""
    idx = 1
    while idx < len(parts):
        idx, keep_going = _consume_env_token(parts, idx)
        if not keep_going:
            break
    return parts[idx:]


def _is_for_loop_header(parts: list[str]) -> bool:
    """Return True if parts represent a shell 'for <var> in ...' loop header."""
    if parts[0] != "for":
        return False
    return len(parts) <= 2 or parts[2] == "in"


def _strip_one_shell_prefix(parts: list[str]) -> tuple[list[str], bool]:
    """Strip one leading shell keyword, env wrapper, or VAR=val token."""
    head = parts[0]
    if head in SHELL_CLOSING_KEYWORDS or _is_for_loop_header(parts):
        return [], False
    if head in SHELL_CONTROL_PREFIXES or head == "for":
        return parts[1:], True
    if _SHELL_VAR_ASSIGN_RE.match(head):
        return parts[1:], True
    if head == "env":
        return _strip_env_wrapper(parts), True
    return parts, False


def _strip_command_prefixes(parts: list[str]) -> list[str]:
    """Unwrap leading shell control keywords, env wrappers, and assignments."""
    current = parts
    while current:
        current, changed = _strip_one_shell_prefix(current)
        if not changed:
            break
    return current


def _has_singular_agent_skills_path(segment: str) -> bool:
    """Return True if a command segment references singular .agent/skills."""
    return bool(re.search(r"(?:^|[\s/\\~\"'])\.agent/skills(?:$|[\s/\\\"'])", segment))


def _execute_single_segment(segment: str, root: Path) -> tuple[bool, str]:
    """Tokenize and dispatch a single shell command segment."""
    cleaned = _strip_shell_redirections(segment)
    if not cleaned:
        return True, ""
    if _has_singular_agent_skills_path(cleaned):
        return (
            False,
            f"Singular '.agent/skills' path is invalid in '{cleaned}'; use '.agents/skills'",
        )
    parts = _strip_command_prefixes(shlex.split(cleaned))
    if not parts:
        return True, ""
    dispatcher = find_tool_dispatcher(parts)
    if dispatcher is None:
        return True, ""
    return dispatcher(parts, root, cleaned)


def execute_snippet_command(
    cmd: str,
    root: Path = ROOT,
) -> tuple[bool, str]:
    """Execute or dry-run validate a single documented shell command.

    Respects constraints: runs in dry-run mode without network calls or destructive writes.
    """
    try:
        shlex.split(cmd)
    except ValueError as exc:
        return False, f"Failed to parse command syntax: {exc}"

    segments = _split_shell_operators(cmd)
    if not segments:
        return True, ""

    for segment in segments:
        ok, msg = _execute_single_segment(segment, root)
        if not ok:
            return False, msg

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
        if Path(path_str).is_absolute():
            return path.as_posix()
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


def _active_cursor_heading(block: CommandBlock) -> str | None:
    """Return the innermost active Markdown heading naming Cursor, if any."""
    for heading in reversed(block.headings):
        if _CURSOR_HEADING_RE.search(heading):
            return heading
    return None


def _verify_cursor_heading_block(rel_path: str, block: CommandBlock) -> list[str]:
    """Reject Claude Code /plugin commands inside sections whose headings name Cursor."""
    cursor_heading = _active_cursor_heading(block)
    if cursor_heading is None:
        return []
    errors: list[str] = []
    for line_no, parts in _block_command_tokens(block):
        if parts[:1] == ["/plugin"]:
            errors.append(
                f"{rel_path}:L{line_no}: Unsupported '/plugin' command under Cursor heading '{cursor_heading}'; use Dashboard -> Plugins & MCPs and Customize"
            )
    return errors


def _verify_marketplace_block(rel_path: str, block: CommandBlock) -> list[str]:
    """Reject incomplete Claude marketplace blocks or unsupported Codex plugin commands."""
    if rel_path not in MARKETPLACE_ONBOARDING_DOCS:
        return []
    tokenized = _block_command_tokens(block)
    has_claude_install = any(p[:2] == ["/plugin", "install"] for _, p in tokenized)
    errors: list[str] = []
    for line_no, parts in tokenized:
        if parts[:3] == ["/plugin", "marketplace", "add"] and not has_claude_install:
            errors.append(
                f"{rel_path}:L{line_no}: Marketplace block missing '/plugin install <name>@fred-skills'"
            )
        if parts[:2] == ["codex", "plugin"]:
            errors.append(
                f"{rel_path}:L{line_no}: Unsupported 'codex plugin' command; use '.agents/skills' or '$skill-installer'"
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


RELEASE_COUNT_DOCS: frozenset[str] = frozenset(CORE_INVARIANT_DOCS)


def _is_release_count_doc(rel_path: str, root: Path = ROOT) -> bool:
    """Return True if rel_path is a core guide or untagged active release note."""
    if rel_path in RELEASE_COUNT_DOCS:
        return True
    return rel_path in discover_untagged_release_notes(root, fallback_root=ROOT)


def _format_release_count_mismatch(rel_path: str, mismatch: Any) -> str:
    if mismatch.label == "starter pack":
        return (
            f"{rel_path}:L{mismatch.line_no}: Documented starter pack count "
            f"{mismatch.found} does not match catalog count {mismatch.expected}"
        )
    return (
        f"{rel_path}:L{mismatch.line_no}: Documented skill count "
        f"{mismatch.found} does not match catalog total {mismatch.expected}"
    )


def _verify_doc_release_counts(rel_path: str, root: Path) -> list[str]:
    """Verify documented total and starter pack skill counts against catalog/packs.json."""
    if not _is_release_count_doc(rel_path, root):
        return []
    doc_path = root / rel_path
    packs = _load_known_packs(root)
    if not doc_path.is_file() or "complete" not in packs:
        return []
    rules = build_prose_count_rules(
        DocCountExpectations(
            total_skills=len(packs["complete"]),
            starter_skills=len(packs.get("starter", [])),
        )
    )
    mismatches = find_prose_count_mismatches(
        doc_path.read_text(encoding="utf-8"),
        rules,
    )
    return [_format_release_count_mismatch(rel_path, m) for m in mismatches]


_NON_AUDIT_SCHEMA_V1_KEYS: frozenset[str] = frozenset(
    {
        "packs",
        "hosts",
        "profiles",
        "recipes",
        "cases",
        "suites",
        "plan",
        "reconciliations",
    }
)


def _verify_yaml_block(
    rel_path: str,
    block: CommandBlock,
    root: Path,
) -> list[str]:
    """Validate YAML syntax and dry-run execute extracted run: steps."""
    if block.content.strip():
        try:
            yaml.safe_load(block.content)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            offset = (mark.line + 1) if mark is not None else 1
            return [
                f"{rel_path}:L{block.start_line + offset}: Invalid YAML syntax: {exc}"
            ]
    return _verify_shell_block(rel_path, block, root)


def _is_valid_signature_stub(source: str) -> bool:
    """Return True if source is a documented bare def/async def signature header."""
    stripped = source.strip()
    if not stripped.startswith(("def ", "async def ", "@")) or stripped.endswith(":"):
        return False
    try:
        tree = ast.parse(f"{stripped}:\n    ...\n")
    except SyntaxError:
        return False
    return len(tree.body) == 1 and isinstance(
        tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
    )


def _verify_python_block(rel_path: str, block: CommandBlock) -> list[str]:
    """Validate Python fenced code block syntax via AST parsing."""
    dedented = textwrap.dedent(block.content)
    if not dedented.strip():
        return []
    try:
        ast.parse(dedented)
        return []
    except SyntaxError as exc:
        if _is_valid_signature_stub(dedented):
            return []
        err_line = block.start_line + (exc.lineno or 1)
        return [f"{rel_path}:L{err_line}: Python syntax error: {exc.msg}"]


def _is_audit_json_candidate(data: Any) -> bool:
    """Return True when a parsed JSON object represents an Audit Schema 1.0 ledger."""
    if not isinstance(data, dict):
        return False
    if _NON_AUDIT_SCHEMA_V1_KEYS.intersection(data.keys()):
        return False
    if data.get("schema_version") == "1.0":
        return True
    return "findings" in data and "actor" not in data


def _load_audit_schema(root: Path) -> dict[str, Any] | None:
    """Load shared/repo-audit/resources/assets/audit.schema.json from root or ROOT."""
    rel = "shared/repo-audit/resources/assets/audit.schema.json"
    candidate = root / rel
    source = candidate if candidate.is_file() else ROOT / rel
    if not source.is_file():
        return None
    loaded: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
    return loaded


def _validate_audit_json_schema(
    rel_path: str,
    line_no: int,
    data: dict[str, Any],
    root: Path,
) -> list[str]:
    """Validate an Audit Schema 1.0 JSON object against audit.schema.json."""
    schema = _load_audit_schema(root)
    if schema is None:
        return [f"{rel_path}:L{line_no}: Missing audit.schema.json contract"]
    validator = jsonschema.Draft202012Validator(schema)
    schema_errs = sorted(
        validator.iter_errors(data), key=lambda e: list(e.absolute_path)
    )
    return [
        f"{rel_path}:L{line_no}: Audit schema validation failed: {err.message}"
        for err in schema_errs
    ]


def _verify_json_block(
    rel_path: str,
    block: CommandBlock,
    root: Path,
) -> list[str]:
    """Validate JSON syntax and Audit Schema 1.0 compliance in fenced JSON blocks."""
    if not block.content.strip():
        return [f"{rel_path}:L{block.start_line + 1}: Empty JSON code block"]
    try:
        data = json.loads(block.content)
    except json.JSONDecodeError as exc:
        err_line = block.start_line + exc.lineno
        return [f"{rel_path}:L{err_line}: Invalid JSON syntax: {exc.msg}"]
    if _is_audit_json_candidate(data):
        return _validate_audit_json_schema(rel_path, block.start_line + 1, data, root)
    return []


def _verify_single_block(
    rel_path: str,
    block: CommandBlock,
    root: Path,
) -> list[str]:
    """Dispatch validation for a single fenced code block according to language."""
    errors = _verify_cursor_heading_block(rel_path, block)
    lang = block.language.lower()
    if lang in ("sh", "bash", "shell"):
        return errors + _verify_shell_block(rel_path, block, root)
    if lang in ("yaml", "yml"):
        return errors + _verify_yaml_block(rel_path, block, root)
    if lang in ("python", "py"):
        return errors + _verify_python_block(rel_path, block)
    if lang == "json":
        return errors + _verify_json_block(rel_path, block, root)
    return errors


_RUNBOOK_MODULE_PATHS: dict[str, str] = {
    "fred": "scripts/fred.py",
    "fredlib.common": "scripts/fredlib/common.py",
    "fredlib.evaluations": "scripts/fredlib/evaluations.py",
    "fredlib.installation": "scripts/fredlib/installation.py",
    "fredlib.releases": "scripts/fredlib/releases.py",
    "fredlib.reports": "scripts/fredlib/reports.py",
    "fredlib.repository": "scripts/fredlib/repository.py",
    "fredlib.runs": "scripts/fredlib/runs.py",
    "fredlib.vetting": "scripts/fredlib/vetting.py",
    "triage": "skills/repo-audit-diagnostics/scripts/triage.py",
}
_RUNBOOK_COLON_BASENAMES: dict[str, str] = {
    Path(rel).name: mod_key for mod_key, rel in _RUNBOOK_MODULE_PATHS.items()
}
_RAW_COLON_LINE_RE = re.compile(r":L\d+(?:-L?\d+)?\b")
_RAW_BARE_RANGE_RE = re.compile(r"(?<!:)\bL\d+-L\d+\b")
_DOTTED_CITATION_RE = re.compile(
    r"\b(?P<mod>fredlib\.[A-Za-z_][A-Za-z0-9_]*|fred|triage)\.(?!py\b)"
    r"(?P<sym>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\b"
)
_COLON_CITATION_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+?\.py):(?P<sym>(?!L\d+\b)[A-Za-z_][A-Za-z0-9_.]*)\b"
)


def _extract_ast_target_names(node: ast.AST) -> set[str]:
    """Return top-level or class-body symbol names defined by an AST statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _collect_ast_symbols(source_path: Path) -> set[str]:
    """Parse a Python module via AST and collect all defined symbol names."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for item in tree.body:
        symbols.update(_extract_ast_target_names(item))
        if isinstance(item, ast.ClassDef):
            for member in item.body:
                symbols.update(
                    f"{item.name}.{name}" for name in _extract_ast_target_names(member)
                )
    return symbols


def _resolve_runbook_module_file(mod_key: str, root: Path) -> Path | None:
    """Resolve a runbook citation module key or relative .py path to a file on disk."""
    rel = _RUNBOOK_MODULE_PATHS.get(mod_key, mod_key if mod_key.endswith(".py") else "")
    if not rel:
        return None
    candidate = root / rel
    if candidate.is_file():
        return candidate
    fallback = ROOT / rel
    return fallback if fallback.is_file() else None


def _extract_line_symbol_citations(line: str) -> list[tuple[str, str]]:
    """Extract (module_key, symbol_name) citations from a single documentation line."""
    refs = [
        (m.group("mod"), m.group("sym")) for m in _DOTTED_CITATION_RE.finditer(line)
    ]
    for match in _COLON_CITATION_RE.finditer(line):
        raw_path = match.group("path")
        mod_key = _RUNBOOK_COLON_BASENAMES.get(Path(raw_path).name, raw_path)
        refs.append((mod_key, match.group("sym")))
    return refs


def _validate_symbol_in_module(
    rel_path: str,
    line_no: int,
    mod_key: str,
    sym: str,
    root: Path,
    cache: dict[Path, set[str]],
) -> str | None:
    """Verify that mod_key resolves to a Python file whose AST defines sym."""
    src = _resolve_runbook_module_file(mod_key, root)
    if src is None:
        return (
            f"{rel_path}:L{line_no}: Unknown module '{mod_key}' "
            f"in symbol citation '{mod_key}.{sym}'"
        )
    if src not in cache:
        cache[src] = _collect_ast_symbols(src)
    if sym not in cache[src]:
        mod_rel = _RUNBOOK_MODULE_PATHS.get(mod_key, mod_key)
        return (
            f"{rel_path}:L{line_no}: Symbol '{sym}' not found in AST of "
            f"'{mod_key}' ({mod_rel})"
        )
    return None


def _non_fenced_lines(
    lines: list[str],
    blocks: list[CommandBlock],
) -> list[tuple[int, str]]:
    """Return 1-indexed (line_no, text) pairs outside fenced code blocks."""
    fenced = {
        ln for block in blocks for ln in range(block.start_line, block.end_line + 1)
    }
    return [(idx, text) for idx, text in enumerate(lines, start=1) if idx not in fenced]


def _check_runbook_line_citations(
    rel_path: str,
    lines: list[str],
    non_fenced: list[tuple[int, str]],
) -> list[str]:
    """Reject raw :L<line> or L<start>-L<end> line citations in runbooks."""
    errors = [
        f"{rel_path}:L{idx}: Raw line citation '{match.group(0)}' is forbidden; use a module.symbol citation"
        for idx, line in enumerate(lines, start=1)
        for match in _RAW_COLON_LINE_RE.finditer(line)
    ]
    errors.extend(
        f"{rel_path}:L{idx}: Raw line citation '{match.group(0)}' is forbidden; use a module.symbol citation"
        for idx, line in non_fenced
        for match in _RAW_BARE_RANGE_RE.finditer(line)
    )
    return errors


def verify_runbook_citations(
    root: Path = ROOT,
    blocks: list[CommandBlock] | None = None,
) -> list[str]:
    """Verify docs/runbooks.md contains AST-valid symbol citations and zero raw line citations."""
    rel_path = "docs/runbooks.md"
    doc_path = root / rel_path
    if not doc_path.is_file():
        return [f"{rel_path}: File does not exist"]
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    active_blocks = (
        blocks if blocks is not None else extract_blocks_from_file(doc_path, root=root)
    )
    non_fenced = _non_fenced_lines(lines, active_blocks)
    errors = _check_runbook_line_citations(rel_path, lines, non_fenced)
    cache: dict[Path, set[str]] = {}
    citation_count = 0
    for line_no, line in non_fenced:
        for mod_key, sym in _extract_line_symbol_citations(line):
            citation_count += 1
            err = _validate_symbol_in_module(
                rel_path, line_no, mod_key, sym, root, cache
            )
            if err is not None:
                errors.append(err)
    if citation_count == 0:
        errors.append(f"{rel_path}:L1: No module.symbol citations found in runbooks")
    return errors


def _verify_file_snippets(
    rel_path: str,
    blocks: list[CommandBlock],
    root: Path,
    should_execute: bool = True,
) -> list[str]:
    """Verify code snippet commands, non-shell blocks, and release counts in a file."""
    doc_path = root / rel_path
    if not doc_path.is_file():
        return [f"{rel_path}: File does not exist"]
    if not should_execute:
        return []

    errors = _verify_doc_release_counts(rel_path, root)
    if rel_path == "docs/runbooks.md":
        errors.extend(verify_runbook_citations(root=root, blocks=blocks))
    for block in blocks:
        errors.extend(_verify_single_block(rel_path, block, root))
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

    ranges_to_check: Mapping[str, tuple[int | None, int | None]] | None = None
    if target_rel_paths is not None:
        ranges_to_check = {
            rel_path: DOCUMENTED_RANGES.get(rel_path, (None, None))
            for rel_path in sorted(target_rel_paths)
        }

    blocks_by_file = discover_doc_blocks(root=root, ranges=ranges_to_check)
    for rel_path, blocks in blocks_by_file.items():
        file_errors = _verify_file_snippets(rel_path, blocks, root, should_execute=True)
        errors.extend(file_errors)

    return errors


def _print_cli_exception(exc: FredCliError | subprocess.TimeoutExpired) -> None:
    if isinstance(exc, FredCliError):
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"Hint: {exc.hint}", file=sys.stderr)
        return
    cmd_display = (
        " ".join(str(part) for part in exc.cmd)
        if isinstance(exc.cmd, (list, tuple))
        else str(exc.cmd)
    )
    print(
        f"Error [subprocess_timeout]: Command timed out after {exc.timeout:g}s: {cmd_display}",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    try:
        errors = verify_all_snippets(root=ROOT, files=args.files or None)
    except (FredCliError, subprocess.TimeoutExpired) as exc:
        _print_cli_exception(exc)
        return 1

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

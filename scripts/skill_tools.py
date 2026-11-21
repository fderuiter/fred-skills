"""Small authoring utilities. No agent host or network access required."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from html import unescape
from pathlib import Path
from typing import Any, Literal, TypeVar, overload
from urllib.parse import unquote, urlsplit

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _markdown_utils import (
    count_fenced_code_blocks,
    extract_markdown_headings,
    extract_markdown_preamble,
    extract_markdown_section,
    extract_markdown_tables,
    extract_reference_link_targets,
    match_atx_heading,
    non_fenced_lines,
    slugify_markdown_heading,
    split_frontmatter_block,
    unique_heading_slug,
)

__all__ = [
    "DEFAULT_GIT_TIMEOUT",
    "DOMAIN_KIND_KEY_SETS",
    "DOMAIN_KIND_TO_SUITE",
    "ROOT",
    "SKILL_SUITE_PREFIXES",
    "FredCliError",
    "build_outputs",
    "classify_domain_payload",
    "count_fenced_code_blocks",
    "detect_artifact_suite",
    "detect_domain_artifact",
    "estimate_tokens",
    "extract_document_anchors",
    "extract_link_fragment",
    "extract_markdown_headings",
    "extract_markdown_preamble",
    "extract_markdown_section",
    "extract_markdown_tables",
    "extract_relative_links",
    "find_deep_reference_chains",
    "git_blob_bytes",
    "git_tag_exists",
    "infer_suite_and_siblings",
    "infer_suite_name",
    "is_audit_ledger_shape",
    "read_frontmatter",
    "read_json_object",
    "read_suites_catalog",
    "read_utf8_text",
    "recipe_to_suite_map",
    "resolve_relative_link_target",
    "run_bounded_subprocess",
    "run_local_git",
    "skill_to_suite_map",
    "slugify_markdown_heading",
    "suite_for_skills",
    "suite_members",
    "suite_skills_by_suite",
    "workflow_phase_deliverables",
    "workflow_phase_order_map",
]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GIT_TIMEOUT = 30


class FredCliError(ValueError):
    """Structured CLI and boundary validation error with an actionable recovery hint."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "cli_error",
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
        }


@overload
def run_bounded_subprocess(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    text: Literal[True],
    check: bool = False,
    capture_output: bool = True,
    timeout_code: str = "subprocess_timeout",
    timeout_hint: str | None = None,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_bounded_subprocess(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    text: Literal[False] = False,
    check: bool = False,
    capture_output: bool = True,
    timeout_code: str = "subprocess_timeout",
    timeout_hint: str | None = None,
) -> subprocess.CompletedProcess[bytes]: ...


def run_bounded_subprocess(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = False,
    check: bool = False,
    capture_output: bool = True,
    timeout_code: str = "subprocess_timeout",
    timeout_hint: str | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run a child process with an explicit timeout, converting TimeoutExpired to FredCliError."""
    argv = [str(part) for part in cmd]
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=check,
        )
    except subprocess.TimeoutExpired as exc:
        display = " ".join(argv)
        raise FredCliError(
            f"Command timed out after {timeout:g}s: {display}",
            code=timeout_code,
            hint=timeout_hint
            or "Retry once background filesystem locks or stalled child processes clear.",
        ) from exc


@overload
def run_local_git(
    repo: Path,
    *args: str,
    timeout: float = DEFAULT_GIT_TIMEOUT,
    text: Literal[True],
    check: bool = False,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_local_git(
    repo: Path,
    *args: str,
    timeout: float = DEFAULT_GIT_TIMEOUT,
    text: Literal[False] = False,
    check: bool = False,
) -> subprocess.CompletedProcess[bytes]: ...


def run_local_git(
    repo: Path,
    *args: str,
    timeout: float = DEFAULT_GIT_TIMEOUT,
    text: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Execute a local Git command with core.fsmonitor=false and a bounded timeout."""
    cmd = ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args]
    hint = (
        "Git timed out inspecting the local repository; check for stale "
        ".git/index.lock files or stalled filesystem monitors and retry."
    )
    if text:
        return run_bounded_subprocess(
            cmd,
            timeout=timeout,
            text=True,
            check=check,
            timeout_code="git_timeout",
            timeout_hint=hint,
        )
    return run_bounded_subprocess(
        cmd,
        timeout=timeout,
        text=False,
        check=check,
        timeout_code="git_timeout",
        timeout_hint=hint,
    )


def git_blob_bytes(
    repo: Path,
    rev_path: str,
    *,
    timeout: float = DEFAULT_GIT_TIMEOUT,
) -> bytes | None:
    """Read raw blob bytes at rev_path from the local repository, or None when absent."""
    if not (repo / ".git").exists() or not shutil.which("git"):
        return None
    proc = run_local_git(
        repo,
        "cat-file",
        "blob",
        rev_path,
        timeout=timeout,
        text=False,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_tag_exists(
    repo: Path,
    tag: str,
    *,
    timeout: float = DEFAULT_GIT_TIMEOUT,
) -> bool:
    """Return True when refs/tags/<tag> resolves to a commit in the local repository."""
    if not (repo / ".git").exists() or not shutil.which("git"):
        return False
    proc = run_local_git(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/tags/{tag}^{{commit}}",
        timeout=timeout,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _resolve_hint(hint: str | None, default: str) -> str:
    return hint if hint is not None else default


def _handle_missing_utf8_file(
    p: Path,
    label: str,
    hint: str | None,
    *,
    optional: bool,
    exc: FileNotFoundError,
) -> None:
    if optional:
        if not p.is_symlink():
            return
        raise FredCliError(
            f"Unable to read {label.lower()} '{p}': dangling symlink",
            code="io_error",
            hint=_resolve_hint(
                hint, f"Check symlink target and readability for '{p}'."
            ),
        ) from exc
    raise FredCliError(
        f"{label} not found: {p}",
        code="missing_file",
        hint=_resolve_hint(
            hint, f"Verify that '{p}' exists and check the path spelling."
        ),
    ) from exc


@overload
def read_utf8_text(
    path: Path | str,
    *,
    label: str = "UTF-8 file",
    hint: str | None = None,
    optional: Literal[False] = False,
) -> str: ...


@overload
def read_utf8_text(
    path: Path | str,
    *,
    label: str = "UTF-8 file",
    hint: str | None = None,
    optional: Literal[True],
) -> str | None: ...


@overload
def read_utf8_text(
    path: Path | str,
    *,
    label: str = "UTF-8 file",
    hint: str | None = None,
    optional: bool = False,
) -> str | None: ...


def read_utf8_text(
    path: Path | str,
    *,
    label: str = "UTF-8 file",
    hint: str | None = None,
    optional: bool = False,
) -> str | None:
    """Read UTF-8 text from path, returning None only when an optional file is absent."""
    p = path if isinstance(path, Path) else Path(path)
    if p.is_dir():
        raise FredCliError(
            f"Expected {label.lower()} at '{p}', got a directory",
            code="invalid_path_type",
            hint=_resolve_hint(hint, f"Pass a file path instead of directory '{p}'."),
        )
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        _handle_missing_utf8_file(p, label, hint, optional=optional, exc=exc)
        return None
    except OSError as exc:
        raise FredCliError(
            f"Unable to read {label.lower()} '{p}': {exc}",
            code="io_error",
            hint=_resolve_hint(
                hint, f"Check file permissions and readability for '{p}'."
            ),
        ) from exc
    except UnicodeDecodeError as exc:
        raise FredCliError(
            f"Invalid UTF-8 encoding in {label.lower()} '{p}': {exc}",
            code="invalid_encoding",
            hint=_resolve_hint(hint, f"Re-encode '{p}' as valid UTF-8."),
        ) from exc


def _json_dir_hint(p: Path, hint: str | None) -> str | None:
    if hint is not None:
        return hint
    return f"Pass a JSON file path instead of directory '{p}'." if p.is_dir() else None


@overload
def read_json_object(
    path: Path | str,
    *,
    label: str = "JSON file",
    hint: str | None = None,
    optional: Literal[False] = False,
) -> dict[str, Any]: ...


@overload
def read_json_object(
    path: Path | str,
    *,
    label: str = "JSON file",
    hint: str | None = None,
    optional: Literal[True],
) -> dict[str, Any] | None: ...


@overload
def read_json_object(
    path: Path | str,
    *,
    label: str = "JSON file",
    hint: str | None = None,
    optional: bool = False,
) -> dict[str, Any] | None: ...


def read_json_object(
    path: Path | str,
    *,
    label: str = "JSON file",
    hint: str | None = None,
    optional: bool = False,
) -> dict[str, Any] | None:
    """Read and parse a JSON object from path, returning None only when an optional file is absent."""
    p = path if isinstance(path, Path) else Path(path)
    raw_text = read_utf8_text(
        p,
        label=label,
        hint=_json_dir_hint(p, hint),
        optional=optional,
    )
    if raw_text is None:
        return None
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FredCliError(
            f"Malformed JSON in {label.lower()} '{p}' (line {exc.lineno}, column {exc.colno}): {exc.msg}",
            code="invalid_json",
            hint=_resolve_hint(
                hint, f"Fix the JSON syntax in '{p}' near line {exc.lineno}."
            ),
        ) from exc
    if not isinstance(data, dict):
        actual = type(data).__name__
        raise FredCliError(
            f"Expected JSON object in {label.lower()} '{p}', got {actual}",
            code="invalid_json_root",
            hint=_resolve_hint(
                hint, f"Ensure the root value in '{p}' is a JSON object ({{...}})."
            ),
        )
    return data


# Matches markdown links [text](target), images ![text](target)
MD_LINK_RE = re.compile(r"!?\[.*?\]\(([^)]+)\)")
# Matches HTML href="..." or src="..."
HTML_LINK_RE = re.compile(r"""(?:href|src)=["\x27]([^"\x27]+)["\x27]""")
_HTML_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_HTML_ID_ATTR_RE = re.compile(r"""\bid=["\x27]([^"\x27]+)["\x27]""")
_HTML_NAME_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*\bname=["\x27]([^"\x27]+)["\x27]""", re.IGNORECASE
)


def estimate_tokens(text: str) -> int:
    """Estimate token count offline using character and whitespace-word bounds."""
    stripped = text.strip()
    if not stripped:
        return 0
    return max(math.ceil(len(stripped) / 4), len(stripped.split()))


def extract_relative_links(
    content: str,
    include_fragments: bool = False,
) -> list[str]:
    """Extract all relative link targets (and optional #fragment links) from content."""
    raw_targets = (
        MD_LINK_RE.findall(content)
        + HTML_LINK_RE.findall(content)
        + extract_reference_link_targets(content)
    )
    relative_paths: list[str] = []
    for target in raw_targets:
        target = target.strip().strip("<>\"'")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        if not parsed.path:
            if include_fragments and parsed.fragment:
                relative_paths.append(target)
            continue
        relative_paths.append(target)
    return relative_paths


def extract_link_fragment(target: str) -> str | None:
    """Extract an unquoted #fragment identifier from a relative or in-page link target."""
    target = target.strip().strip("<>\"'")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.fragment:
        return None
    return unquote(parsed.fragment)


def _extract_html_anchors(content: str) -> set[str]:
    """Extract id and <a name=...> anchor targets from HTML text outside script/style."""
    cleaned = _HTML_SCRIPT_STYLE_RE.sub("", content)
    raw_anchors = _HTML_ID_ATTR_RE.findall(cleaned) + _HTML_NAME_ANCHOR_RE.findall(
        cleaned
    )
    anchors: set[str] = set()
    for item in raw_anchors:
        decoded = unescape(item).strip()
        if decoded:
            anchors.add(decoded)
            anchors.add(unquote(decoded))
    return anchors


def extract_document_anchors(content: str, suffix: str = ".md") -> set[str]:
    """Extract all valid #fragment anchor identifiers from a Markdown or HTML document."""
    if suffix.lower() in {".html", ".htm"}:
        return _extract_html_anchors(content)

    anchors: set[str] = set()
    seen_slugs: dict[str, int] = {}

    for _, line in non_fenced_lines(content):
        heading = match_atx_heading(line, max_indent=None)
        if heading is not None:
            anchors.add(unique_heading_slug(heading[1], seen_slugs))

        for raw_attr in _HTML_ID_ATTR_RE.findall(line) + _HTML_NAME_ANCHOR_RE.findall(
            line
        ):
            decoded = unescape(raw_attr).strip()
            if decoded:
                anchors.add(decoded)

    return anchors


def resolve_relative_link_target(target: str, base_dir: Path) -> Path | None:
    """Resolve a relative markdown link target to an absolute filesystem Path."""
    target = target.strip().strip("<>\"'")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    raw_path = unquote(parsed.path)
    clean_path = raw_path.split("#")[0].split("?")[0]
    if not clean_path:
        return None
    return (base_dir / clean_path).resolve()


def _format_package_rel(path: Path, package_root: Path, fallback: str) -> str:
    if path.is_relative_to(package_root):
        return path.relative_to(package_root).as_posix()
    return fallback


def _record_chain_violation(
    chain_str: str,
    violations: list[str],
    seen_violations: set[str],
) -> None:
    if chain_str not in seen_violations:
        seen_violations.add(chain_str)
        violations.append(chain_str)


def _read_chain_markdown(
    path: Path,
    rel_label: str,
    read_errors: list[str] | None,
) -> str | None:
    try:
        return read_utf8_text(path, label=f"Markdown file '{rel_label}'")
    except FredCliError as exc:
        if read_errors is None:
            raise
        if exc.code == "invalid_encoding":
            msg = f"Binary or non-UTF-8 file in skill package: {rel_label}"
        else:
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            msg = f"{rel_label}: {cause}"
        if msg not in read_errors:
            read_errors.append(msg)
        return None


def _is_traversable_package_markdown(
    resolved: Path,
    package_root: Path,
    path_chain: tuple[Path, ...],
) -> bool:
    if resolved.suffix.lower() != ".md" or resolved in path_chain:
        return False
    if not resolved.is_relative_to(package_root):
        return False
    return resolved.is_file() and not resolved.is_symlink()


def _expand_entry_markdown_links(
    current: Path,
    path_chain: tuple[Path, ...],
    label_chain: tuple[str, ...],
    *,
    package_root: Path,
    max_hops: int,
    queue: deque[tuple[Path, tuple[Path, ...], tuple[str, ...]]],
    violations: list[str],
    seen_violations: set[str],
    read_errors: list[str] | None,
) -> None:
    content = _read_chain_markdown(current, label_chain[-1], read_errors)
    if content is None:
        return
    next_depth = len(path_chain)
    for raw_target in extract_relative_links(content):
        resolved = resolve_relative_link_target(raw_target, current.parent)
        if resolved is None:
            continue
        next_label = _format_package_rel(resolved, package_root, raw_target)
        if next_depth > max_hops:
            chain_str = " -> ".join((*label_chain, next_label))
            _record_chain_violation(chain_str, violations, seen_violations)
        elif _is_traversable_package_markdown(resolved, package_root, path_chain):
            queue.append(
                (resolved, (*path_chain, resolved), (*label_chain, next_label))
            )


def _walk_entry_reference_chains(
    entry_path: Path,
    package_root: Path,
    max_hops: int,
    visited_markdown: set[Path],
    violations: list[str],
    seen_violations: set[str],
    read_errors: list[str] | None,
) -> None:
    if not entry_path.is_file() or entry_path.is_symlink():
        return
    entry_resolved = entry_path.resolve()
    queue: deque[tuple[Path, tuple[Path, ...], tuple[str, ...]]] = deque(
        [(entry_resolved, (entry_resolved,), ("SKILL.md",))]
    )
    while queue:
        current, path_chain, label_chain = queue.popleft()
        visited_markdown.add(current)
        _expand_entry_markdown_links(
            current,
            path_chain,
            label_chain,
            package_root=package_root,
            max_hops=max_hops,
            queue=queue,
            violations=violations,
            seen_violations=seen_violations,
            read_errors=read_errors,
        )


def _collect_orphan_reference_violations(
    ref_md: Path,
    package_root: Path,
    violations: list[str],
    seen_violations: set[str],
    read_errors: list[str] | None,
) -> None:
    ref_label = _format_package_rel(ref_md.resolve(), package_root, ref_md.as_posix())
    ref_content = _read_chain_markdown(ref_md, ref_label, read_errors)
    if ref_content is None:
        return
    for raw_target in extract_relative_links(ref_content):
        resolved = resolve_relative_link_target(raw_target, ref_md.parent)
        if resolved is None:
            continue
        target_label = _format_package_rel(resolved, package_root, raw_target)
        chain_str = f"SKILL.md -> {ref_label} -> {target_label}"
        _record_chain_violation(chain_str, violations, seen_violations)


def _scan_unvisited_reference_files(
    references_dir: Path,
    package_root: Path,
    visited_markdown: set[Path],
    violations: list[str],
    seen_violations: set[str],
    read_errors: list[str] | None,
) -> None:
    if not references_dir.is_dir() or references_dir.is_symlink():
        return
    for ref_md in sorted(references_dir.rglob("*.md")):
        if ref_md.is_symlink() or not ref_md.is_file():
            continue
        if ref_md.resolve() in visited_markdown:
            continue
        _collect_orphan_reference_violations(
            ref_md, package_root, violations, seen_violations, read_errors
        )


def find_deep_reference_chains(
    package: Path,
    max_hops: int = 1,
    *,
    read_errors: list[str] | None = None,
) -> list[str]:
    """Return markdown reference chains exceeding max_hops from SKILL.md."""
    if max_hops < 1:
        raise ValueError("max_hops must be at least 1")
    package_root = package.resolve()
    violations: list[str] = []
    seen_violations: set[str] = set()
    visited_markdown: set[Path] = set()
    _walk_entry_reference_chains(
        package / "SKILL.md",
        package_root,
        max_hops,
        visited_markdown,
        violations,
        seen_violations,
        read_errors,
    )
    _scan_unvisited_reference_files(
        package / "references",
        package_root,
        visited_markdown,
        violations,
        seen_violations,
        read_errors,
    )
    return violations


_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+|\"[^\"]+\"|'[^']+')\s*:(.*)$")
_YAML_INT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")
_YAML_FLOAT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")
_YAML_LITERAL_SCALARS: dict[str, Any] = {
    "true": True,
    "True": True,
    "TRUE": True,
    "false": False,
    "False": False,
    "FALSE": False,
    "null": None,
    "Null": None,
    "NULL": None,
    "~": None,
}


def _is_yaml_comment_start(
    raw: str, idx: int, in_single: bool, in_double: bool
) -> bool:
    if in_single or in_double:
        return False
    return idx == 0 or raw[idx - 1].isspace()


def _advance_yaml_quote_state(
    ch: str, in_single: bool, in_double: bool
) -> tuple[bool, bool, bool]:
    if ch == "\\" and in_double:
        return in_single, in_double, True
    if ch == "'" and not in_double:
        return not in_single, in_double, False
    if ch == '"' and not in_single:
        return in_single, not in_double, False
    return in_single, in_double, False


def _strip_yaml_inline_comment(raw: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "#" and _is_yaml_comment_start(raw, idx, in_single, in_double):
            return raw[:idx].rstrip()
        in_single, in_double, escaped = _advance_yaml_quote_state(
            ch, in_single, in_double
        )
    return raw.strip()


def _parse_quoted_yaml_scalar(cleaned: str, path: Path) -> Any:
    if cleaned.startswith('"'):
        if len(cleaned) < 2 or not cleaned.endswith('"'):
            raise ValueError(f"{path}: unterminated double-quoted YAML scalar")
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid double-quoted YAML scalar") from exc
    if len(cleaned) < 2 or not cleaned.endswith("'"):
        raise ValueError(f"{path}: unterminated single-quoted YAML scalar")
    return cleaned[1:-1].replace("''", "'")


def _parse_unquoted_yaml_scalar(cleaned: str, path: Path) -> Any:
    if cleaned in _YAML_LITERAL_SCALARS:
        return _YAML_LITERAL_SCALARS[cleaned]
    if _YAML_INT_RE.fullmatch(cleaned):
        return int(cleaned)
    if _YAML_FLOAT_RE.fullmatch(cleaned):
        return float(cleaned)
    if cleaned.startswith("[") and cleaned.endswith("]"):
        inner = cleaned[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar_stdlib(item, path) for item in inner.split(",")]
    return cleaned


def _parse_yaml_scalar_stdlib(raw: str, path: Path) -> Any:
    cleaned = _strip_yaml_inline_comment(raw.strip())
    if not cleaned:
        return None
    if cleaned[0] in {'"', "'"}:
        return _parse_quoted_yaml_scalar(cleaned, path)
    return _parse_unquoted_yaml_scalar(cleaned, path)


def _collect_yaml_block_lines(
    raw_lines: list[str], idx: int
) -> tuple[list[tuple[int, str]], int]:
    block_lines: list[tuple[int, str]] = []
    block_indent: int | None = None
    total = len(raw_lines)
    while idx < total:
        sub = raw_lines[idx]
        if not sub.strip():
            block_lines.append((0, ""))
            idx += 1
            continue
        indent = len(sub) - len(sub.lstrip(" "))
        if indent == 0:
            break
        if block_indent is None:
            block_indent = indent
        content = sub[block_indent:] if indent >= block_indent else sub.lstrip(" ")
        block_lines.append((indent, content))
        idx += 1
    return block_lines, idx


def _fold_yaml_block_lines(block_lines: list[tuple[int, str]]) -> str:
    folded_parts: list[str] = []
    for _, part in block_lines:
        if not part:
            folded_parts.append("\n")
        elif folded_parts and not folded_parts[-1].endswith("\n"):
            folded_parts.append(" " + part)
        else:
            folded_parts.append(part)
    return "".join(folded_parts)


def _parse_yaml_block_scalar(
    raw_lines: list[str], idx: int, style: str
) -> tuple[str, int]:
    block_lines, next_idx = _collect_yaml_block_lines(raw_lines, idx)
    if style.endswith("-"):
        while block_lines and block_lines[-1][1] == "":
            block_lines.pop()
    joined = (
        "\n".join(part for _, part in block_lines)
        if style.startswith("|")
        else _fold_yaml_block_lines(block_lines)
    )
    if not style.endswith("-") and block_lines:
        joined = joined.rstrip("\n") + "\n"
    return joined, next_idx


def _collect_indented_yaml_lines(
    raw_lines: list[str], idx: int
) -> tuple[list[str], int]:
    sub_lines: list[str] = []
    total = len(raw_lines)
    while idx < total:
        sub = raw_lines[idx]
        sub_stripped = sub.strip()
        if not sub_stripped or sub_stripped.startswith("#"):
            idx += 1
            continue
        if not sub[0].isspace():
            break
        sub_lines.append(sub)
        idx += 1
    return sub_lines, idx


def _parse_yaml_sequence_lines(sub_lines: list[str], path: Path) -> list[Any]:
    seq: list[Any] = []
    for item_line in sub_lines:
        item_str = item_line.lstrip()
        if not item_str.startswith("- "):
            raise ValueError(f"{path}: invalid YAML sequence item: {item_line!r}")
        seq.append(_parse_yaml_scalar_stdlib(item_str[2:], path))
    return seq


def _parse_yaml_mapping_lines(sub_lines: list[str], path: Path) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for map_line in sub_lines:
        sub_match = _YAML_KEY_RE.match(map_line.lstrip(" "))
        if sub_match is None:
            raise ValueError(f"{path}: invalid nested YAML mapping: {map_line!r}")
        nk = str(_parse_yaml_scalar_stdlib(sub_match.group(1), path))
        nested[nk] = _parse_yaml_scalar_stdlib(sub_match.group(2), path)
    return nested


def _parse_yaml_nested_block(
    raw_lines: list[str], idx: int, path: Path
) -> tuple[Any, int]:
    sub_lines, next_idx = _collect_indented_yaml_lines(raw_lines, idx)
    if not sub_lines:
        return None, next_idx
    if sub_lines[0].lstrip().startswith("- "):
        return _parse_yaml_sequence_lines(sub_lines, path), next_idx
    return _parse_yaml_mapping_lines(sub_lines, path), next_idx


def _parse_yaml_plain_continuation(
    scalar_val: str, raw_lines: list[str], idx: int
) -> tuple[str, int]:
    cont_parts = [scalar_val]
    total = len(raw_lines)
    while idx < total:
        sub = raw_lines[idx]
        sub_stripped = sub.strip()
        if not sub_stripped:
            break
        if sub_stripped.startswith("#"):
            idx += 1
            continue
        if not sub[0].isspace():
            break
        cont_parts.append(_strip_yaml_inline_comment(sub_stripped))
        idx += 1
    return " ".join(p for p in cont_parts if p), idx


def _parse_yaml_entry_value(
    raw_rest: str, raw_lines: list[str], idx: int, path: Path
) -> tuple[Any, int]:
    rest = _strip_yaml_inline_comment(raw_rest.strip())
    if rest in {"|", "|-", "|+", ">", ">-", ">+"}:
        return _parse_yaml_block_scalar(raw_lines, idx, rest)
    if rest == "":
        return _parse_yaml_nested_block(raw_lines, idx, path)
    scalar_val = _parse_yaml_scalar_stdlib(raw_rest, path)
    is_unquoted_str = isinstance(scalar_val, str) and not raw_rest.lstrip().startswith(
        ('"', "'")
    )
    if is_unquoted_str and idx < len(raw_lines):
        assert isinstance(scalar_val, str)
        return _parse_yaml_plain_continuation(scalar_val, raw_lines, idx)
    return scalar_val, idx


def _parse_top_level_frontmatter_line(
    line: str, stripped: str, path: Path
) -> tuple[bool, Any, str]:
    if "\t" in line[: len(line) - len(line.lstrip())]:
        raise ValueError(f"{path}: tab indentation is not allowed in YAML frontmatter")
    if line[0].isspace() or stripped.startswith("- "):
        return False, stripped, ""
    match = _YAML_KEY_RE.match(line)
    if match is None:
        if ":" not in stripped:
            return False, _parse_yaml_scalar_stdlib(stripped, path), ""
        raise ValueError(f"{path}: invalid YAML frontmatter line: {line!r}")
    key = str(_parse_yaml_scalar_stdlib(match.group(1), path))
    return True, key, match.group(2)


def _parse_frontmatter_stdlib(frontmatter_text: str, path: Path) -> Any:
    raw_lines = frontmatter_text.splitlines()
    result: dict[str, Any] = {}
    saw_content = False
    idx = 0
    total = len(raw_lines)
    while idx < total:
        line = raw_lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        saw_content = True
        is_kv, key_or_val, raw_rest = _parse_top_level_frontmatter_line(
            line, stripped, path
        )
        if not is_kv:
            return key_or_val
        result[key_or_val], idx = _parse_yaml_entry_value(
            raw_rest, raw_lines, idx + 1, path
        )
    return result if saw_content else None


def read_frontmatter(path: Path) -> tuple[dict, str]:
    raw_fm, body = split_frontmatter_block(
        path.read_text(encoding="utf-8"),
        label=path,
        require=True,
    )
    assert raw_fm is not None
    try:
        import yaml as _yaml
    except ModuleNotFoundError:
        metadata = _parse_frontmatter_stdlib(raw_fm, path)
    else:
        metadata = _yaml.safe_load(raw_fm)
    if not isinstance(metadata, dict):
        raise TypeError(f"{path}: frontmatter must be a mapping")
    return metadata, body


def _is_valid_json_basename(filename: object) -> bool:
    if not isinstance(filename, str) or not filename.endswith(".json"):
        return False
    return Path(filename).name == filename and "\\" not in filename


def _validate_suite_artifact_schema(
    suite_name: object, schema: object, root: Path
) -> None:
    if not isinstance(schema, str) or not schema.endswith(".schema.json"):
        raise ValueError(
            f"Suite '{suite_name}' artifact schema must name a .schema.json path"
        )
    if (root / "skills").is_dir() and (root / "shared").is_dir():
        schema_file = root / schema
        if schema_file.is_symlink() or not schema_file.is_file():
            raise ValueError(f"Suite '{suite_name}' missing artifact schema: {schema}")


def _validate_suite_artifact(suite: dict, root: Path) -> None:
    artifact = suite.get("artifact")
    if artifact is None:
        return
    suite_name = suite.get("name")
    if not isinstance(artifact, dict):
        raise TypeError(f"Suite '{suite_name}' artifact must be an object")
    if not _is_valid_json_basename(artifact.get("filename")):
        raise ValueError(
            f"Suite '{suite_name}' artifact filename must be a JSON basename"
        )
    _validate_suite_artifact_schema(suite_name, artifact.get("schema"), root)
    starter = artifact.get("starter")
    if not isinstance(starter, dict) or not starter:
        raise ValueError(
            f"Suite '{suite_name}' artifact starter must be a nonempty mapping"
        )


SKILL_SUITE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("repo-audit-", "repository-audits"),
    ("arch-", "software-architecture"),
    ("review-", "code-review"),
    ("test-", "automated-testing"),
    ("spec-", "spec-driven-development"),
    ("repo-", "repository-workflows"),
)

DOMAIN_KIND_KEY_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("remediation", frozenset({"records"})),
    (
        "reconciliation",
        frozenset(
            {
                "finding_fingerprint_matches",
                "fingerprint_matches",
                "remediation_state_transitions",
                "state_transitions",
                "unmapped_records",
            }
        ),
    ),
    (
        "finding-verification",
        frozenset(
            {
                "reproduction_status",
                "safe_control_outcomes",
                "confidence_rating",
            }
        ),
    ),
    (
        "regression",
        frozenset(
            {
                "failure_boundaries",
                "failure_boundary",
                "test_command_traces",
                "assertion_expectations",
            }
        ),
    ),
    (
        "plan",
        frozenset(
            {
                "task_breakdowns",
                "dependency_order",
                "file_scopes",
                "plan_id",
            }
        ),
    ),
    (
        "fix",
        frozenset(
            {
                "file_changes",
                "diff_summaries",
                "diff_summary",
                "execution_results",
                "compatibility_limits",
            }
        ),
    ),
    (
        "verification",
        frozenset(
            {
                "original_reproduced",
                "regression_passed",
                "before_revision",
                "after_revision",
                "safe_controls_passed",
            }
        ),
    ),
    ("review", frozenset({"verdict"})),
    ("testing", frozenset({"test_cases"})),
    (
        "spec",
        frozenset(
            {
                "interview_decisions",
                "new_file_scopes",
                "out_of_scope",
                "verification_commands",
            }
        ),
    ),
    ("architecture", frozenset({"boundaries", "decisions", "components"})),
    ("observations", frozenset({"observations"})),
    ("run", frozenset({"plan", "authority", "checkpoints"})),
)

DOMAIN_KIND_TO_SUITE: dict[str, str] = {
    "architecture": "software-architecture",
    "review": "code-review",
    "testing": "automated-testing",
    "spec": "spec-driven-development",
    "remediation": "repository-workflows",
    "reconciliation": "repository-workflows",
    "finding-verification": "repository-workflows",
    "regression": "repository-workflows",
    "plan": "repository-workflows",
    "fix": "repository-workflows",
    "verification": "repository-workflows",
    "observations": "repository-workflows",
}


def infer_suite_name(
    skill_name: str,
    available_suites: Sequence[str] | None = None,
) -> str:
    """Infer the target suite name from a skill prefix and optional available suites."""
    available = set(available_suites) if available_suites else set()
    for prefix, suite_name in SKILL_SUITE_PREFIXES:
        if skill_name.startswith(prefix) and (not available or suite_name in available):
            return suite_name
    if "repository-workflows" in available:
        return "repository-workflows"
    if available_suites:
        return available_suites[0]
    return "repository-audits"


def _phase_sort_key(item: tuple[int, Any]) -> tuple[int, int]:
    idx, phase = item
    if isinstance(phase, dict) and isinstance(phase.get("id"), int):
        return (0, phase["id"])
    return (1, idx)


def _order_suite_phases(
    suite: dict[str, Any], suite_name: str, *, strict: bool
) -> list[Any] | None:
    phases = suite["phases"] if strict else suite.get("phases", [])
    if not isinstance(phases, list):
        if strict:
            raise TypeError(f"Suite '{suite_name}' phases must be a list")
        return None
    if strict:
        return phases
    return [phase for _, phase in sorted(enumerate(phases), key=_phase_sort_key)]


def _iter_phase_skills(
    suite_name: str, phase: Any, *, strict: bool
) -> Iterator[tuple[str, dict[str, Any], Any]]:
    if not isinstance(phase, dict):
        if strict:
            raise TypeError(f"Suite '{suite_name}' phase must be an object")
        return
    skills = phase["skills"] if strict else phase.get("skills", [])
    if not isinstance(skills, list):
        if strict:
            raise TypeError(f"Suite '{suite_name}' phase skills must be a list")
        return
    for skill in skills:
        if strict or (isinstance(skill, str) and skill):
            yield suite_name, phase, skill


def _resolve_suite_entry_name(
    suite: Any, *, strict: bool, root: Path
) -> tuple[dict[str, Any], str] | None:
    if not isinstance(suite, dict):
        if strict:
            raise TypeError("Suite entry must be an object")
        return None
    if strict:
        _validate_suite_artifact(suite, root)
    suite_name = suite["name"] if strict else suite.get("name")
    if not isinstance(suite_name, str) or not suite_name:
        if strict:
            raise ValueError(f"Invalid suite name: {suite_name!r}")
        return None
    return suite, suite_name


def _iter_suite_phase_skills(
    suites: Iterable[Any],
    *,
    strict: bool = False,
    root: Path = ROOT,
) -> Iterator[tuple[str, dict[str, Any] | None, Any]]:
    for raw_suite in suites:
        resolved = _resolve_suite_entry_name(raw_suite, strict=strict, root=root)
        if resolved is None:
            continue
        suite, suite_name = resolved
        yield suite_name, None, None
        ordered_phases = _order_suite_phases(suite, suite_name, strict=strict)
        if ordered_phases is None:
            continue
        for phase in ordered_phases:
            yield from _iter_phase_skills(suite_name, phase, strict=strict)


def read_suites_catalog(root: Path = ROOT) -> list[dict[str, Any]] | None:
    """Read catalog/suites.json, returning suite objects or None when absent."""
    suites_path = root / "catalog/suites.json"
    doc = read_json_object(
        suites_path,
        label="Suites catalog 'catalog/suites.json'",
        optional=True,
    )
    if doc is None:
        return None
    suites = doc.get("suites")
    if not isinstance(suites, list):
        raise FredCliError(
            f"Malformed suites catalog '{suites_path}': expected 'suites' array",
            code="invalid_catalog",
            hint=f"Ensure '{suites_path}' contains a JSON array under 'suites'.",
        )
    result: list[dict[str, Any]] = []
    for suite in suites:
        if not isinstance(suite, dict):
            raise FredCliError(
                f"Malformed suites catalog '{suites_path}': each suite entry must be an object",
                code="invalid_catalog",
                hint=f"Ensure every item in 'suites' in '{suites_path}' is a JSON object.",
            )
        result.append(suite)
    return result


def suite_skills_by_suite(root: Path = ROOT) -> dict[str, tuple[str, ...]] | None:
    """Return ordered deduplicated skills per suite, or None when catalog/suites.json is absent."""
    suites = read_suites_catalog(root)
    if suites is None:
        return None
    grouped: dict[str, list[str]] = {}
    for suite_name, _phase, skill in _iter_suite_phase_skills(
        suites, strict=False, root=root
    ):
        bucket = grouped.setdefault(suite_name, [])
        if isinstance(skill, str) and skill not in bucket:
            bucket.append(skill)
    return {name: tuple(members) for name, members in grouped.items()}


def skill_to_suite_map(root: Path = ROOT) -> dict[str, str]:
    """Map each skill registered in catalog/suites.json to its owning suite name."""
    by_suite = suite_skills_by_suite(root)
    if not by_suite:
        return {}
    return {
        skill: suite_name
        for suite_name, members in by_suite.items()
        for skill in members
    }


def workflow_phase_deliverables(root: Path = ROOT) -> dict[str, str]:
    """Return deliverable filename -> schema path registered on repository-workflows phases."""
    suites = read_suites_catalog(root)
    if suites is None:
        return {}
    mapping: dict[str, str] = {}
    for suite_name, phase, _skill in _iter_suite_phase_skills(
        suites, strict=False, root=root
    ):
        if suite_name != "repository-workflows" or not isinstance(phase, dict):
            continue
        deliverable = phase.get("deliverable")
        schema_rel = phase.get("schema")
        if isinstance(deliverable, str) and isinstance(schema_rel, str):
            mapping[deliverable] = schema_rel
    return mapping


def workflow_phase_order_map(root: Path = ROOT) -> dict[str, int]:
    """Map each repository-workflows skill in catalog/suites.json to its phase order."""
    suites = read_suites_catalog(root)
    if suites is None:
        return {}
    order_map: dict[str, int] = {}
    for suite_name, phase, skill in _iter_suite_phase_skills(
        suites, strict=False, root=root
    ):
        if suite_name != "repository-workflows" or not isinstance(phase, dict):
            continue
        order = phase.get("order")
        if isinstance(order, int) and isinstance(skill, str):
            order_map[skill] = order
    return order_map


def _load_strict_suites_list(suites_path: Path) -> list[Any]:
    document = read_json_object(
        suites_path,
        label="Suites catalog 'catalog/suites.json'",
    )
    raw_suites = document.get("suites")
    if not isinstance(raw_suites, list):
        raise FredCliError(
            f"Malformed suites catalog '{suites_path}': expected 'suites' array",
            code="invalid_catalog",
            hint=f"Ensure '{suites_path}' contains a JSON array under 'suites'.",
        )
    return raw_suites


def suite_members(root: Path = ROOT) -> dict[str, dict]:
    raw_suites = _load_strict_suites_list(root / "catalog/suites.json")
    members: dict[str, dict] = {}
    for suite_name, phase, name in _iter_suite_phase_skills(
        raw_suites, strict=True, root=root
    ):
        if phase is None:
            continue
        if name in members:
            raise ValueError(f"Duplicate suite membership: {name}")
        # Manifest entries are identifiers, not arbitrary filesystem paths.
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in {".", ".."}
            or "\\" in name
        ):
            raise ValueError(f"Invalid skill identifier: {name!r}")
        members[name] = {
            "suite": suite_name,
            "phase": phase["id"],
            "phase_title": phase["title"],
        }
    return members


def _load_suite_members_strict(root: Path, suites_path: Path) -> dict[str, dict]:
    try:
        return suite_members(root)
    except FredCliError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise FredCliError(
            f"Malformed suites catalog '{suites_path}': {exc}",
            code="invalid_catalog",
            hint=f"Fix suite definitions in '{suites_path}'.",
        ) from exc


def infer_suite_and_siblings(
    skill_name: str,
    root: Path | None = None,
) -> tuple[str, set[str]]:
    """Resolve a skill's suite and sibling set from catalog/suites.json or prefix fallback."""
    if root is not None:
        suites_path = root / "catalog/suites.json"
        if suites_path.exists() or suites_path.is_symlink():
            members = _load_suite_members_strict(root, suites_path)
            if skill_name in members:
                suite_name = str(members[skill_name]["suite"])
                siblings = {
                    s
                    for s, info in members.items()
                    if info.get("suite") == suite_name and s != skill_name
                }
                return suite_name, siblings
    return infer_suite_name(skill_name), set()


def _is_valid_skill_suite_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    return isinstance(row.get("name"), str) and isinstance(row.get("suite"), str)


def _fallback_skill_suites_from_catalog(root: Path) -> dict[str, str]:
    skills_path = root / "catalog/skills.json"
    doc = read_json_object(
        skills_path,
        label="Skills catalog 'catalog/skills.json'",
        optional=True,
    )
    if doc is None:
        return {}
    raw_skills = doc.get("skills")
    if not isinstance(raw_skills, list):
        raise FredCliError(
            f"Malformed skills catalog '{skills_path}': expected 'skills' array",
            code="invalid_catalog",
            hint=f"Ensure '{skills_path}' contains a JSON array under 'skills'.",
        )
    return {
        row["name"]: row["suite"]
        for row in raw_skills
        if _is_valid_skill_suite_row(row)
    }


def suite_for_skills(
    skills: Iterable[str],
    root: Path = ROOT,
    *,
    skill_suites: Mapping[str, str] | None = None,
) -> str:
    """Resolve the primary suite for an ordered skill list, preferring repository-audits."""
    resolved_map = (
        dict(skill_suites) if skill_suites is not None else skill_to_suite_map(root)
    )
    if not resolved_map:
        resolved_map = _fallback_skill_suites_from_catalog(root)
    suites = list(dict.fromkeys(resolved_map[s] for s in skills if s in resolved_map))
    if "repository-audits" in suites:
        return "repository-audits"
    return suites[0] if suites else "repository-audits"


def _resolve_recipe_suite(
    recipe_obj: dict[str, Any], root: Path, s_map: dict[str, str]
) -> str:
    explicit_suite = recipe_obj.get("suite")
    if isinstance(explicit_suite, str) and explicit_suite:
        return explicit_suite
    raw_skills = recipe_obj.get("skills", [])
    str_skills = (
        [s for s in raw_skills if isinstance(s, str)]
        if isinstance(raw_skills, list)
        else []
    )
    return suite_for_skills(str_skills, root=root, skill_suites=s_map or None)


def _register_recipe_aliases(
    mapping: dict[str, str], raw_aliases: Any, resolved_suite: str
) -> None:
    if not isinstance(raw_aliases, list):
        return
    for alias in raw_aliases:
        if isinstance(alias, str) and alias and alias not in mapping:
            mapping[alias] = resolved_suite


def _register_top_level_recipe_aliases(
    mapping: dict[str, str], top_aliases: Any
) -> None:
    if not isinstance(top_aliases, dict):
        return
    for alias, target in top_aliases.items():
        if not isinstance(alias, str) or not alias or alias in mapping:
            continue
        if isinstance(target, str) and target in mapping:
            mapping[alias] = mapping[target]


def recipe_to_suite_map(root: Path = ROOT) -> dict[str, str]:
    """Map every recipe name and alias in catalog/recipes.json to its target suite."""
    recipes_path = root / "catalog/recipes.json"
    doc = read_json_object(
        recipes_path,
        label="Recipes catalog 'catalog/recipes.json'",
        optional=True,
    )
    if doc is None:
        return {}
    recipes_obj = doc.get("recipes")
    if not isinstance(recipes_obj, dict):
        raise FredCliError(
            f"Malformed recipes catalog '{recipes_path}': expected 'recipes' object",
            code="invalid_catalog",
            hint=f"Ensure '{recipes_path}' contains a JSON object under 'recipes'.",
        )
    s_map = skill_to_suite_map(root)
    mapping: dict[str, str] = {}
    for recipe_name, recipe_obj in recipes_obj.items():
        if not isinstance(recipe_name, str) or not isinstance(recipe_obj, dict):
            continue
        resolved_suite = _resolve_recipe_suite(recipe_obj, root, s_map)
        mapping[recipe_name] = resolved_suite
        _register_recipe_aliases(mapping, recipe_obj.get("aliases", []), resolved_suite)
    _register_top_level_recipe_aliases(mapping, doc.get("aliases"))
    return mapping


def is_audit_ledger_shape(data: Mapping[str, Any]) -> bool:
    """Return True when a JSON mapping carries audit.json ledger markers alongside findings."""
    if "findings" not in data or "verdict" in data:
        return False
    if any(
        key in data
        for key in (
            "test_cases",
            "boundaries",
            "decisions",
            "components",
            "interview_decisions",
        )
    ):
        return False
    if "summary" in data and not any(
        key in data for key in ("evidence", "contracts", "repository", "issues")
    ):
        return False
    return any(
        key in data
        for key in ("evidence", "coverage", "contracts", "run_id", "repository")
    )


def _classify_fallback_domain_payload(data: dict[str, Any]) -> str | None:
    if "summary" in data and "findings" in data:
        return "review"
    if "component" in data and "framework" in data:
        return "testing"
    if "title" in data and "status" in data and "findings" not in data:
        return "architecture"
    return "run" if "run_id" in data else None


def classify_domain_payload(data: object) -> str | None:
    """Classify a non-audit domain, workflow, or run JSON payload by kind."""
    if not isinstance(data, dict) or is_audit_ledger_shape(data):
        return None
    keys = set(data)
    for kind, markers in DOMAIN_KIND_KEY_SETS:
        if keys & markers:
            return kind
    return _classify_fallback_domain_payload(data)


def detect_domain_artifact(data: object) -> str | None:
    """Return the canonical non-audit JSON filename for a domain/workflow/run payload."""
    kind = classify_domain_payload(data)
    return f"{kind}.json" if kind is not None else None


def detect_artifact_suite(data: object) -> str | None:
    """Return the owning suite name for a non-audit domain or workflow deliverable payload."""
    kind = classify_domain_payload(data)
    if kind is None:
        return None
    return DOMAIN_KIND_TO_SUITE.get(kind)


_PathKey = TypeVar("_PathKey", bound=str | Path)


def build_outputs(
    outputs: Mapping[_PathKey, str],
    root: Path = ROOT,
    check: bool = False,
    prefix: str = "Stale generated file",
) -> list[str]:
    """Materialize generated file outputs, or detect drift when check is True."""
    problems = []
    for relative, content in outputs.items():
        rel_path = Path(relative)
        path = root / rel_path
        if check:
            if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
                problems.append(f"{prefix}: {rel_path.as_posix()}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return problems

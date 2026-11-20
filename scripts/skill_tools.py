"""Small authoring utilities. No agent host or network access required."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from html import unescape
from pathlib import Path
from typing import Any, Literal, TypeVar, overload
from urllib.parse import unquote, urlsplit

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


def read_json_object(
    path: Path | str,
    *,
    label: str = "JSON file",
    hint: str | None = None,
) -> dict[str, Any]:
    p = path if isinstance(path, Path) else Path(path)
    if p.is_dir():
        raise FredCliError(
            f"Expected {label.lower()} at '{p}', got a directory",
            code="invalid_path_type",
            hint=hint or f"Pass a JSON file path instead of directory '{p}'.",
        )
    try:
        raw_text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FredCliError(
            f"{label} not found: {p}",
            code="missing_file",
            hint=hint or f"Verify that '{p}' exists and check the path spelling.",
        ) from exc
    except OSError as exc:
        raise FredCliError(
            f"Unable to read {label.lower()} '{p}': {exc}",
            code="io_error",
            hint=hint or f"Check file permissions and readability for '{p}'.",
        ) from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FredCliError(
            f"Malformed JSON in {label.lower()} '{p}' (line {exc.lineno}, column {exc.colno}): {exc.msg}",
            code="invalid_json",
            hint=hint or f"Fix the JSON syntax in '{p}' near line {exc.lineno}.",
        ) from exc
    if not isinstance(data, dict):
        actual = type(data).__name__
        raise FredCliError(
            f"Expected JSON object in {label.lower()} '{p}', got {actual}",
            code="invalid_json_root",
            hint=hint or f"Ensure the root value in '{p}' is a JSON object ({{...}}).",
        )
    return data


# Matches markdown links [text](target), images ![text](target)
MD_LINK_RE = re.compile(r"!?\[.*?\]\(([^)]+)\)")
# Matches HTML href="..." or src="..."
HTML_LINK_RE = re.compile(r"""(?:href|src)=["\x27]([^"\x27]+)["\x27]""")
# Matches reference-style link definitions [label]: target
MD_REF_LINK_RE = re.compile(r"""^\[[^\]]+\]:\s*(\S+)""", re.MULTILINE)
_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_MARKER_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_TABLE_DELIM_CELL_RE = re.compile(r"^:?-{3,}:?$")
_HTML_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_HTML_ID_ATTR_RE = re.compile(r"""\bid=["\x27]([^"\x27]+)["\x27]""")
_HTML_NAME_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*\bname=["\x27]([^"\x27]+)["\x27]""", re.IGNORECASE
)


def _iter_markdown_fence_states(content: str) -> Iterator[tuple[str, bool, bool]]:
    """Yield (line, is_fence_delimiter, is_inside_fence) across Markdown lines."""
    fence_char = ""
    fence_len = 0
    for line in content.splitlines():
        fence_match = _FENCE_MARKER_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            trailing = line[fence_match.end() :].strip()
            if not fence_char:
                fence_char = marker[0]
                fence_len = len(marker)
                yield line, True, True
                continue
            if marker[0] == fence_char and len(marker) >= fence_len and not trailing:
                fence_char = ""
                fence_len = 0
                yield line, True, False
                continue
        yield line, False, bool(fence_char)


def count_fenced_code_blocks(
    content: str,
    *,
    language: str | None = None,
) -> int:
    """Count closed fenced code blocks in Markdown, optionally filtered by language."""
    fence_char = ""
    fence_len = 0
    open_lang = ""
    wanted_lang = language.strip().lower() if language is not None else None
    count = 0
    for line in content.splitlines():
        fence_match = _FENCE_MARKER_RE.match(line)
        if not fence_match:
            continue
        marker = fence_match.group(1)
        trailing = line[fence_match.end() :].strip()
        if not fence_char:
            fence_char = marker[0]
            fence_len = len(marker)
            tokens = trailing.split()
            open_lang = tokens[0].lower() if tokens else ""
            continue
        if marker[0] == fence_char and len(marker) >= fence_len and not trailing:
            if wanted_lang is None or open_lang == wanted_lang:
                count += 1
            fence_char = ""
            fence_len = 0
            open_lang = ""
    return count


def extract_markdown_preamble(content: str, *, before_level: int = 2) -> str:
    """Extract Markdown content preceding the first unfenced section heading.

    By default (`before_level=2`), preserves `#` document titles and intro content
    up to the first `##` heading, ignoring ATX headings inside fenced blocks.
    """
    if before_level < 1:
        raise ValueError("before_level must be at least 1")
    collected: list[str] = []
    for line, is_delim, in_fence in _iter_markdown_fence_states(content):
        if not is_delim and not in_fence:
            heading_match = _ATX_HEADING_RE.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                stop = level == 1 if before_level == 1 else (2 <= level <= before_level)
                if stop:
                    break
        collected.append(line)
    return "\n".join(collected)


def extract_markdown_headings(
    content: str,
    *,
    level: int | None = None,
) -> list[tuple[int, str]]:
    """Return ordered (level, heading_text) tuples for unfenced ATX headings."""
    headings: list[tuple[int, str]] = []
    for line, is_delim, in_fence in _iter_markdown_fence_states(content):
        if is_delim or in_fence:
            continue
        heading_match = _ATX_HEADING_RE.match(line)
        if not heading_match:
            continue
        h_level = len(heading_match.group(1))
        if level is None or h_level == level:
            headings.append((h_level, heading_match.group(2).strip()))
    return headings


def extract_markdown_section(
    content: str,
    heading: str,
    *,
    level: int | None = None,
    include_subheadings: bool = True,
) -> str | None:
    """Extract the body of an unfenced ATX heading section, ignoring fenced headings."""
    wanted = heading.strip()
    in_section = False
    section_level = 0
    collected: list[str] = []
    for line, is_delim, in_fence in _iter_markdown_fence_states(content):
        if not is_delim and not in_fence:
            heading_match = _ATX_HEADING_RE.match(line)
            if heading_match:
                h_level = len(heading_match.group(1))
                h_text = heading_match.group(2).strip()
                if not in_section:
                    if h_text == wanted and (level is None or h_level == level):
                        in_section = True
                        section_level = h_level
                    continue
                if not include_subheadings or h_level <= section_level:
                    break
        if in_section:
            collected.append(line)
    return "\n".join(collected) if in_section else None


def _split_markdown_table_cells(line: str) -> list[str] | None:
    """Split a Markdown pipe-table row into trimmed cells while honoring inline code spans."""
    stripped = line.strip()
    if not stripped.startswith("|") or stripped.count("|") < 2:
        return None
    cells: list[str] = []
    buf: list[str] = []
    idx = 0
    tick_len = 0
    length = len(stripped)
    while idx < length:
        ch = stripped[idx]
        if ch == "\\" and idx + 1 < length and tick_len == 0:
            buf.append(stripped[idx + 1])
            idx += 2
            continue
        if ch == "`":
            run = 1
            while idx + run < length and stripped[idx + run] == "`":
                run += 1
            if tick_len == 0:
                tick_len = run
            elif run == tick_len:
                tick_len = 0
            buf.append(stripped[idx : idx + run])
            idx += run
            continue
        if ch == "|" and tick_len == 0:
            cells.append("".join(buf).strip())
            buf = []
            idx += 1
            continue
        buf.append(ch)
        idx += 1
    cells.append("".join(buf).strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells if cells else None


def extract_markdown_tables(content: str) -> list[list[dict[str, str]]]:
    """Extract unfenced Markdown pipe tables as lists of {header: cell_value} dicts."""
    unfenced_rows: list[list[str] | None] = []
    for line, is_delim, in_fence in _iter_markdown_fence_states(content):
        if is_delim or in_fence:
            unfenced_rows.append(None)
        else:
            unfenced_rows.append(_split_markdown_table_cells(line))

    tables: list[list[dict[str, str]]] = []
    idx = 0
    total = len(unfenced_rows)
    while idx + 1 < total:
        headers = unfenced_rows[idx]
        delims = unfenced_rows[idx + 1]
        if (
            headers is not None
            and delims is not None
            and len(delims) == len(headers)
            and all(_TABLE_DELIM_CELL_RE.match(cell) for cell in delims)
        ):
            idx += 2
            table_rows: list[dict[str, str]] = []
            while idx < total and unfenced_rows[idx] is not None:
                row_cells = unfenced_rows[idx]
                assert row_cells is not None
                padded = [
                    row_cells[col] if col < len(row_cells) else ""
                    for col in range(len(headers))
                ]
                table_rows.append(dict(zip(headers, padded, strict=True)))
                idx += 1
            tables.append(table_rows)
            continue
        idx += 1
    return tables


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
        + MD_REF_LINK_RE.findall(content)
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


def slugify_markdown_heading(heading_text: str) -> str:
    """Convert Markdown heading text into an anchor slug matching GitHub and build_site."""
    cleaned = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", heading_text)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[`]", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9 _-]", "", cleaned)
    return cleaned.strip().replace(" ", "-")


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
    fence_char = ""
    fence_len = 0

    for line in content.splitlines():
        fence_match = _FENCE_MARKER_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len:
                fence_char = ""
                fence_len = 0
                continue
        if fence_char:
            continue

        heading_match = _ATX_HEADING_RE.match(line)
        if heading_match:
            base = slugify_markdown_heading(heading_match.group(2)) or "section"
            count = seen_slugs.get(base, 0)
            seen_slugs[base] = count + 1
            anchors.add(base if count == 0 else f"{base}-{count}")

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


def find_deep_reference_chains(package: Path, max_hops: int = 1) -> list[str]:
    """Return markdown reference chains exceeding max_hops from SKILL.md."""
    if max_hops < 1:
        raise ValueError("max_hops must be at least 1")
    package_root = package.resolve()
    entry_path = package / "SKILL.md"
    violations: list[str] = []
    seen_violations: set[str] = set()
    visited_markdown: set[Path] = set()

    if entry_path.is_file() and not entry_path.is_symlink():
        entry_resolved = entry_path.resolve()
        queue: deque[tuple[Path, tuple[Path, ...], tuple[str, ...]]] = deque(
            [(entry_resolved, (entry_resolved,), ("SKILL.md",))]
        )
        while queue:
            current, path_chain, label_chain = queue.popleft()
            visited_markdown.add(current)
            depth = len(path_chain) - 1
            try:
                content = current.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for raw_target in extract_relative_links(content):
                resolved = resolve_relative_link_target(raw_target, current.parent)
                if resolved is None:
                    continue
                next_depth = depth + 1
                next_label = _format_package_rel(resolved, package_root, raw_target)
                if next_depth > max_hops:
                    chain_str = " -> ".join((*label_chain, next_label))
                    if chain_str not in seen_violations:
                        seen_violations.add(chain_str)
                        violations.append(chain_str)
                    continue
                if (
                    resolved.suffix.lower() == ".md"
                    and resolved.is_relative_to(package_root)
                    and resolved.is_file()
                    and not resolved.is_symlink()
                    and resolved not in path_chain
                ):
                    queue.append(
                        (
                            resolved,
                            (*path_chain, resolved),
                            (*label_chain, next_label),
                        )
                    )

    references_dir = package / "references"
    if references_dir.is_dir() and not references_dir.is_symlink():
        for ref_md in sorted(references_dir.rglob("*.md")):
            if ref_md.is_symlink() or not ref_md.is_file():
                continue
            ref_resolved = ref_md.resolve()
            if ref_resolved in visited_markdown:
                continue
            ref_label = _format_package_rel(
                ref_resolved, package_root, ref_md.as_posix()
            )
            try:
                ref_content = ref_md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for raw_target in extract_relative_links(ref_content):
                resolved = resolve_relative_link_target(raw_target, ref_md.parent)
                if resolved is None:
                    continue
                target_label = _format_package_rel(resolved, package_root, raw_target)
                chain_str = f"SKILL.md -> {ref_label} -> {target_label}"
                if chain_str not in seen_violations:
                    seen_violations.add(chain_str)
                    violations.append(chain_str)

    return violations


def read_frontmatter(path: Path) -> tuple[dict, str]:
    import yaml

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError(f"{path}: unterminated YAML frontmatter")
    metadata = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise TypeError(f"{path}: frontmatter must be a mapping")
    return metadata, "".join(lines[end + 1 :])


def _validate_suite_artifact(suite: dict, root: Path) -> None:
    artifact = suite.get("artifact")
    if artifact is None:
        return
    if not isinstance(artifact, dict):
        raise TypeError(f"Suite '{suite.get('name')}' artifact must be an object")
    filename = artifact.get("filename")
    schema = artifact.get("schema")
    starter = artifact.get("starter")
    if (
        not isinstance(filename, str)
        or not filename.endswith(".json")
        or Path(filename).name != filename
        or "\\" in filename
    ):
        raise ValueError(
            f"Suite '{suite.get('name')}' artifact filename must be a JSON basename"
        )
    if not isinstance(schema, str) or not schema.endswith(".schema.json"):
        raise ValueError(
            f"Suite '{suite.get('name')}' artifact schema must name a .schema.json path"
        )
    if (root / "skills").is_dir() and (root / "shared").is_dir():
        schema_file = root / schema
        if schema_file.is_symlink() or not schema_file.is_file():
            raise ValueError(
                f"Suite '{suite.get('name')}' missing artifact schema: {schema}"
            )
    if not isinstance(starter, dict) or not starter:
        raise ValueError(
            f"Suite '{suite.get('name')}' artifact starter must be a nonempty mapping"
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


def _iter_suite_phase_skills(
    suites: Iterable[Any],
    *,
    strict: bool = False,
    root: Path = ROOT,
) -> Iterator[tuple[str, dict[str, Any] | None, Any]]:
    for suite in suites:
        if not isinstance(suite, dict):
            if strict:
                raise TypeError("Suite entry must be an object")
            continue
        if strict:
            _validate_suite_artifact(suite, root)
        suite_name = suite["name"] if strict else suite.get("name")
        if not isinstance(suite_name, str) or not suite_name:
            if strict:
                raise ValueError(f"Invalid suite name: {suite_name!r}")
            continue
        yield suite_name, None, None
        phases = suite["phases"] if strict else suite.get("phases", [])
        if not isinstance(phases, list):
            if strict:
                raise TypeError(f"Suite '{suite_name}' phases must be a list")
            continue
        ordered_phases = (
            phases
            if strict
            else sorted(
                enumerate(phases),
                key=lambda item: (
                    (0, item[1]["id"])
                    if isinstance(item[1], dict) and isinstance(item[1].get("id"), int)
                    else (1, item[0])
                ),
            )
        )
        for item in ordered_phases:
            phase = item if strict else item[1]
            if not isinstance(phase, dict):
                if strict:
                    raise TypeError(f"Suite '{suite_name}' phase must be an object")
                continue
            skills = phase["skills"] if strict else phase.get("skills", [])
            if not isinstance(skills, list):
                if strict:
                    raise TypeError(f"Suite '{suite_name}' phase skills must be a list")
                continue
            for skill in skills:
                if strict or (isinstance(skill, str) and skill):
                    yield suite_name, phase, skill


def read_suites_catalog(root: Path = ROOT) -> list[dict[str, Any]] | None:
    """Read catalog/suites.json, returning suite objects or None when absent."""
    suites_path = root / "catalog/suites.json"
    try:
        doc = read_json_object(
            suites_path, label="Suites catalog 'catalog/suites.json'"
        )
    except FredCliError as exc:
        if exc.code == "missing_file":
            return None
        raise
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


def suite_members(root: Path = ROOT) -> dict[str, dict]:
    document = json.loads((root / "catalog/suites.json").read_text(encoding="utf-8"))
    members: dict[str, dict] = {}
    for suite_name, phase, name in _iter_suite_phase_skills(
        document["suites"], strict=True, root=root
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


def infer_suite_and_siblings(
    skill_name: str,
    root: Path | None = None,
) -> tuple[str, set[str]]:
    """Resolve a skill's suite and sibling set from catalog/suites.json or prefix fallback."""
    if root is not None and (root / "catalog/suites.json").is_file():
        try:
            members = suite_members(root)
            if skill_name in members:
                suite_name = str(members[skill_name]["suite"])
                siblings = {
                    s
                    for s, info in members.items()
                    if info.get("suite") == suite_name and s != skill_name
                }
                return suite_name, siblings
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return infer_suite_name(skill_name), set()


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
        skills_path = root / "catalog/skills.json"
        try:
            doc = read_json_object(
                skills_path, label="Skills catalog 'catalog/skills.json'"
            )
        except FredCliError as exc:
            if exc.code != "missing_file":
                raise
        else:
            raw_skills = doc.get("skills")
            if not isinstance(raw_skills, list):
                raise FredCliError(
                    f"Malformed skills catalog '{skills_path}': expected 'skills' array",
                    code="invalid_catalog",
                    hint=f"Ensure '{skills_path}' contains a JSON array under 'skills'.",
                )
            resolved_map = {
                row["name"]: row["suite"]
                for row in raw_skills
                if isinstance(row, dict)
                and isinstance(row.get("name"), str)
                and isinstance(row.get("suite"), str)
            }
    suites = list(dict.fromkeys(resolved_map[s] for s in skills if s in resolved_map))
    if "repository-audits" in suites:
        return "repository-audits"
    return suites[0] if suites else "repository-audits"


def recipe_to_suite_map(root: Path = ROOT) -> dict[str, str]:
    """Map every recipe name and alias in catalog/recipes.json to its target suite."""
    recipes_path = root / "catalog/recipes.json"
    try:
        doc = read_json_object(
            recipes_path, label="Recipes catalog 'catalog/recipes.json'"
        )
    except FredCliError as exc:
        if exc.code == "missing_file":
            return {}
        raise
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
        explicit_suite = recipe_obj.get("suite")
        if isinstance(explicit_suite, str) and explicit_suite:
            resolved_suite = explicit_suite
        else:
            raw_skills = recipe_obj.get("skills", [])
            str_skills = (
                [s for s in raw_skills if isinstance(s, str)]
                if isinstance(raw_skills, list)
                else []
            )
            resolved_suite = suite_for_skills(
                str_skills, root=root, skill_suites=s_map or None
            )
        mapping[recipe_name] = resolved_suite
        raw_aliases = recipe_obj.get("aliases", [])
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                if isinstance(alias, str) and alias and alias not in mapping:
                    mapping[alias] = resolved_suite
    top_aliases = doc.get("aliases")
    if isinstance(top_aliases, dict):
        for alias, target in top_aliases.items():
            if (
                isinstance(alias, str)
                and alias
                and isinstance(target, str)
                and target in mapping
                and alias not in mapping
            ):
                mapping[alias] = mapping[target]
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


def classify_domain_payload(data: object) -> str | None:
    """Classify a non-audit domain, workflow, or run JSON payload by kind."""
    if not isinstance(data, dict) or is_audit_ledger_shape(data):
        return None
    keys = set(data)
    for kind, markers in DOMAIN_KIND_KEY_SETS:
        if keys & markers:
            return kind
    if "summary" in data and "findings" in data:
        return "review"
    if "component" in data and "framework" in data:
        return "testing"
    if "title" in data and "status" in data and "findings" not in data:
        return "architecture"
    if "run_id" in data:
        return "run"
    return None


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

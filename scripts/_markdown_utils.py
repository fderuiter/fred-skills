"""Stdlib-only Markdown parsing helpers shared across repository scripts and fredlib."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ATX_HEADING_RE",
    "FenceOpen",
    "FencedBlock",
    "consume_fenced_block",
    "count_fenced_code_blocks",
    "extract_fenced_blocks",
    "extract_markdown_headings",
    "extract_markdown_preamble",
    "extract_markdown_section",
    "extract_markdown_tables",
    "extract_noncode_lines",
    "extract_reference_link_targets",
    "is_fence_close",
    "is_markdown_table_separator",
    "match_atx_heading",
    "match_fence_open",
    "non_fenced_lines",
    "parse_frontmatter_scalars",
    "parse_reference_link_definition",
    "scan_markdown_fences",
    "slugify_markdown_heading",
    "split_frontmatter_block",
    "split_markdown_table_cells",
    "strip_fenced_code_blocks",
    "strip_reference_link_definitions",
    "unique_heading_slug",
]

ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_ATX_HEADING_SCAN_RE = re.compile(r"^([ \t]*)(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_OPEN_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
_TABLE_DELIM_CELL_RE = re.compile(r"^:?-{3,}:?$")
_REF_LINK_DEF_RE = re.compile(
    r"^[ \t]{0,3}\[([^\]]+)\]:(?:\s*<([^>\r\n]+)>|\s+(\S+?))"
    r"(?:\s+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|\([^)\r\n]*\)))?\s*$"
)
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:(.*)$")
_BLOCK_SCALAR_MARKERS = frozenset({"", ">", "|", ">-", "|-", ">+", "|+"})


@dataclass(frozen=True)
class FenceOpen:
    """Opening CommonMark fenced code block marker."""

    indent: str
    char: str
    length: int
    info: str

    @property
    def language(self) -> str:
        stripped = self.info.strip()
        return stripped.split()[0] if stripped else ""


@dataclass(frozen=True)
class FencedBlock:
    """Scanned CommonMark fenced code block with 1-based line bounds."""

    start_line: int
    end_line: int
    fence: FenceOpen
    lines: tuple[tuple[int, str], ...]
    closed: bool


def _indent_within_limit(indent: str, max_indent: int | None) -> bool:
    if max_indent is None:
        return True
    return len(indent.expandtabs(4)) <= max_indent


def match_fence_open(
    line: str,
    *,
    max_indent: int | None = None,
) -> FenceOpen | None:
    """Match an opening CommonMark backtick or tilde fence on a single line."""
    clean = line.rstrip("\r\n")
    match = _FENCE_OPEN_RE.match(clean)
    if match is None:
        return None
    indent, marker, info = match.group(1), match.group(2), match.group(3)
    if not _indent_within_limit(indent, max_indent):
        return None
    if marker[0] == "`" and "`" in info:
        return None
    return FenceOpen(indent=indent, char=marker[0], length=len(marker), info=info)


def is_fence_close(
    line: str,
    fence: FenceOpen,
    *,
    max_indent: int | None = None,
) -> bool:
    """Return True when line closes the given opening CommonMark fence."""
    clean = line.rstrip("\r\n")
    stripped = clean.lstrip(" \t")
    indent = clean[: len(clean) - len(stripped)]
    if not _indent_within_limit(indent, max_indent):
        return False
    if not stripped.startswith(fence.char * fence.length):
        return False
    closing_run = len(stripped) - len(stripped.lstrip(fence.char))
    return closing_run >= fence.length and not stripped[closing_run:].strip()


def consume_fenced_block(
    lines: Sequence[str],
    start_idx: int,
    *,
    max_indent: int | None = None,
) -> FencedBlock:
    """Consume a fenced code block starting at 0-based start_idx."""
    fence = match_fence_open(lines[start_idx], max_indent=max_indent)
    if fence is None:
        raise ValueError(f"Line at index {start_idx} is not a valid fence opening")
    body_lines: list[tuple[int, str]] = []
    idx = start_idx + 1
    while idx < len(lines):
        candidate = lines[idx]
        if is_fence_close(candidate, fence, max_indent=max_indent):
            return FencedBlock(
                start_line=start_idx + 1,
                end_line=idx + 1,
                fence=fence,
                lines=tuple(body_lines),
                closed=True,
            )
        body_lines.append((idx + 1, candidate))
        idx += 1
    return FencedBlock(
        start_line=start_idx + 1,
        end_line=len(lines),
        fence=fence,
        lines=tuple(body_lines),
        closed=False,
    )


def scan_markdown_fences(
    lines: Sequence[str],
    *,
    max_indent: int | None = None,
) -> tuple[list[tuple[int, str]], list[FencedBlock]]:
    """Partition lines into 1-based non-fenced lines and scanned fenced code blocks."""
    outside: list[tuple[int, str]] = []
    blocks: list[FencedBlock] = []
    idx = 0
    while idx < len(lines):
        if match_fence_open(lines[idx], max_indent=max_indent) is not None:
            block = consume_fenced_block(lines, idx, max_indent=max_indent)
            blocks.append(block)
            idx = block.end_line
            continue
        outside.append((idx + 1, lines[idx]))
        idx += 1
    return outside, blocks


def non_fenced_lines(
    markdown_text: str,
    *,
    max_indent: int | None = None,
) -> list[tuple[int, str]]:
    """Return 1-based (line_no, line) pairs outside fenced code blocks."""
    outside, _ = scan_markdown_fences(
        markdown_text.splitlines(),
        max_indent=max_indent,
    )
    return outside


def strip_fenced_code_blocks(
    markdown_text: str,
    *,
    max_indent: int | None = None,
) -> str:
    """Remove fenced code blocks from markdown_text while preserving line endings."""
    outside, _ = scan_markdown_fences(
        markdown_text.splitlines(keepends=True),
        max_indent=max_indent,
    )
    return "".join(line for _, line in outside)


def extract_fenced_blocks(
    markdown_text: str,
    *,
    max_indent: int | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """Return ([(lang_lower, code_body), ...], has_unclosed_fence) from Markdown text."""
    _, blocks = scan_markdown_fences(
        markdown_text.splitlines(),
        max_indent=max_indent,
    )
    closed_blocks = [
        (block.fence.language.lower(), "\n".join(line for _, line in block.lines))
        for block in blocks
        if block.closed
    ]
    has_unclosed = any(not block.closed for block in blocks)
    return closed_blocks, has_unclosed


def extract_noncode_lines(
    markdown_text: str,
    *,
    max_indent: int | None = None,
) -> list[str]:
    """Return non-empty stripped prose/heading lines outside fenced code blocks."""
    return [
        stripped
        for _, raw_line in non_fenced_lines(markdown_text, max_indent=max_indent)
        if (stripped := raw_line.strip())
    ]


def match_atx_heading(
    line: str,
    *,
    max_indent: int | None = 3,
) -> tuple[int, str] | None:
    """Match an ATX Markdown heading and return (level, heading_text)."""
    match = _ATX_HEADING_SCAN_RE.match(line.rstrip("\r\n"))
    if match is None or not _indent_within_limit(match.group(1), max_indent):
        return None
    return len(match.group(2)), match.group(3).strip()


def slugify_markdown_heading(heading_text: str) -> str:
    """Convert Markdown heading text into an anchor slug matching GitHub and build_site."""
    cleaned = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", heading_text)
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[`]", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9 _-]", "", cleaned)
    return cleaned.strip().replace(" ", "-")


def unique_heading_slug(heading_text: str, seen_slugs: dict[str, int]) -> str:
    """Return a deduplicated heading slug, incrementing seen_slugs in place."""
    base = slugify_markdown_heading(heading_text) or "section"
    count = seen_slugs.get(base, 0)
    seen_slugs[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def count_fenced_code_blocks(
    content: str,
    *,
    language: str | None = None,
    max_indent: int | None = None,
) -> int:
    """Count closed fenced code blocks in Markdown, optionally filtered by language."""
    blocks, _ = extract_fenced_blocks(content, max_indent=max_indent)
    if language is None:
        return len(blocks)
    wanted = language.strip().lower()
    return sum(1 for lang, _ in blocks if lang == wanted)


def _preamble_stops_at_heading(h_level: int, before_level: int) -> bool:
    return h_level == 1 if before_level == 1 else 2 <= h_level <= before_level


def extract_markdown_preamble(
    content: str,
    *,
    before_level: int = 2,
    max_indent: int | None = None,
) -> str:
    """Extract Markdown content preceding the first unfenced section heading.

    By default (`before_level=2`), preserves `#` document titles and intro content
    up to the first `##` heading, ignoring ATX headings inside fenced blocks.
    """
    if before_level < 1:
        raise ValueError("before_level must be at least 1")
    lines = content.splitlines()
    outside, _ = scan_markdown_fences(lines, max_indent=max_indent)
    for line_no, line in outside:
        heading = match_atx_heading(line)
        if heading is not None and _preamble_stops_at_heading(heading[0], before_level):
            return "\n".join(lines[: line_no - 1])
    return "\n".join(lines)


def extract_markdown_headings(
    content: str,
    *,
    level: int | None = None,
    max_indent: int | None = None,
) -> list[tuple[int, str]]:
    """Return ordered (level, heading_text) tuples for unfenced ATX headings."""
    headings: list[tuple[int, str]] = []
    for _, line in non_fenced_lines(content, max_indent=max_indent):
        heading = match_atx_heading(line)
        if heading is not None and (level is None or heading[0] == level):
            headings.append(heading)
    return headings


def _unfenced_heading_positions(
    lines: Sequence[str],
    *,
    max_indent: int | None = None,
) -> list[tuple[int, int, str]]:
    outside, _ = scan_markdown_fences(lines, max_indent=max_indent)
    positions: list[tuple[int, int, str]] = []
    for line_no, line in outside:
        parsed = match_atx_heading(line)
        if parsed is not None:
            positions.append((line_no, parsed[0], parsed[1]))
    return positions


def extract_markdown_section(
    content: str,
    heading: str,
    *,
    level: int | None = None,
    include_subheadings: bool = True,
    max_indent: int | None = None,
) -> str | None:
    """Extract the body of an unfenced ATX heading section, ignoring fenced headings."""
    wanted = heading.strip()
    lines = content.splitlines()
    positions = _unfenced_heading_positions(lines, max_indent=max_indent)
    match_idx = next(
        (
            idx
            for idx, (_, h_level, h_text) in enumerate(positions)
            if h_text == wanted and (level is None or h_level == level)
        ),
        None,
    )
    if match_idx is None:
        return None
    start_line, section_level, _ = positions[match_idx]
    for next_line, next_level, _ in positions[match_idx + 1 :]:
        if not include_subheadings or next_level <= section_level:
            return "\n".join(lines[start_line : next_line - 1])
    return "\n".join(lines[start_line:])


def _trim_outer_table_pipes(line: str) -> str:
    stripped = line.strip().removeprefix("|")
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        return stripped[:-1]
    return stripped


def _find_closing_backticks(text: str, start: int, run_len: int) -> int | None:
    idx = start
    total = len(text)
    while idx < total:
        if text[idx] != "`":
            idx += 1
            continue
        end = idx + 1
        while end < total and text[end] == "`":
            end += 1
        if end - idx == run_len:
            return end
        idx = end
    return None


def _consume_table_token(text: str, idx: int) -> tuple[str | None, int]:
    ch = text[idx]
    if ch == "\\" and idx + 1 < len(text):
        nxt = text[idx + 1]
        return ("|" if nxt == "|" else f"\\{nxt}"), idx + 2
    if ch == "`":
        end = idx + 1
        while end < len(text) and text[end] == "`":
            end += 1
        closed = _find_closing_backticks(text, end, end - idx)
        stop = closed if closed is not None else end
        return text[idx:stop].replace(r"\|", "|"), stop
    if ch == "|":
        return None, idx + 1
    return ch, idx + 1


def split_markdown_table_cells(line: str) -> list[str]:
    """Split a Markdown pipe-table row into trimmed cells, preserving inline code pipes."""
    trimmed = _trim_outer_table_pipes(line)
    cells: list[str] = []
    buf: list[str] = []
    idx = 0
    while idx < len(trimmed):
        piece, idx = _consume_table_token(trimmed, idx)
        if piece is None:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(piece)
    cells.append("".join(buf).strip())
    return cells


def is_markdown_table_separator(line: str) -> bool:
    """Return True when line is a GFM pipe-table alignment/delimiter row."""
    stripped = line.strip()
    if "|" not in stripped:
        return False
    cells = split_markdown_table_cells(stripped)
    return bool(cells) and all(
        _TABLE_DELIM_CELL_RE.match(cell) is not None for cell in cells
    )


def _unfenced_pipe_rows(
    lines: Sequence[str],
    *,
    max_indent: int | None = None,
) -> list[list[str] | None]:
    outside, _ = scan_markdown_fences(lines, max_indent=max_indent)
    outside_nos = {line_no for line_no, _ in outside}
    return [
        split_markdown_table_cells(line)
        if line_no in outside_nos and "|" in line and line.strip()
        else None
        for line_no, line in enumerate(lines, start=1)
    ]


def _is_delim_row(headers: list[str] | None, delims: list[str] | None) -> bool:
    if headers is None or delims is None or len(headers) != len(delims):
        return False
    return all(_TABLE_DELIM_CELL_RE.match(cell) is not None for cell in delims)


def _consume_table_dict_rows(
    rows: Sequence[list[str] | None],
    start_idx: int,
    headers: Sequence[str],
) -> tuple[list[dict[str, str]], int]:
    idx = start_idx
    width = len(headers)
    parsed_rows: list[dict[str, str]] = []
    while idx < len(rows) and rows[idx] is not None:
        row_cells = rows[idx]
        assert row_cells is not None
        padded = (row_cells + [""] * width)[:width]
        parsed_rows.append(dict(zip(headers, padded, strict=True)))
        idx += 1
    return parsed_rows, idx


def extract_markdown_tables(
    content: str,
    *,
    max_indent: int | None = None,
) -> list[list[dict[str, str]]]:
    """Extract unfenced Markdown pipe tables as lists of {header: cell_value} dicts."""
    rows = _unfenced_pipe_rows(content.splitlines(), max_indent=max_indent)
    tables: list[list[dict[str, str]]] = []
    idx = 0
    while idx + 1 < len(rows):
        headers = rows[idx]
        if headers is not None and _is_delim_row(headers, rows[idx + 1]):
            table_rows, idx = _consume_table_dict_rows(rows, idx + 2, headers)
            tables.append(table_rows)
            continue
        idx += 1
    return tables


def parse_reference_link_definition(line: str) -> tuple[str, str] | None:
    """Parse a CommonMark reference-link definition line into (lower_label, target_url)."""
    match = _REF_LINK_DEF_RE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    label = match.group(1).strip().lower()
    target = (match.group(2) or match.group(3) or "").strip()
    if not label or not target:
        return None
    return label, target


def extract_reference_link_targets(markdown_text: str) -> list[str]:
    """Extract target URLs from reference-style link definitions in Markdown text."""
    targets: list[str] = []
    for line in markdown_text.splitlines():
        parsed = parse_reference_link_definition(line)
        if parsed is not None:
            targets.append(parsed[1])
    return targets


def strip_reference_link_definitions(
    markdown_text: str,
    *,
    max_indent: int | None = None,
) -> tuple[str, dict[str, str]]:
    """Strip reference-link definitions outside fenced code blocks and return (clean_md, defs)."""
    lines = markdown_text.splitlines()
    outside, _ = scan_markdown_fences(lines, max_indent=max_indent)
    outside_line_nos = {line_no for line_no, _ in outside}
    defs: dict[str, str] = {}
    kept: list[str] = []
    for line_no, line in enumerate(lines, start=1):
        if line_no in outside_line_nos:
            parsed = parse_reference_link_definition(line)
            if parsed is not None:
                defs[parsed[0]] = parsed[1]
                continue
        kept.append(line)
    return "\n".join(kept), defs


def _unquote_yaml_scalar(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        inner = raw[1:-1]
        return inner.replace("''", "'") if raw[0] == "'" else inner
    return raw


def split_frontmatter_block(
    markdown_text: str,
    *,
    label: Path | str | None = None,
    require: bool = False,
) -> tuple[str | None, str]:
    """Split leading --- YAML frontmatter from Markdown body, preserving line endings."""
    prefix = f"{label}: " if label is not None else ""
    lines = markdown_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        if require:
            raise ValueError(f"{prefix}missing YAML frontmatter")
        return None, markdown_text
    end_idx = next(
        (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
        None,
    )
    if end_idx is None:
        if require:
            raise ValueError(f"{prefix}unterminated YAML frontmatter")
        return None, markdown_text
    return "".join(lines[1:end_idx]), "".join(lines[end_idx + 1 :])


def _consume_frontmatter_continuations(
    raw_lines: Sequence[str],
    start_idx: int,
) -> tuple[list[str], int]:
    parts: list[str] = []
    idx = start_idx
    while idx < len(raw_lines):
        nxt = raw_lines[idx]
        if not nxt.strip():
            idx += 1
            continue
        if not nxt.startswith((" ", "\t")):
            break
        cleaned = nxt.strip()
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        parts.append(_unquote_yaml_scalar(cleaned))
        idx += 1
    return parts, idx


def _parse_frontmatter_key_line(
    fm_lines: Sequence[str],
    idx: int,
    *,
    prefix: str,
    require: bool,
) -> tuple[tuple[str, str] | None, int]:
    key_match = _FRONTMATTER_KEY_RE.match(fm_lines[idx])
    if key_match is None:
        if require:
            raise ValueError(f"{prefix}invalid YAML frontmatter line")
        return None, idx
    key = key_match.group(1)
    val_raw = key_match.group(2).strip()
    block_indicator = val_raw if val_raw in _BLOCK_SCALAR_MARKERS else None
    head_val = "" if block_indicator is not None else _unquote_yaml_scalar(val_raw)
    cont_parts, next_idx = _consume_frontmatter_continuations(fm_lines, idx + 1)
    is_literal = block_indicator is not None and block_indicator.startswith("|")
    joiner = "\n" if is_literal else " "
    combined = joiner.join(part for part in (head_val, *cont_parts) if part)
    return (key, combined), next_idx


def parse_frontmatter_scalars(
    markdown_text: str,
    *,
    label: Path | str | None = None,
    require: bool = False,
) -> tuple[list[tuple[str, str]], str]:
    """Extract leading YAML frontmatter key/value pairs and body using Python stdlib only."""
    raw_fm, body = split_frontmatter_block(markdown_text, label=label, require=require)
    if raw_fm is None:
        return [], markdown_text
    prefix = f"{label}: " if label is not None else ""
    fm_lines = raw_fm.splitlines()
    pairs: list[tuple[str, str]] = []
    idx = 0
    while idx < len(fm_lines):
        line = fm_lines[idx]
        if not line.strip() or line.lstrip().startswith("#"):
            idx += 1
            continue
        entry, idx = _parse_frontmatter_key_line(
            fm_lines,
            idx,
            prefix=prefix,
            require=require,
        )
        if entry is None:
            return [], markdown_text
        pairs.append(entry)
    if not pairs and not require:
        return [], markdown_text
    return pairs, body

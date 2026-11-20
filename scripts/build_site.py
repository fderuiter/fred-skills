"""Compile repository Markdown and skill packages into a zero-dependency static HTML site."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

sys.dont_write_bytecode = True
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _markdown_utils import (
    consume_fenced_block,
    is_markdown_table_separator,
    match_atx_heading,
    match_fence_open,
    parse_frontmatter_scalars,
    split_markdown_table_cells,
    strip_reference_link_definitions,
    unique_heading_slug,
)
from _markdown_utils import (
    slugify_markdown_heading as slugify,
)
from build_experience import (
    SITE_DIRECTORIES,
    SITE_ROOT_FILES,
    _ignore_site_entries,
    _render_explorer_html,
    explorer_doc_guides,
)
from build_experience import (
    outputs as experience_outputs,
)
from fredlib.common import read_json
from fredlib.reports import SCRIPT, STYLE, _page_shell
from skill_tools import (
    ROOT,
    read_frontmatter,
    suite_members,
)

__all__ = [
    "SCRIPT",
    "STYLE",
    "build_site",
    "compile_markdown",
    "rewrite_link_target",
    "slugify",
]

ROOT_MARKDOWN_FILES: tuple[str, ...] = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "SECURITY.md",
    "evals/README.md",
)
AUXILIARY_ROOT_MARKDOWN: tuple[str, ...] = ROOT_MARKDOWN_FILES[1:]
GITHUB_BLOB_BASE_URL = "https://github.com/fderuiter/fred-skills/blob/main"

_HR_RE = re.compile(r"^\s*(?:-\s*-\s*-[-\s]*|\*\s*\*\s*\*[\*\s]*|_\s*_\s*_[_\s]*)$")
_UL_ITEM_RE = re.compile(r"^([ \t]*)[-*+]\s+(.*)$")
_OL_ITEM_RE = re.compile(r"^([ \t]*)\d+\.\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_INLINE_CODE_RE = re.compile(r"``(.+?)``|`([^`]+)`")
_INLINE_LINK_RE = re.compile(r"(!?)\[([^\]]+)\]\(([^)]+)\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")


def _rel_prefix_to_site_root(page_rel_path: str) -> str:
    depth = len(Path(page_rel_path).parts) - 1
    return "../" * depth


def _site_header_html(
    page_rel_path: str,
    subtitle: str = "documentation",
    root: Path = ROOT,
    compiled_md_paths: frozenset[str] | None = None,
) -> str:
    root_prefix = _rel_prefix_to_site_root(page_rel_path)
    guide_links = "".join(
        f'<a href="{root_prefix}docs/{slug}.html">{escape(label)}</a>'
        for slug, label in explorer_doc_guides(root)
        if compiled_md_paths is None or f"docs/{slug}.md" in compiled_md_paths
    )
    return (
        f'<header><p class="muted">fred-skills · {escape(subtitle)}</p>'
        f'<nav aria-label="Site navigation">'
        f'<a href="{root_prefix}index.html">Quickstart</a>'
        f'<a href="{root_prefix}docs/explorer.html">Skill explorer</a>'
        f'<a href="{root_prefix}docs/index.html">Docs</a>'
        f'<a href="{root_prefix}skills/index.html">Skills</a>'
        f"{guide_links}"
        f"</nav></header>"
    )


def _is_repo_hosted_skill_resource(rel_target: str) -> bool:
    parts = Path(rel_target).parts
    if len(parts) < 4 or parts[0] != "skills":
        return False
    subdir = parts[2]
    lower = rel_target.lower()
    return (subdir == "assets" and lower.endswith(".md")) or (
        subdir == "scripts" and lower.endswith(".py")
    )


def rewrite_link_target(
    target: str,
    source_rel_path: str,
    compiled_md_paths: frozenset[str],
    root: Path = ROOT,
) -> str:
    """Rewrite relative .md link targets to .html (or GitHub blob URLs for pruned skill resources)."""
    cleaned = target.strip().strip("<>\"'")
    parsed = urlsplit(cleaned)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return cleaned

    raw_path = unquote(parsed.path)
    lower_path = raw_path.lower()
    if not lower_path.endswith((".md", ".py")):
        return cleaned

    source_dir = (root.resolve() / source_rel_path).parent
    resolved = (source_dir / raw_path).resolve()
    try:
        rel_target = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return cleaned

    if lower_path.endswith(".md") and rel_target in compiled_md_paths:
        new_path = parsed.path[:-3] + ".html"
        return urlunsplit(("", "", new_path, parsed.query, parsed.fragment))

    if _is_repo_hosted_skill_resource(rel_target):
        blob_url = f"{GITHUB_BLOB_BASE_URL}/{rel_target}"
        blob_parts = urlsplit(blob_url)
        return urlunsplit(
            (
                blob_parts.scheme,
                blob_parts.netloc,
                blob_parts.path,
                parsed.query,
                parsed.fragment,
            )
        )

    return cleaned


def _format_escaped_emphasis(escaped_text: str) -> str:
    rendered = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_text)
    rendered = re.sub(
        r"(?<![\w*])\*([^\s*](?:[^*]*?[^\s*])?)\*(?![\w*])",
        r"<em>\1</em>",
        rendered,
    )
    return rendered


def _render_inline(
    raw: str,
    source_rel_path: str,
    compiled_md_paths: frozenset[str],
    ref_defs: Mapping[str, str],
    root: Path,
) -> str:
    """Render inline code, links, bold/italic, and escaped text safely."""
    code_tokens: list[str] = []
    link_tokens: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        content = match.group(1) if match.group(1) is not None else match.group(2)
        idx = len(code_tokens)
        code_tokens.append(f"<code>{escape(content, quote=True)}</code>")
        return f"\x00C{idx}\x00"

    working = _INLINE_CODE_RE.sub(_stash_code, raw)

    def _build_anchor(label_raw: str, url_raw: str, is_image: bool) -> str:
        rewritten = rewrite_link_target(
            url_raw, source_rel_path, compiled_md_paths, root=root
        )
        label_html = _format_escaped_emphasis(escape(label_raw, quote=True))
        for c_idx, c_html in enumerate(code_tokens):
            label_html = label_html.replace(f"\x00C{c_idx}\x00", c_html)
        if is_image:
            return f'<a href="{escape(rewritten, quote=True)}">{label_html or escape(rewritten)}</a>'
        return f'<a href="{escape(rewritten, quote=True)}">{label_html}</a>'

    def _stash_inline_link(match: re.Match[str]) -> str:
        is_img = bool(match.group(1))
        label_raw = match.group(2)
        url_raw = match.group(3).strip()
        idx = len(link_tokens)
        link_tokens.append(_build_anchor(label_raw, url_raw, is_img))
        return f"\x00L{idx}\x00"

    working = _INLINE_LINK_RE.sub(_stash_inline_link, working)

    if ref_defs:

        def _stash_ref_link(match: re.Match[str]) -> str:
            label_raw = match.group(1)
            ref_key = (match.group(2) or label_raw).strip().lower()
            target_url = ref_defs.get(ref_key)
            if target_url is None:
                return match.group(0)
            idx = len(link_tokens)
            link_tokens.append(_build_anchor(label_raw, target_url, False))
            return f"\x00L{idx}\x00"

        working = _REF_LINK_RE.sub(_stash_ref_link, working)

    escaped = _format_escaped_emphasis(escape(working, quote=True))
    for l_idx, l_html in enumerate(link_tokens):
        escaped = escaped.replace(f"\x00L{l_idx}\x00", l_html)
    for c_idx, c_html in enumerate(code_tokens):
        escaped = escaped.replace(f"\x00C{c_idx}\x00", c_html)
    return escaped


def _split_table_cells(line: str) -> list[str]:
    """Split a Markdown pipe-table row while ignoring pipes inside backticks or escaped pipes."""
    return split_markdown_table_cells(line)


def _is_table_separator(line: str) -> bool:
    return is_markdown_table_separator(line)


def _consume_fenced_block(lines: list[str], start_idx: int) -> tuple[str, int]:
    block = consume_fenced_block(lines, start_idx)
    indent_prefix = block.fence.indent
    lang = block.fence.language
    code_lines = [
        candidate[len(indent_prefix) :]
        if indent_prefix and candidate.startswith(indent_prefix)
        else candidate
        for _, candidate in block.lines
    ]
    escaped_code = escape("\n".join(code_lines), quote=False)
    lang_attr = f' class="language-{escape(lang, quote=True)}"' if lang else ""
    return f"<pre><code{lang_attr}>{escaped_code}</code></pre>", block.end_line


def _consume_table(
    lines: list[str],
    start_idx: int,
    inline_fn: Any,
) -> tuple[str, int]:
    headers = _split_table_cells(lines[start_idx])
    idx = start_idx + 2
    body_rows: list[list[str]] = []
    while idx < len(lines):
        row_line = lines[idx]
        if (
            not row_line.strip()
            or "|" not in row_line
            or match_fence_open(row_line) is not None
        ):
            break
        body_rows.append(_split_table_cells(row_line))
        idx += 1

    thead = (
        "<thead><tr>"
        + "".join(f"<th>{inline_fn(h)}</th>" for h in headers)
        + "</tr></thead>"
    )
    tbody_parts: list[str] = []
    for row in body_rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        cells_html = "".join(
            f"<td>{inline_fn(cell)}</td>" for cell in padded[: len(headers)]
        )
        tbody_parts.append(f"<tr>{cells_html}</tr>")
    tbody = f"<tbody>{''.join(tbody_parts)}</tbody>"
    return f'<div class="table-container"><table>{thead}{tbody}</table></div>', idx


def _match_list_item(line: str) -> tuple[str, int, str] | None:
    if _HR_RE.match(line):
        return None
    ul_m = _UL_ITEM_RE.match(line)
    if ul_m:
        indent = len(ul_m.group(1).expandtabs(4))
        return ("ul", indent, ul_m.group(2))
    ol_m = _OL_ITEM_RE.match(line)
    if ol_m:
        indent = len(ol_m.group(1).expandtabs(4))
        return ("ol", indent, ol_m.group(2))
    return None


def _consume_list(
    lines: list[str],
    start_idx: int,
    base_indent: int,
    list_tag: str,
    inline_fn: Any,
) -> tuple[str, int]:
    items_html: list[str] = []
    idx = start_idx

    while idx < len(lines):
        item_info = _match_list_item(lines[idx])
        if item_info is None:
            break
        tag, indent, first_text = item_info
        if indent < base_indent or (indent == base_indent and tag != list_tag):
            break
        if indent > base_indent:
            sub_html, idx = _consume_list(lines, idx, indent, tag, inline_fn)
            if items_html:
                items_html[-1] = items_html[-1][:-5] + sub_html + "</li>"
            else:
                items_html.append(f"<li>{sub_html}</li>")
            continue

        text_parts = [first_text.strip()]
        extra_blocks: list[str] = []
        idx += 1

        while idx < len(lines):
            nxt = lines[idx]
            if not nxt.strip():
                look = idx + 1
                while look < len(lines) and not lines[look].strip():
                    look += 1
                if look < len(lines):
                    next_indent = len(lines[look]) - len(lines[look].lstrip(" \t"))
                    next_item = _match_list_item(lines[look])
                    if (
                        match_fence_open(lines[look]) is not None
                        and next_indent > base_indent
                    ):
                        idx = look
                        fence_html, idx = _consume_fenced_block(lines, idx)
                        extra_blocks.append(fence_html)
                        continue
                    if next_item and next_item[1] >= base_indent:
                        idx = look
                        break
                    if (
                        next_indent > base_indent
                        and match_atx_heading(lines[look]) is None
                    ):
                        idx = look
                        continue
                idx = look
                break

            if match_fence_open(nxt) is not None:
                fence_indent = len(nxt) - len(nxt.lstrip(" \t"))
                if fence_indent > base_indent:
                    fence_html, idx = _consume_fenced_block(lines, idx)
                    extra_blocks.append(fence_html)
                    continue
                break

            sub_item = _match_list_item(nxt)
            if sub_item is not None:
                if sub_item[1] > base_indent:
                    sub_html, idx = _consume_list(
                        lines, idx, sub_item[1], sub_item[0], inline_fn
                    )
                    extra_blocks.append(sub_html)
                    continue
                break

            if match_atx_heading(nxt) is not None or _HR_RE.match(nxt):
                break

            line_indent = len(nxt.expandtabs(4)) - len(nxt.expandtabs(4).lstrip(" "))
            if line_indent > base_indent:
                text_parts.append(nxt.strip())
                idx += 1
                continue
            break

        lead_html = inline_fn(" ".join(p for p in text_parts if p))
        items_html.append(f"<li>{lead_html}{''.join(extra_blocks)}</li>")

    return f"<{list_tag}>{''.join(items_html)}</{list_tag}>", idx


def compile_markdown(
    markdown_text: str,
    source_rel_path: str,
    compiled_md_paths: frozenset[str],
    root: Path = ROOT,
) -> tuple[str, str]:
    """Compile Markdown text into (first_heading_title, rendered_html)."""
    frontmatter_pairs, body_markdown = parse_frontmatter_scalars(markdown_text)
    cleaned_md, ref_defs = strip_reference_link_definitions(body_markdown)
    lines = cleaned_md.splitlines()
    seen_slugs: dict[str, int] = {}
    first_title = ""
    blocks: list[str] = []
    if frontmatter_pairs:
        fm_items = "".join(
            f"<dt><code>{escape(k, quote=True)}</code></dt><dd>{escape(v, quote=True)}</dd>"
            for k, v in frontmatter_pairs
        )
        blocks.append(
            f'<section class="frontmatter" aria-label="Document metadata"><dl>{fm_items}</dl></section>'
        )

    def _inline(text_segment: str) -> str:
        return _render_inline(
            text_segment,
            source_rel_path=source_rel_path,
            compiled_md_paths=compiled_md_paths,
            ref_defs=ref_defs,
            root=root,
        )

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue

        if match_fence_open(line) is not None:
            fence_html, idx = _consume_fenced_block(lines, idx)
            blocks.append(fence_html)
            continue

        heading_info = match_atx_heading(line)
        if heading_info is not None:
            level, raw_heading = heading_info
            if not first_title:
                first_title = re.sub(r"[`*_]", "", raw_heading).strip()
            slug = unique_heading_slug(raw_heading, seen_slugs)
            blocks.append(
                f'<h{level} id="{escape(slug, quote=True)}">{_inline(raw_heading)}</h{level}>'
            )
            idx += 1
            continue

        if _HR_RE.match(line):
            blocks.append("<hr>")
            idx += 1
            continue

        if "|" in line and idx + 1 < len(lines) and _is_table_separator(lines[idx + 1]):
            table_html, idx = _consume_table(lines, idx, _inline)
            blocks.append(table_html)
            continue

        bq_match = _BLOCKQUOTE_RE.match(line)
        if bq_match:
            quote_parts: list[str] = []
            while idx < len(lines):
                m = _BLOCKQUOTE_RE.match(lines[idx])
                if not m:
                    break
                if m.group(1).strip():
                    quote_parts.append(m.group(1).strip())
                idx += 1
            blocks.append(
                f"<blockquote><p>{_inline(' '.join(quote_parts))}</p></blockquote>"
            )
            continue

        list_info = _match_list_item(line)
        if list_info is not None:
            list_html, idx = _consume_list(
                lines, idx, list_info[1], list_info[0], _inline
            )
            blocks.append(list_html)
            continue

        para_lines: list[str] = []
        while idx < len(lines):
            cur = lines[idx]
            if (
                not cur.strip()
                or match_fence_open(cur) is not None
                or match_atx_heading(cur) is not None
                or _HR_RE.match(cur)
                or _BLOCKQUOTE_RE.match(cur)
                or _match_list_item(cur) is not None
                or (
                    "|" in cur
                    and idx + 1 < len(lines)
                    and _is_table_separator(lines[idx + 1])
                )
            ):
                break
            para_lines.append(cur.strip())
            idx += 1
        blocks.append(f"<p>{_inline(' '.join(para_lines))}</p>")

    if not first_title and frontmatter_pairs:
        fm_map = dict(frontmatter_pairs)
        first_title = fm_map.get("title") or fm_map.get("name") or ""

    return first_title, "\n".join(blocks)


def _render_skill_metadata_section(
    frontmatter: Mapping[str, Any],
    catalog_entry: Mapping[str, Any] | None,
    suite_info: Mapping[str, Any] | None,
    skill_rel_dir: str,
) -> str:
    skill_name = str(frontmatter.get("name") or Path(skill_rel_dir).name)
    description = str(frontmatter.get("description") or "")
    suite_name = str(
        (suite_info or {}).get("suite")
        or (catalog_entry or {}).get("suite")
        or "standalone"
    )
    phase_title = str(
        (suite_info or {}).get("phase_title")
        or (catalog_entry or {}).get("phase_title")
        or "Specialist"
    )
    maturity = str((catalog_entry or {}).get("maturity") or "experimental")
    symptoms = list((catalog_entry or {}).get("symptoms") or [])
    symptoms_html = (
        f"<p><b>Symptoms:</b> {escape(', '.join(str(s) for s in symptoms))}</p>"
        if symptoms
        else ""
    )
    install_cmd = (
        f"python scripts/fred.py install --destination /path/to/repo "
        f"--host codex --skill {skill_name}"
    )
    return (
        f'<section class="skill-metadata" aria-label="Skill metadata">'
        f"<p><code>{escape(skill_name)}</code> · "
        f'<span class="badge badge-info info">{escape(suite_name)}</span> '
        f'<span class="badge badge-low low">{escape(phase_title)}</span> '
        f'<span class="badge badge-medium medium">{escape(maturity)}</span></p>'
        f"<p>{escape(description)}</p>"
        f"{symptoms_html}"
        f"<pre><code>{escape(install_cmd)}</code></pre>"
        f"</section>"
    )


def _is_excluded_markdown_parts(rel_parts: tuple[str, ...]) -> bool:
    if any(
        part in {"__pycache__", ".DS_Store"}
        or part.endswith((".pyc", ".pyo", ".pyd", ".telemetry.json"))
        for part in rel_parts
    ):
        return True
    return len(rel_parts) >= 3 and rel_parts[0] == "skills" and rel_parts[2] == "assets"


def _discover_compilable_markdown_paths(root: Path) -> list[str]:
    """Return sorted repo-relative POSIX paths of all Markdown files compiled into dist/site/."""
    candidates: set[str] = set()
    for rel in ROOT_MARKDOWN_FILES:
        if (root / rel).is_file():
            candidates.add(rel)
    for rel_dir in ("docs", "skills"):
        src_dir = root / rel_dir
        if not src_dir.is_dir():
            continue
        for md_path in src_dir.rglob("*.md"):
            if not md_path.is_file():
                continue
            rel_parts = md_path.relative_to(root).parts
            if _is_excluded_markdown_parts(rel_parts):
                continue
            candidates.add(md_path.relative_to(root).as_posix())
    if (root / "catalog/skills.json").is_file() and (root / "docs").is_dir():
        candidates.add("docs/experience.md")
    return sorted(candidates)


def _prune_empty_directories(directory: Path) -> None:
    """Remove empty subdirectories bottom-up after filtered static file copying."""
    for subdir in sorted(
        (p for p in directory.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            next(subdir.iterdir())
        except StopIteration:
            subdir.rmdir()


def _render_docs_hub_body(
    compiled_titles: Mapping[str, str],
    compiled_md_paths: frozenset[str],
    root: Path,
) -> str:
    header = _site_header_html(
        "docs/index.html",
        subtitle="documentation",
        root=root,
        compiled_md_paths=compiled_md_paths,
    )
    guide_labels = dict(explorer_doc_guides(root))
    docs_md_paths = sorted(p for p in compiled_md_paths if p.startswith("docs/"))
    docs_items: list[str] = [
        '<li><a href="explorer.html"><strong>Skill explorer</strong></a> — Interactive task, symptom, pack, and host search (<code>docs/explorer.html</code>).</li>'
    ]
    for rel_md in docs_md_paths:
        rel_from_docs = Path(rel_md).relative_to("docs").with_suffix(".html").as_posix()
        stem = Path(rel_md).stem
        title = compiled_titles.get(rel_md) or guide_labels.get(stem) or stem
        label_prefix = (
            f"{escape(guide_labels[stem])} — "
            if stem in guide_labels
            and Path(rel_md).parent == Path("docs")
            and guide_labels[stem].lower() != title.lower()
            else ""
        )
        docs_items.append(
            f'<li><a href="{escape(rel_from_docs, quote=True)}">{label_prefix}{escape(title)}</a> '
            f"(<code>{escape(rel_from_docs)}</code>)</li>"
        )

    root_items: list[str] = []
    for rel_md in ROOT_MARKDOWN_FILES:
        if rel_md not in compiled_md_paths:
            continue
        rel_html = rel_md[:-3] + ".html"
        title = compiled_titles.get(rel_md) or Path(rel_md).stem
        root_items.append(
            f'<li><a href="../{escape(rel_html, quote=True)}">{escape(title)}</a> '
            f"(<code>{escape(rel_html)}</code>)</li>"
        )
    root_items.append(
        '<li><a href="../skills/index.html">Skill packages</a> (<code>skills/index.html</code>)</li>'
    )

    return (
        f"{header}\n"
        f"<section><h1>Documentation</h1>"
        f"<p>Browse compiled guides, release notes, and repository references for <code>fred-skills</code>.</p>"
        f"<pre><code>python scripts/fred.py --help</code></pre>"
        f"<h2>Guides and release notes</h2><ul>{''.join(docs_items)}</ul>"
        f"<h2>Repository references</h2><ul>{''.join(root_items)}</ul>"
        f"</section>"
    )


def _render_skills_hub_body(
    catalog_list: list[dict[str, Any]],
    suites_list: list[dict[str, Any]] | None,
    compiled_md_paths: frozenset[str],
    root: Path,
) -> str:
    header = _site_header_html(
        "skills/index.html",
        subtitle="skill packages",
        root=root,
        compiled_md_paths=compiled_md_paths,
    )
    by_suite: dict[str, list[dict[str, Any]]] = {}
    for entry in catalog_list:
        suite_key = str(entry.get("suite") or "standalone")
        by_suite.setdefault(suite_key, []).append(entry)

    ordered_suites: list[tuple[str, str]] = []
    seen_suites: set[str] = set()
    for suite_obj in suites_list or []:
        sname = str(suite_obj.get("name") or "")
        if sname and sname in by_suite:
            stitle = str(suite_obj.get("title") or sname)
            ordered_suites.append((sname, stitle))
            seen_suites.add(sname)
    for sname in sorted(by_suite):
        if sname not in seen_suites:
            ordered_suites.append((sname, sname))

    suite_sections: list[str] = []
    for sname, stitle in ordered_suites:
        rows = "".join(
            f"<tr>"
            f'<td><a href="{escape(str(item["name"]), quote=True)}/index.html"><code>{escape(str(item["name"]))}</code></a> '
            f'(<a href="{escape(str(item["name"]), quote=True)}/SKILL.html">SKILL.html</a>)</td>'
            f"<td>{escape(str(item.get('title') or item['name']))}</td>"
            f"<td>{escape(str(item.get('description') or ''))}</td>"
            f'<td><span class="badge badge-medium medium">{escape(str(item.get("maturity") or "experimental"))}</span></td>'
            f"</tr>"
            for item in by_suite[sname]
        )
        suite_sections.append(
            f'<section id="{escape(sname, quote=True)}">'
            f"<h2>{escape(stitle)} (<code>{escape(sname)}</code>)</h2>"
            f'<div class="table-container"><table><thead><tr><th>Package</th><th>Title</th><th>Description</th><th>Maturity</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></section>"
        )

    return (
        f"{header}\n"
        f"<section><h1>Skill packages</h1>"
        f"<p><strong>{len(catalog_list)} standalone skills</strong> across {len(ordered_suites)} suites. "
        f'Filter interactively in the <a href="../docs/explorer.html">Skill explorer</a> or view the '
        f'<a href="../docs/catalog.html">catalog table</a>.</p>'
        f"<pre><code>python scripts/fred.py install --destination /path/to/repo --host codex --pack starter</code></pre>"
        f"</section>\n" + "\n".join(suite_sections)
    )


def build_site(output_dir: Path, root: Path = ROOT) -> Path:
    """Compile all documentation, explorer, and skill HTML pages into output_dir."""
    resolved_root = root.resolve()
    resolved_out = (
        output_dir.resolve()
        if output_dir.is_absolute()
        else (resolved_root / output_dir).resolve()
    )
    if resolved_out == resolved_root or resolved_root.is_relative_to(resolved_out):
        raise ValueError(
            f"Refusing to stage site into repository root or ancestor: {resolved_out}"
        )

    catalog_list: list[dict[str, Any]] = read_json(
        resolved_root / "catalog/skills.json"
    )["skills"]
    recipes: dict[str, dict[str, Any]] = read_json(
        resolved_root / "catalog/recipes.json"
    )["recipes"]
    packs: dict[str, dict[str, Any]] = read_json(resolved_root / "catalog/packs.json")[
        "packs"
    ]
    hosts: dict[str, dict[str, Any]] = read_json(resolved_root / "catalog/hosts.json")[
        "hosts"
    ]
    catalog_by_name = {entry["name"]: entry for entry in catalog_list}
    suites_list: list[dict[str, Any]] | None = (
        read_json(resolved_root / "catalog/suites.json")["suites"]
        if (resolved_root / "catalog/suites.json").is_file()
        else None
    )
    suites_by_skill = (
        suite_members(resolved_root)
        if (resolved_root / "catalog/suites.json").is_file()
        else {}
    )
    generated_experience = experience_outputs(resolved_root)

    if resolved_out.exists():
        shutil.rmtree(resolved_out)
    resolved_out.mkdir(parents=True, exist_ok=True)

    (resolved_out / ".nojekyll").write_text("", encoding="utf-8", newline="\n")

    for rel_dir in SITE_DIRECTORIES:
        src_dir = resolved_root / rel_dir
        if src_dir.is_dir():
            shutil.copytree(
                src_dir,
                resolved_out / rel_dir,
                ignore=_ignore_site_entries,
            )

    for rel_file in SITE_ROOT_FILES:
        src_file = resolved_root / rel_file
        if src_file.is_file():
            dst_file = resolved_out / rel_file
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)

    _prune_empty_directories(resolved_out)

    compilable_rel_paths = _discover_compilable_markdown_paths(resolved_root)
    compiled_set = frozenset(compilable_rel_paths)
    compiled_titles: dict[str, str] = {}

    for rel_md in compilable_rel_paths:
        src_md = resolved_root / rel_md
        rel_html = rel_md[:-3] + ".html"
        is_skill_root = (
            rel_md.startswith("skills/")
            and len(Path(rel_md).parts) == 3
            and Path(rel_md).name == "SKILL.md"
        )

        if is_skill_root:
            frontmatter, md_body = read_frontmatter(src_md)
            skill_name = str(frontmatter.get("name") or Path(rel_md).parts[1])
            meta_section = _render_skill_metadata_section(
                frontmatter=frontmatter,
                catalog_entry=catalog_by_name.get(skill_name),
                suite_info=suites_by_skill.get(skill_name),
                skill_rel_dir=str(Path(rel_md).parent.as_posix()),
            )
            heading_title, body_content = compile_markdown(
                md_body,
                source_rel_path=rel_md,
                compiled_md_paths=compiled_set,
                root=resolved_root,
            )
            compiled_titles[rel_md] = heading_title or skill_name
            page_title = f"{escape(heading_title or skill_name)} — fred-skills"
            full_body = (
                f"{_site_header_html(rel_html, subtitle='skill package', root=resolved_root, compiled_md_paths=compiled_set)}\n"
                f"{meta_section}\n"
                f"<section>{body_content}</section>"
            )
        else:
            raw_md = (
                generated_experience[rel_md]
                if rel_md in generated_experience
                else src_md.read_text(encoding="utf-8")
            )
            heading_title, body_content = compile_markdown(
                raw_md,
                source_rel_path=rel_md,
                compiled_md_paths=compiled_set,
                root=resolved_root,
            )
            compiled_titles[rel_md] = heading_title or Path(rel_md).stem
            page_title = (
                "fred-skills — documentation"
                if rel_md == "README.md"
                else f"{escape(heading_title or Path(rel_md).stem)} — fred-skills"
            )
            full_body = (
                f"{_site_header_html(rel_html, root=resolved_root, compiled_md_paths=compiled_set)}\n"
                f"<section>{body_content}</section>"
            )

        rendered_page = _page_shell(page_title, full_body, include_script=False)
        dst_html = resolved_out / rel_html
        dst_html.parent.mkdir(parents=True, exist_ok=True)
        dst_html.write_text(rendered_page, encoding="utf-8", newline="\n")
        if is_skill_root:
            (dst_html.parent / "index.html").write_text(
                rendered_page, encoding="utf-8", newline="\n"
            )
        if rel_md == "README.md":
            (resolved_out / "index.html").write_text(
                rendered_page, encoding="utf-8", newline="\n"
            )

    docs_hub_html = _page_shell(
        "Documentation — fred-skills",
        _render_docs_hub_body(compiled_titles, compiled_set, resolved_root),
        include_script=False,
    )
    docs_hub_target = resolved_out / "docs/index.html"
    docs_hub_target.parent.mkdir(parents=True, exist_ok=True)
    docs_hub_target.write_text(docs_hub_html, encoding="utf-8", newline="\n")

    skills_hub_html = _page_shell(
        "Skill packages — fred-skills",
        _render_skills_hub_body(catalog_list, suites_list, compiled_set, resolved_root),
        include_script=False,
    )
    skills_hub_target = resolved_out / "skills/index.html"
    skills_hub_target.parent.mkdir(parents=True, exist_ok=True)
    skills_hub_target.write_text(skills_hub_html, encoding="utf-8", newline="\n")

    web_explorer_html = _render_explorer_html(
        catalog_list,
        recipes,
        suites_list,
        packs=packs,
        hosts=hosts,
        root_prefix="../",
        docs_prefix="",
        web_mode=True,
        root=resolved_root,
    )
    explorer_target = resolved_out / "docs/explorer.html"
    explorer_target.parent.mkdir(parents=True, exist_ok=True)
    explorer_target.write_text(web_explorer_html, encoding="utf-8", newline="\n")

    return resolved_out


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for scripts/build_site.py."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/site"),
        help="Destination directory for compiled static HTML site (default: dist/site).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    site_dir = build_site(args.output, root=args.root)
    print(f"PASS: compiled static HTML site in {site_dir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

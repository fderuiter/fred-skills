#!/usr/bin/env python3
"""Validate relative file links and references in documentation markdown and HTML files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from skill_tools import (
    ROOT,
    extract_document_anchors,
    extract_link_fragment,
    extract_relative_links,
    resolve_relative_link_target,
)

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "audit-output",
    ".pytest_cache",
    ".mypy_cache",
    ".fred-skills",
}
NESTED_EXCLUDE_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
DOC_EXTENSIONS = {".md", ".html"}
_HTML_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_FENCE_MARKER_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _strip_non_link_blocks(content: str, is_html: bool) -> str:
    """Strip HTML script/style blocks or Markdown fenced code blocks before scanning links."""
    if is_html:
        return _HTML_SCRIPT_STYLE_RE.sub("", content)
    kept: list[str] = []
    fence_char = ""
    fence_len = 0
    for line in content.splitlines(keepends=True):
        match = _FENCE_MARKER_RE.match(line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len:
                fence_char = ""
                fence_len = 0
                continue
        if not fence_char:
            kept.append(line)
    return "".join(kept)


def _get_cached_anchors(
    target_path: Path,
    cache: dict[Path, set[str]],
) -> set[str] | None:
    """Load and cache valid #fragment anchors for a Markdown or HTML file."""
    resolved = target_path.resolve()
    if resolved in cache:
        return cache[resolved]
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError:
        return None
    anchors = extract_document_anchors(text, suffix=resolved.suffix)
    cache[resolved] = anchors
    return anchors


def check_file_links(
    file_path: Path,
    root: Path = ROOT,
    anchor_cache: dict[Path, set[str]] | None = None,
) -> list[str]:
    """Check that all relative links and #fragment anchors in a markdown or HTML file resolve cleanly."""
    errors: list[str] = []
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{file_path}: Cannot read file: {exc}"]

    resolved_file = file_path.resolve()
    file_dir = resolved_file.parent
    is_html = resolved_file.suffix.lower() == ".html"
    active_cache = anchor_cache if anchor_cache is not None else {}
    active_cache.setdefault(
        resolved_file, extract_document_anchors(content, suffix=resolved_file.suffix)
    )

    scannable = _strip_non_link_blocks(content, is_html=is_html)
    targets = extract_relative_links(scannable, include_fragments=True)

    try:
        rel_file: Path = Path(resolved_file.relative_to(root.resolve()).as_posix())
    except ValueError:
        rel_file = file_path

    for target in targets:
        fragment = extract_link_fragment(target)
        resolved = resolve_relative_link_target(target, file_dir)
        if resolved is None:
            if fragment is not None:
                self_anchors = _get_cached_anchors(resolved_file, active_cache)
                if self_anchors is not None and fragment not in self_anchors:
                    errors.append(
                        f"{rel_file.as_posix()}: Broken anchor link '{target}' -> anchor '#{fragment}' does not exist"
                    )
            continue

        if not resolved.exists():
            errors.append(
                f"{rel_file.as_posix()}: Broken relative link '{target}' -> path does not exist"
            )
            continue
        if is_html and resolved.is_dir() and not (resolved / "index.html").is_file():
            errors.append(
                f"{rel_file.as_posix()}: Broken directory link '{target}' -> missing index.html"
            )
            continue

        if fragment is not None:
            anchor_target = resolved / "index.html" if resolved.is_dir() else resolved
            if (
                anchor_target.is_file()
                and anchor_target.suffix.lower() in DOC_EXTENSIONS
            ):
                target_anchors = _get_cached_anchors(anchor_target, active_cache)
                if target_anchors is not None and fragment not in target_anchors:
                    errors.append(
                        f"{rel_file.as_posix()}: Broken anchor link '{target}' -> anchor '#{fragment}' does not exist"
                    )
    return errors


def _collect_dir_doc_files(directory: Path) -> list[Path]:
    """Collect all .md and .html files within an explicit target directory."""
    resolved_dir = directory.resolve()
    collected: list[Path] = []
    for path in resolved_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        rel_parts = path.relative_to(resolved_dir).parts
        if any(part in NESTED_EXCLUDE_DIRS for part in rel_parts):
            continue
        collected.append(path)
    return collected


def _check_orphan_docs_guides(
    docs_dir: Path,
    scan_files: list[Path],
    display_root: Path,
) -> list[str]:
    """Verify every documentation file in docs_dir has at least one inbound relative link."""
    if not docs_dir.is_dir():
        return []
    inbound_targets: set[Path] = set()
    for src_file in scan_files:
        if not src_file.is_file():
            continue
        try:
            content = src_file.read_text(encoding="utf-8")
        except OSError:
            continue
        resolved_src = src_file.resolve()
        is_html = resolved_src.suffix.lower() == ".html"
        scannable = _strip_non_link_blocks(content, is_html=is_html)
        for target in extract_relative_links(scannable, include_fragments=False):
            resolved_target = resolve_relative_link_target(target, resolved_src.parent)
            if resolved_target is None or resolved_target == resolved_src:
                continue
            inbound_targets.add(resolved_target)
            if resolved_target.is_dir() and (resolved_target / "index.html").is_file():
                inbound_targets.add((resolved_target / "index.html").resolve())

    orphan_errors: list[str] = []
    for guide_path in sorted(docs_dir.iterdir()):
        if not guide_path.is_file() or guide_path.suffix.lower() not in DOC_EXTENSIONS:
            continue
        resolved_guide = guide_path.resolve()
        if resolved_guide not in inbound_targets:
            try:
                rel_guide = resolved_guide.relative_to(
                    display_root.resolve()
                ).as_posix()
            except ValueError:
                rel_guide = guide_path.as_posix()
            orphan_errors.append(
                f"{rel_guide}: Orphaned documentation file in docs/ (zero inbound relative links)"
            )
    return orphan_errors


def check_docs(root: Path = ROOT, target_files: list[Path] | None = None) -> list[str]:
    """Scan markdown and HTML documentation files and verify all relative targets exist."""
    errors: list[str] = []
    resolved_root = root.resolve()
    files: list[Path] = []
    orphan_check_scopes: list[tuple[Path, list[Path]]] = []

    if target_files:
        for raw_target in target_files:
            candidate = Path(raw_target)
            if (
                not candidate.is_absolute()
                and not candidate.exists()
                and (resolved_root / candidate).exists()
            ):
                candidate = resolved_root / candidate
            resolved_candidate = candidate.resolve()
            if not resolved_candidate.exists():
                errors.append(f"{raw_target}: Target path does not exist")
                continue
            if resolved_candidate.is_dir():
                dir_files = _collect_dir_doc_files(resolved_candidate)
                files.extend(dir_files)
                if (resolved_candidate / "docs").is_dir():
                    orphan_check_scopes.append((resolved_candidate / "docs", dir_files))
            elif resolved_candidate.suffix.lower() in DOC_EXTENSIONS:
                files.append(resolved_candidate)
    else:
        repo_files: list[Path] = []
        for path in resolved_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
                continue
            rel_parts = path.relative_to(resolved_root).parts
            if any(part in EXCLUDE_DIRS for part in rel_parts):
                continue
            repo_files.append(path)
        files.extend(repo_files)
        if (resolved_root / "docs").is_dir():
            orphan_check_scopes.append((resolved_root / "docs", repo_files))
        staged_site = resolved_root / "dist" / "site"
        if staged_site.is_dir():
            staged_files = _collect_dir_doc_files(staged_site)
            files.extend(staged_files)
            if (staged_site / "docs").is_dir():
                orphan_check_scopes.append((staged_site / "docs", staged_files))

    anchor_cache: dict[Path, set[str]] = {}
    for doc_file in sorted(set(files)):
        if not doc_file.is_file():
            continue
        errors.extend(
            check_file_links(doc_file, root=resolved_root, anchor_cache=anchor_cache)
        )

    for docs_dir, scope_files in orphan_check_scopes:
        errors.extend(
            _check_orphan_docs_guides(docs_dir, scope_files, display_root=resolved_root)
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that relative file links in markdown and HTML documents resolve to existing targets."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific markdown/HTML files or directories to verify (defaults to repository docs and dist/site if present)",
    )

    args = parser.parse_args()
    target_files = [Path(f) for f in args.files] if args.files else None
    errors = check_docs(root=ROOT, target_files=target_files)

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"FAILED: Found {len(errors)} broken relative link(s)", file=sys.stderr)
        return 1

    file_count = len(target_files) if target_files else "all"
    print(
        f"PASS: Relative links verified across {file_count} markdown documentation files."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

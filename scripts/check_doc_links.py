#!/usr/bin/env python3
"""Validate relative file links and references in documentation markdown and HTML files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from skill_tools import ROOT, extract_relative_links, resolve_relative_link_target

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


def check_file_links(file_path: Path, root: Path = ROOT) -> list[str]:
    """Check that all relative links in a markdown or HTML file resolve cleanly."""
    errors = []
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{file_path}: Cannot read file: {exc}"]

    targets = extract_relative_links(content)
    resolved_file = file_path.resolve()
    file_dir = resolved_file.parent
    is_html = resolved_file.suffix.lower() == ".html"

    try:
        rel_file: Path = Path(resolved_file.relative_to(root.resolve()).as_posix())
    except ValueError:
        rel_file = file_path

    for target in targets:
        resolved = resolve_relative_link_target(target, file_dir)
        if resolved is None:
            continue

        if not resolved.exists():
            errors.append(
                f"{rel_file.as_posix()}: Broken relative link '{target}' -> path does not exist"
            )
        elif is_html and resolved.is_dir() and not (resolved / "index.html").is_file():
            errors.append(
                f"{rel_file.as_posix()}: Broken directory link '{target}' -> missing index.html"
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


def check_docs(root: Path = ROOT, target_files: list[Path] | None = None) -> list[str]:
    """Scan markdown and HTML documentation files and verify all relative targets exist."""
    errors: list[str] = []
    resolved_root = root.resolve()
    files: list[Path] = []

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
                files.extend(_collect_dir_doc_files(resolved_candidate))
            elif resolved_candidate.suffix.lower() in DOC_EXTENSIONS:
                files.append(resolved_candidate)
    else:
        for path in resolved_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS:
                continue
            rel_parts = path.relative_to(resolved_root).parts
            if any(part in EXCLUDE_DIRS for part in rel_parts):
                continue
            files.append(path)
        staged_site = resolved_root / "dist" / "site"
        if staged_site.is_dir():
            files.extend(_collect_dir_doc_files(staged_site))

    for doc_file in sorted(set(files)):
        if not doc_file.is_file():
            continue
        errors.extend(check_file_links(doc_file, root=resolved_root))

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

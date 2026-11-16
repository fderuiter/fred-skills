#!/usr/bin/env python3
"""Validate relative file links and references in documentation markdown files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from skill_tools import ROOT, extract_relative_links

EXCLUDE_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


def check_file_links(file_path: Path, root: Path = ROOT) -> list[str]:
    """Check that all relative links in a markdown file resolve to existing files."""
    errors = []
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{file_path}: Cannot read file: {exc}"]

    targets = extract_relative_links(content)
    file_dir = file_path.resolve().parent

    for target in targets:
        parsed = urlsplit(target)
        raw_path = unquote(parsed.path)
        # Split on fragment or query if any remained
        clean_path = raw_path.split("#")[0].split("?")[0]
        if not clean_path:
            continue

        resolved = (file_dir / clean_path).resolve()
        if not resolved.exists():
            try:
                rel_file = file_path.resolve().relative_to(root.resolve())
            except ValueError:
                rel_file = file_path
            errors.append(
                f"{rel_file}: Broken relative link '{target}' -> path does not exist"
            )
    return errors


def check_docs(root: Path = ROOT, target_files: list[Path] | None = None) -> list[str]:
    """Scan markdown documentation files and verify all relative file paths exist."""
    errors = []
    if target_files:
        files = [
            Path(f).resolve() for f in target_files if Path(f).suffix.lower() == ".md"
        ]
    else:
        files = []
        for path in root.resolve().rglob("*.md"):
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            files.append(path)

    for md_file in sorted(files):
        if not md_file.is_file():
            continue
        file_errors = check_file_links(md_file, root=root)
        errors.extend(file_errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that relative file links in markdown documents resolve to existing files."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific markdown files to verify (defaults to all markdown files in repo)",
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

"""Small authoring utilities. No agent host or network access required."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import yaml

__all__ = [
    "ROOT",
    "build_outputs",
    "extract_relative_links",
    "read_frontmatter",
    "suite_members",
]

ROOT = Path(__file__).resolve().parents[1]

# Matches markdown links [text](target), images ![text](target)
MD_LINK_RE = re.compile(r"!?\[.*?\]\(([^)]+)\)")
# Matches HTML href="..." or src="..."
HTML_LINK_RE = re.compile(r"""(?:href|src)=["\x27]([^"\x27]+)["\x27]""")
# Matches reference-style link definitions [label]: target
MD_REF_LINK_RE = re.compile(r"""^\[[^\]]+\]:\s*(\S+)""", re.MULTILINE)


def extract_relative_links(content: str) -> list[str]:
    """Extract all link targets from markdown content."""
    raw_targets = (
        MD_LINK_RE.findall(content)
        + HTML_LINK_RE.findall(content)
        + MD_REF_LINK_RE.findall(content)
    )
    relative_paths = []
    for target in raw_targets:
        target = target.strip().strip("<>\"'")
        parsed = urlsplit(target)
        # Skip external links and in-page anchor links
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        relative_paths.append(target)
    return relative_paths


def read_frontmatter(path: Path) -> tuple[dict, str]:
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


def suite_members(root: Path = ROOT) -> dict[str, dict]:
    document = json.loads((root / "catalog/suites.json").read_text(encoding="utf-8"))
    members = {}
    for suite in document["suites"]:
        for phase in suite["phases"]:
            for name in phase["skills"]:
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
                    "suite": suite["name"],
                    "phase": phase["id"],
                    "phase_title": phase["title"],
                }
    return members


def build_outputs(
    outputs: Mapping[str | Path, str],
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

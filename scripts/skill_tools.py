"""Small authoring utilities. No agent host or network access required."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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

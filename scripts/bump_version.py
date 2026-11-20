#!/usr/bin/env python3
"""Automate SemVer progression, manifest synchronization, and release note generation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

DISTRIBUTABLE_PATHS: tuple[str, ...] = (
    "skills/",
    "catalog/",
    "shared/",
    "scripts/fredlib/",
)

_SUITE_DESCRIPTION = (
    "Portable engineering workflows for investigating repositories, planning"
    " useful fixes, and verifying that those fixes work."
)
_AUTHOR: dict[str, str] = {"name": "fderuiter"}

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_RELEASE_DOWNLOAD_URL_RE = re.compile(
    r"(https://github\.com/fderuiter/fred-skills/releases/download/v)"
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?(?=/)"
)
_PACK_ARCHIVE_STEM_RE = re.compile(
    r"\bfred-skills-(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:[-+][0-9A-Za-z.-]+)?-([a-z0-9-]+)\.(tar|zip)\b"
)
_README_RELEASE_NOTES_LINK_RE = re.compile(
    r"\[(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?"
    r"(\s+release notes\]\(docs/release-notes-)"
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?"
    r"(\.md\))"
)
_EXPLORER_RELEASE_NOTES_HREF_RE = re.compile(
    r'(<a href="release-notes-)'
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?"
    r'(\.md">Release notes</a>)'
)
_RELEASING_NOTES_EXAMPLE_RE = re.compile(
    r"\(such as\s+\[release-notes-[^)]+\)"
    r"(?:\s*(?:,\s*|\s+and\s+)(?:archived\s+)?\[release-notes-[^)]+\))*"
    r"(?=,\s*or\s+`--generate-notes`)",
    re.DOTALL,
)
_RELEASE_NOTES_FILENAME_RE = re.compile(r"^release-notes-(.+)\.md$")

BumpKind = Literal["none", "patch", "minor"]


@dataclass(frozen=True)
class BumpResult:
    """Structured summary of a SemVer evaluation or bump operation."""

    bumped: bool
    previous_version: str
    new_version: str
    bump_kind: BumpKind
    reason: str
    changed_distributable_files: tuple[str, ...]
    updated_files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "bumped": self.bumped,
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "release_tag": f"v{self.new_version}",
            "bump_kind": self.bump_kind,
            "reason": self.reason,
            "changed_distributable_files": list(self.changed_distributable_files),
            "updated_files": list(self.updated_files),
        }


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a SemVer 2.0.0 string and return its (major, minor, patch) core tuple."""
    match = _SEMVER_RE.match(version.strip())
    if match is None:
        raise ValueError(f"Invalid SemVer version: {version!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_lf_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _has_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def _tag_exists(root: Path, tag: str) -> bool:
    if not _has_git_repo(root):
        return False
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _git_blob_bytes(root: Path, rev_path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "cat-file", "blob", rev_path],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_json_dict(root: Path, tag: str, rel_path: str) -> dict[str, Any] | None:
    raw = _git_blob_bytes(root, f"refs/tags/{tag}:{rel_path}")
    if raw is None:
        return None
    loaded = json.loads(raw.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _worktree_json_dict(root: Path, rel_path: str) -> dict[str, Any] | None:
    target = root / rel_path
    if not target.is_file():
        return None
    loaded = json.loads(target.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def detect_distributable_changes(root: Path, tag: str) -> list[str]:
    """Return sorted distributable file paths modified between refs/tags/<tag> and HEAD."""
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            f"refs/tags/{tag}..HEAD",
            "--",
            *DISTRIBUTABLE_PATHS,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted({line.strip() for line in proc.stdout.splitlines() if line.strip()})


def _skills_membership(doc: Mapping[str, Any] | None) -> frozenset[str]:
    if doc is None:
        return frozenset()
    raw = doc.get("skills")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(
        str(entry["name"])
        for entry in raw
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    )


def _suites_membership(doc: Mapping[str, Any] | None) -> dict[str, frozenset[str]]:
    if doc is None:
        return {}
    raw = doc.get("suites")
    if not isinstance(raw, list):
        return {}
    result: dict[str, frozenset[str]] = {}
    for suite in raw:
        if not isinstance(suite, dict) or not isinstance(suite.get("name"), str):
            continue
        phases = suite.get("phases")
        members: set[str] = set()
        if isinstance(phases, list):
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                skills = phase.get("skills")
                if isinstance(skills, list):
                    members.update(s for s in skills if isinstance(s, str))
        result[suite["name"]] = frozenset(members)
    return result


def _packs_membership(doc: Mapping[str, Any] | None) -> dict[str, frozenset[str]]:
    if doc is None:
        return {}
    raw = doc.get("packs")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, frozenset[str]] = {}
    for pack_name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        skills = cfg.get("skills")
        if isinstance(skills, list):
            result[str(pack_name)] = frozenset(s for s in skills if isinstance(s, str))
    return result


def compute_bump_kind(root: Path, tag: str) -> Literal["minor", "patch"]:
    """Return 'minor' when catalog/skills.json, suites.json, or packs.json changes membership."""
    tag_skills = _skills_membership(_git_json_dict(root, tag, "catalog/skills.json"))
    head_skills = _skills_membership(_worktree_json_dict(root, "catalog/skills.json"))
    if tag_skills != head_skills:
        return "minor"

    tag_suites = _suites_membership(_git_json_dict(root, tag, "catalog/suites.json"))
    head_suites = _suites_membership(_worktree_json_dict(root, "catalog/suites.json"))
    if tag_suites != head_suites:
        return "minor"

    tag_packs = _packs_membership(_git_json_dict(root, tag, "catalog/packs.json"))
    head_packs = _packs_membership(_worktree_json_dict(root, "catalog/packs.json"))
    if tag_packs != head_packs:
        return "minor"

    return "patch"


def advance_semver(version: str, bump_kind: Literal["minor", "patch"]) -> str:
    """Compute the next SemVer version string for a minor or patch increment."""
    major, minor, patch = parse_semver(version)
    if bump_kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _unquote_yaml_value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        return inner.replace("''", "'") if raw[0] == "'" else inner
    return raw


def _parse_frontmatter_description(skill_md: Path) -> str:
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Missing YAML frontmatter delimiter in {skill_md}")
    end_idx = next(
        (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
        None,
    )
    if end_idx is None:
        raise ValueError(f"Unterminated YAML frontmatter in {skill_md}")
    fm_lines = lines[1:end_idx]
    for idx, line in enumerate(fm_lines):
        if not line.startswith("description:"):
            continue
        raw_val = line.partition(":")[2].strip()
        if raw_val not in ("", ">", ">-", "|", "|-"):
            return _unquote_yaml_value(raw_val)
        block: list[str] = []
        for sub in fm_lines[idx + 1 :]:
            if sub.strip() and not sub.startswith((" ", "\t")):
                break
            block.append(sub.strip())
        joiner = "\n" if raw_val.startswith("|") else " "
        return joiner.join(part for part in block if part).strip()
    raise ValueError(f"Missing description field in {skill_md}")


def _load_member_descriptions(root: Path) -> list[tuple[str, str]]:
    suites_doc = _worktree_json_dict(root, "catalog/suites.json")
    if suites_doc is None:
        raise ValueError("Missing or invalid catalog/suites.json")
    suite_map = _suites_membership(suites_doc)
    member_names = sorted({skill for skills in suite_map.values() for skill in skills})

    catalog_descriptions: dict[str, str] = {}
    skills_doc = _worktree_json_dict(root, "catalog/skills.json")
    if skills_doc is not None and isinstance(skills_doc.get("skills"), list):
        for entry in skills_doc["skills"]:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("description"), str)
            ):
                catalog_descriptions[entry["name"]] = entry["description"]

    members: list[tuple[str, str]] = []
    for name in member_names:
        skill_md = root / "skills" / name / "SKILL.md"
        if skill_md.is_symlink() or not skill_md.is_file():
            raise ValueError(f"Listed skill missing entry point file: {name}")
        desc = catalog_descriptions.get(name) or _parse_frontmatter_description(
            skill_md
        )
        members.append((name, desc))
    return members


def _render_pack_plugins(
    root: Path,
    known_skills: set[str],
    version: str,
) -> list[dict[str, object]]:
    packs_doc = _worktree_json_dict(root, "catalog/packs.json")
    if packs_doc is None:
        return []
    raw_packs = packs_doc.get("packs")
    if not isinstance(raw_packs, dict):
        return []
    bundles: list[dict[str, object]] = []
    for pack_name, pack_data in raw_packs.items():
        if not isinstance(pack_data, dict):
            continue
        if pack_name == "fred-skills" or pack_name in known_skills:
            raise ValueError(
                f"Pack name collides with reserved or skill name: {pack_name}"
            )
        raw_skills = pack_data.get("skills", [])
        pack_skills = [s for s in raw_skills if isinstance(s, str)]
        missing = set(pack_skills) - known_skills
        if not pack_skills or missing:
            raise ValueError(
                f"Invalid skill selection in pack '{pack_name}': {sorted(missing)}"
            )
        bundles.append(
            {
                "name": str(pack_name),
                "description": str(pack_data["description"]),
                "version": version,
                "author": dict(_AUTHOR),
                "source": "./",
                "strict": False,
                "skills": [f"./skills/{skill_name}" for skill_name in pack_skills],
            }
        )
    return bundles


def render_versioned_manifests(root: Path, version: str) -> dict[str, str]:
    """Render all 60 Claude/Cursor plugin manifests plus gemini-extension.json."""
    members = _load_member_descriptions(root)
    sorted_names = [name for name, _ in members]
    known_set = set(sorted_names)

    suite_plugin: dict[str, object] = {
        "name": "fred-skills",
        "description": _SUITE_DESCRIPTION,
        "version": version,
        "author": dict(_AUTHOR),
        "source": "./",
        "strict": False,
        "skills": [f"./skills/{name}" for name in sorted_names],
    }
    standalone_plugins: list[dict[str, object]] = [
        {
            "name": name,
            "description": desc,
            "version": version,
            "author": dict(_AUTHOR),
            "source": f"./skills/{name}",
            "skills": ["."],
        }
        for name, desc in members
    ]
    claude_marketplace = {
        "name": "fred-skills",
        "version": version,
        "description": _SUITE_DESCRIPTION,
        "owner": dict(_AUTHOR),
        "metadata": {
            "description": _SUITE_DESCRIPTION,
            "version": version,
        },
        "plugins": [
            suite_plugin,
            *_render_pack_plugins(root, known_set, version),
            *standalone_plugins,
        ],
    }
    cursor_root_plugin: dict[str, object] = {
        "name": "fred-skills",
        "description": _SUITE_DESCRIPTION,
        "version": version,
        "author": dict(_AUTHOR),
        "license": "MIT",
        "skills": [f"./skills/{name}" for name in sorted_names],
    }
    cursor_skill_manifests: dict[str, str] = {
        f"skills/{name}/.cursor-plugin/plugin.json": _json_text(
            {
                "name": name,
                "description": desc,
                "version": version,
                "author": dict(_AUTHOR),
                "license": "MIT",
                "skills": ["."],
            }
        )
        for name, desc in members
    }
    member_cursor_plugins: list[dict[str, object]] = [
        {
            "name": name,
            "source": f"./skills/{name}",
            "description": desc,
            "version": version,
            "author": dict(_AUTHOR),
            "license": "MIT",
        }
        for name, desc in members
    ]
    cursor_plugins: list[dict[str, object]] = [
        {
            "name": "fred-skills",
            "source": "./",
            "description": _SUITE_DESCRIPTION,
            "version": version,
            "author": dict(_AUTHOR),
            "license": "MIT",
        },
        *member_cursor_plugins,
    ]
    cursor_marketplace = {
        "name": "fred-skills",
        "owner": dict(_AUTHOR),
        "metadata": {
            "description": _SUITE_DESCRIPTION,
            "version": version,
        },
        "plugins": cursor_plugins,
    }
    gemini_description = (
        f"{_SUITE_DESCRIPTION} Stage-1 budget note: installing the full extension loads all "
        f"{len(sorted_names)} skills at startup, which can exceed Stage-1 "
        "discovery limits in OpenAI Codex (https://developers.openai.com/codex/skills, "
        "2% context / 8,000-character fallback) and Claude Code "
        "(https://code.claude.com/docs/en/skills, 1% context); prefer "
        "`python scripts/fred.py install --pack starter` (6 skills, <= 1,500 chars) "
        "for bounded discovery."
    )
    gemini_extension = {
        "name": "fred-skills",
        "description": gemini_description,
        "version": version,
        "contextFileName": "AGENTS.md",
    }
    return {
        ".claude-plugin/marketplace.json": _json_text(claude_marketplace),
        ".cursor-plugin/marketplace.json": _json_text(cursor_marketplace),
        ".cursor-plugin/plugin.json": _json_text(cursor_root_plugin),
        **cursor_skill_manifests,
        "gemini-extension.json": _json_text(gemini_extension),
    }


def rewrite_doc_release_versions(content: str, version: str) -> str:
    """Update GitHub Release download URLs, pack archive filenames, and active release-note links."""
    updated = _RELEASE_DOWNLOAD_URL_RE.sub(rf"\g<1>{version}", content)
    updated = _PACK_ARCHIVE_STEM_RE.sub(rf"fred-skills-{version}-\1.\2", updated)
    return _README_RELEASE_NOTES_LINK_RE.sub(
        lambda match: f"[{version}{match.group(1)}{version}{match.group(2)}",
        updated,
    )


def _discover_archived_note_versions(
    root: Path,
    new_version: str,
    restorations: Mapping[str, bytes],
) -> list[str]:
    versions: set[str] = set()
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for note_path in docs_dir.glob("release-notes-*.md"):
            match = _RELEASE_NOTES_FILENAME_RE.match(note_path.name)
            if match is not None and _SEMVER_RE.match(match.group(1)):
                versions.add(match.group(1))
    for rel_path in restorations:
        match = _RELEASE_NOTES_FILENAME_RE.match(Path(rel_path).name)
        if match is not None and _SEMVER_RE.match(match.group(1)):
            versions.add(match.group(1))
    versions.discard(new_version)
    return sorted(versions, key=lambda ver: (parse_semver(ver), ver), reverse=True)


def rewrite_releasing_doc_notes(
    content: str,
    new_version: str,
    archived_versions: list[str],
) -> str:
    """Keep active and archived release-note relative links current in docs/releasing.md."""
    active_link = f"[release-notes-{new_version}.md](release-notes-{new_version}.md)"
    if archived_versions:
        archived_links = ", ".join(
            f"[release-notes-{ver}.md](release-notes-{ver}.md)"
            for ver in archived_versions
        )
        replacement = f"(such as {active_link} and archived {archived_links}"
    else:
        replacement = f"(such as {active_link}"
    return _RELEASING_NOTES_EXAMPLE_RE.sub(lambda _match: replacement, content)


def render_release_notes(root: Path, version: str) -> str:
    """Render docs/release-notes-<version>.md reflecting current HEAD catalog counts."""
    suites_doc = _worktree_json_dict(root, "catalog/suites.json") or {}
    packs_doc = _worktree_json_dict(root, "catalog/packs.json") or {}
    hosts_doc = _worktree_json_dict(root, "catalog/hosts.json") or {}
    cases_doc = _worktree_json_dict(root, "evals/cases.json") or {}

    suite_map = _suites_membership(suites_doc)
    suites_count = len(suite_map)
    total_skills = sum(len(members) for members in suite_map.values())
    workflows_count = len(suite_map.get("repository-workflows", frozenset()))
    raw_packs = packs_doc.get("packs")
    packs_count = len(raw_packs) if isinstance(raw_packs, dict) else 0
    raw_hosts = hosts_doc.get("hosts")
    hosts_count = len(raw_hosts) if isinstance(raw_hosts, dict) else 0
    raw_cases = cases_doc.get("cases")
    cases_count = len(raw_cases) if isinstance(raw_cases, list) else 15

    return (
        f"# {version} — Automated SemVer release and verified skill catalog\n"
        "\n"
        f"Publishes task-based discovery across {suites_count} suites, {packs_count} purpose packs, {hosts_count} explicit host installation\n"
        "locations, managed updates/removal/rollback, bounded run plans, source-freshness checks,\n"
        f"offline reports, issue drafts, comparison, SARIF observations, and {workflows_count} correction workflows.\n"
        f"Original 35 audit package names and audit schema 1.0 remain compatible. Total: {total_skills} skills.\n"
        "\n"
        f"Includes {cases_count} executable evaluation cases, a deterministic harness adapter, and a runnable\n"
        "synthetic original/corrected walkthrough. Fixture scores do not establish LLM quality.\n"
        "Real host behavior and broad model effectiveness remain unverified. See validation.md\n"
        "and implementation-status.md for actual evidence and limits.\n"
        "\n"
        "Update only after inspecting the new source; use installer dry runs and retain receipts\n"
        "and backups. Modified/unmanaged packages are refused. A later missing finding is not\n"
        "proof of a fix. Checksums establish integrity against the included manifest, not identity.\n"
        "\n"
        "This document is release-note source; committing it does not publish a release.\n"
    )


def _list_semver_tags(root: Path) -> list[str]:
    if not _has_git_repo(root):
        return []
    proc = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:strip=2)", "refs/tags/v*"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    tags: list[str] = []
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if raw.startswith("v") and _SEMVER_RE.match(raw[1:]):
            tags.append(raw[1:])
    return sorted(tags)


def _collect_tagged_note_restorations(root: Path) -> dict[str, bytes]:
    restorations: dict[str, bytes] = {}
    for tag_ver in _list_semver_tags(root):
        rel_path = f"docs/release-notes-{tag_ver}.md"
        tagged_bytes = _git_blob_bytes(root, f"refs/tags/v{tag_ver}:{rel_path}")
        if tagged_bytes is None:
            continue
        target = root / rel_path
        if (
            target.is_symlink()
            or not target.is_file()
            or target.read_bytes() != tagged_bytes
        ):
            restorations[rel_path] = tagged_bytes
    return restorations


def _apply_version_updates(
    root: Path,
    new_version: str,
    *,
    dry_run: bool,
) -> list[str]:
    text_updates: dict[str, str] = {"VERSION": f"{new_version}\n"}
    text_updates.update(render_versioned_manifests(root, new_version))

    for doc_rel in ("README.md", "docs/usage.md"):
        doc_path = root / doc_rel
        if doc_path.is_file():
            text_updates[doc_rel] = rewrite_doc_release_versions(
                doc_path.read_text(encoding="utf-8"),
                new_version,
            )

    notes_rel = f"docs/release-notes-{new_version}.md"
    text_updates[notes_rel] = render_release_notes(root, new_version)
    byte_restorations = _collect_tagged_note_restorations(root)

    explorer_path = root / "docs/explorer.html"
    if explorer_path.is_file():
        text_updates["docs/explorer.html"] = _EXPLORER_RELEASE_NOTES_HREF_RE.sub(
            lambda match: f"{match.group(1)}{new_version}{match.group(2)}",
            explorer_path.read_text(encoding="utf-8"),
        )

    releasing_path = root / "docs/releasing.md"
    if releasing_path.is_file():
        archived_versions = _discover_archived_note_versions(
            root,
            new_version,
            byte_restorations,
        )
        text_updates["docs/releasing.md"] = rewrite_releasing_doc_notes(
            releasing_path.read_text(encoding="utf-8"),
            new_version,
            archived_versions,
        )

    modified: list[str] = []
    for rel_path, new_text in sorted(text_updates.items()):
        target = root / rel_path
        new_bytes = new_text.encode("utf-8")
        if (
            target.is_symlink()
            or not target.is_file()
            or target.read_bytes() != new_bytes
        ):
            modified.append(rel_path)
            if not dry_run:
                _write_lf_text(target, new_text)

    for rel_path, raw_bytes in sorted(byte_restorations.items()):
        target = root / rel_path
        modified.append(rel_path)
        if not dry_run:
            if target.is_symlink():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw_bytes)

    return sorted(set(modified))


def bump_repository_version(
    root: Path = ROOT,
    *,
    dry_run: bool = False,
) -> BumpResult:
    """Evaluate distributable changes since refs/tags/v<VERSION> and synchronize files."""
    version_path = root / "VERSION"
    if not version_path.is_file():
        raise FileNotFoundError(f"Missing VERSION file at {version_path}")
    current_version = version_path.read_text(encoding="utf-8").strip()
    parse_semver(current_version)
    tag = f"v{current_version}"

    if not _tag_exists(root, tag):
        return BumpResult(
            bumped=False,
            previous_version=current_version,
            new_version=current_version,
            bump_kind="none",
            reason=f"refs/tags/{tag} does not exist yet; {current_version} is already pending release",
            changed_distributable_files=(),
            updated_files=(),
        )

    changed_files = detect_distributable_changes(root, tag)
    if not changed_files:
        return BumpResult(
            bumped=False,
            previous_version=current_version,
            new_version=current_version,
            bump_kind="none",
            reason=f"no distributable changes under {', '.join(DISTRIBUTABLE_PATHS)} since refs/tags/{tag}",
            changed_distributable_files=(),
            updated_files=(),
        )

    bump_kind = compute_bump_kind(root, tag)
    new_version = advance_semver(current_version, bump_kind)
    updated_files = _apply_version_updates(root, new_version, dry_run=dry_run)
    return BumpResult(
        bumped=True,
        previous_version=current_version,
        new_version=new_version,
        bump_kind=bump_kind,
        reason=f"{bump_kind} bump triggered by {len(changed_files)} distributable file(s) since refs/tags/{tag}",
        changed_distributable_files=tuple(changed_files),
        updated_files=tuple(updated_files),
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for bump_version.py."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory (defaults to parent of scripts/).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate SemVer bump without modifying files on disk.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON summary to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = bump_repository_version(args.root.resolve(), dry_run=args.dry_run)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif result.bumped:
        mode = "Would bump" if args.dry_run else "Bumped"
        print(
            f"{mode} VERSION {result.previous_version} -> {result.new_version} "
            f"({result.bump_kind}; {len(result.updated_files)} files updated)"
        )
    else:
        print(f"No version bump needed ({result.previous_version}): {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

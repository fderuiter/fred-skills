"""Stdlib-only documentation count invariants and git-tag helpers."""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from skill_tools import (
    FredCliError,
    git_blob_bytes,
    git_tag_exists,
    read_utf8_text,
    run_local_git,
)

__all__ = [
    "CORE_INVARIANT_DOCS",
    "HOST_LAYOUTS_RE",
    "PROSE_NUMBER_WORDS",
    "RELEASE_HOST_COUNT_RE",
    "RELEASE_PACK_COUNT_RE",
    "RELEASE_SUITE_COUNT_RE",
    "RELEASE_TOTAL_SKILLS_RE",
    "DocCountExpectations",
    "ProseCountMismatch",
    "build_prose_count_rules",
    "check_single_tagged_release_note",
    "discover_untagged_release_notes",
    "find_prose_count_mismatches",
    "git_tag_exists",
    "list_local_v_tag_versions",
    "parse_prose_int",
    "validate_historical_release_notes_immutability",
]

PROSE_NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

CORE_INVARIANT_DOCS: tuple[str, ...] = (
    "README.md",
    "docs/usage.md",
    "docs/architecture.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "docs/validation.md",
    "docs/implementation-status.md",
    "docs/research.md",
    "evals/README.md",
)

RELEASE_HOST_COUNT_RE = re.compile(
    r"\b([A-Za-z0-9]+)\s+explicit\s+host\s+installation\s+locations\b",
    re.IGNORECASE,
)
RELEASE_SUITE_COUNT_RE = re.compile(r"\b([A-Za-z0-9]+)\s+suites\b", re.IGNORECASE)
RELEASE_PACK_COUNT_RE = re.compile(
    r"\b([A-Za-z0-9]+)\s+(?:purpose\s+)?packs\b",
    re.IGNORECASE,
)
RELEASE_TOTAL_SKILLS_RE = re.compile(
    r"\bTotal:\s*([A-Za-z0-9]+)\s+skills\b",
    re.IGNORECASE,
)
HOST_LAYOUTS_RE = re.compile(r"\b([A-Za-z0-9]+)\s+host\s+layouts\b", re.IGNORECASE)


@dataclass(frozen=True)
class DocCountExpectations:
    """Expected catalog counts used to build shared prose count rules."""

    total_skills: int | None = None
    starter_skills: int | None = None
    suites_count: int | None = None
    packs_count: int | None = None
    hosts_count: int | None = None
    deliverables_count: int | None = None
    audit_skills: int | None = None
    audit_domains: int | None = None
    workflow_skills: int | None = None
    engineering_skills: int | None = None


@dataclass(frozen=True)
class ProseCountMismatch:
    """Structured prose count mismatch with 1-based line number."""

    line_no: int
    label: str
    found: int
    expected: int
    snippet: str


def parse_prose_int(raw: str) -> int | None:
    """Parse an integer written as ASCII digits or an English number word."""
    token = raw.strip().lower()
    if token.isdigit():
        return int(token)
    return PROSE_NUMBER_WORDS.get(token)


def discover_untagged_release_notes(
    root: Path,
    *,
    fallback_root: Path | None = None,
) -> list[str]:
    """Return ['docs/release-notes-<VERSION>.md'] when VERSION is untagged and present."""
    version_path = root / "VERSION"
    if not version_path.exists() and not version_path.is_symlink():
        if fallback_root is None:
            return []
        fb_path = fallback_root / "VERSION"
        if not fb_path.exists() and not fb_path.is_symlink():
            return []
        version_path = fb_path
    raw_ver = read_utf8_text(version_path, label="VERSION file", optional=True)
    ver = raw_ver.strip() if raw_ver is not None else ""
    if not ver or git_tag_exists(root, f"v{ver}"):
        return []
    candidate = f"docs/release-notes-{ver}.md"
    cand_path = root / candidate
    return [candidate] if (cand_path.exists() or cand_path.is_symlink()) else []


def _read_active_version(root: Path) -> str:
    version_path = root / "VERSION"
    raw_ver = read_utf8_text(version_path, label="VERSION file", optional=True)
    return raw_ver.strip() if raw_ver is not None else ""


def list_local_v_tag_versions(root: Path) -> set[str]:
    """Return local SemVer tag versions (stripped of leading 'v') under refs/tags/v*."""
    if not (root / ".git").exists() or not shutil.which("git"):
        return set()
    proc = run_local_git(
        root,
        "for-each-ref",
        "--format=%(refname:strip=2)",
        "refs/tags/v*",
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return {
        raw[1:]
        for line in proc.stdout.splitlines()
        if (raw := line.strip()).startswith("v") and len(raw) > 1
    }


def check_single_tagged_release_note(root: Path, tag_ver: str) -> list[str]:
    """Verify docs/release-notes-<tag_ver>.md matches refs/tags/v<tag_ver> byte-for-byte."""
    rel_path = f"docs/release-notes-{tag_ver}.md"
    doc_path = root / rel_path
    blob_bytes = git_blob_bytes(root, f"refs/tags/v{tag_ver}:{rel_path}")
    if blob_bytes is None:
        if doc_path.exists() or doc_path.is_symlink():
            return [
                (
                    f"{rel_path}: tagged release notes immutability violation; "
                    f"file did not exist at refs/tags/v{tag_ver}"
                )
            ]
        return []
    if doc_path.is_symlink() or not doc_path.is_file():
        return [
            (
                f"{rel_path}: tagged release notes immutability violation; "
                f"file missing or symlink after refs/tags/v{tag_ver}"
            )
        ]
    try:
        actual_bytes = doc_path.read_bytes()
    except OSError as exc:
        raise FredCliError(
            f"Unable to read release notes '{doc_path}': {exc}",
            code="io_error",
            hint=f"Check file permissions and readability for '{doc_path}'.",
        ) from exc
    if actual_bytes != blob_bytes:
        return [
            (
                f"{rel_path}: tagged release notes immutability violation; "
                f"content must remain byte-identical to refs/tags/v{tag_ver}:{rel_path}"
            )
        ]
    return []


def _discover_release_note_files(
    root: Path,
    target_rel_paths: set[str] | None,
) -> list[tuple[str, str]]:
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return []
    discovered: list[tuple[str, str]] = []
    for note_path in sorted(docs_dir.glob("release-notes-*.md")):
        ver = note_path.name.removeprefix("release-notes-").removesuffix(".md")
        rel_path = f"docs/{note_path.name}"
        if not ver:
            continue
        if target_rel_paths is not None and rel_path not in target_rel_paths:
            continue
        discovered.append((ver, rel_path))
    return discovered


def validate_historical_release_notes_immutability(
    root: Path,
    *,
    target_rel_paths: set[str] | None = None,
) -> list[str]:
    """Fail closed when .git exists and historical release notes lack local tags or drift."""
    if not (root / ".git").exists():
        return []
    active_ver = _read_active_version(root)
    errors: list[str] = []
    tagged = list_local_v_tag_versions(root)
    for ver, rel_path in _discover_release_note_files(root, target_rel_paths):
        if git_tag_exists(root, f"v{ver}"):
            tagged.add(ver)
        elif ver != active_ver:
            errors.append(
                f"{rel_path}: tagged release notes immutability violation; "
                f"missing local tag refs/tags/v{ver} for historical release notes"
            )
    for tag_ver in sorted(tagged):
        rel_path = f"docs/release-notes-{tag_ver}.md"
        if target_rel_paths is not None and rel_path not in target_rel_paths:
            continue
        errors.extend(check_single_tagged_release_note(root, tag_ver))
    return errors


def build_prose_count_rules(
    exp: DocCountExpectations,
) -> tuple[tuple[re.Pattern[str], str, int | None], ...]:
    """Build shared prose count patterns and expected counts for documentation checks."""
    scenarios_total = exp.total_skills * 3 if exp.total_skills is not None else None
    paired_runs_total = exp.total_skills * 6 if exp.total_skills is not None else None
    return (
        (
            re.compile(r"\*\*([A-Za-z0-9]+)\s+standalone\s+skills\*\*", re.IGNORECASE),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"`complete`\s+for\s+all\s+([A-Za-z0-9]+)\s+(?:skills|packages)",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(r"\beach\s+of\s+the\s+([A-Za-z0-9]+)\s+skills\b", re.IGNORECASE),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"\bcomplete\s+([A-Za-z0-9]+)-package\s+pack\b",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"\bcreates\s+all\s+([A-Za-z0-9]+)\s+skill\s+directories\b",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (RELEASE_TOTAL_SKILLS_RE, "skill", exp.total_skills),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)\s+total\s+(?:packages|skills)\b",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"\bacross\s+(?:the|all)\s+([A-Za-z0-9]+)\s+skills\b",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"\bhosts?(?:\s+adapters)?,\s+and\s+([A-Za-z0-9]+)\s+skills\b",
                re.IGNORECASE,
            ),
            "skill",
            exp.total_skills,
        ),
        (
            re.compile(
                r"`starter`\s+for\s+([A-Za-z0-9]+)\s+core\s+audit\s+skills",
                re.IGNORECASE,
            ),
            "starter pack",
            exp.starter_skills,
        ),
        (
            re.compile(r"\b([A-Za-z0-9]+)\s+(?:`shared/`\s+)?suites\b", re.IGNORECASE),
            "suite",
            exp.suites_count,
        ),
        (RELEASE_PACK_COUNT_RE, "pack", exp.packs_count),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)\s+(?:explicit\s+)?(?:cataloged\s+)?(?:agent\s+)?hosts?(?:\s+(?:layouts|adapters|installation\s+locations))?\b",
                re.IGNORECASE,
            ),
            "host",
            exp.hosts_count,
        ),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)\s+structured\s+JSON\s+deliverables\b",
                re.IGNORECASE,
            ),
            "JSON deliverable",
            exp.deliverables_count,
        ),
        (
            re.compile(r"\b([A-Za-z0-9]+)-package\s+audit\s+family\b", re.IGNORECASE),
            "audit suite",
            exp.audit_skills,
        ),
        (
            re.compile(r"\ball\s+([A-Za-z0-9]+)\s+specialists\b", re.IGNORECASE),
            "audit specialist",
            exp.audit_domains,
        ),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)-package\s+workflow\s+family\b",
                re.IGNORECASE,
            ),
            "workflow suite",
            exp.workflow_skills,
        ),
        (
            re.compile(r"\b([A-Za-z0-9]+)\s+correction\s+workflows\b", re.IGNORECASE),
            "workflow suite",
            exp.workflow_skills,
        ),
        (
            re.compile(
                r"\|\s*Verification\s+lifecycle\s*\|\s*([A-Za-z0-9]+)\s+skills\b",
                re.IGNORECASE,
            ),
            "workflow suite",
            exp.workflow_skills,
        ),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)\s+engineering\s+suite\s+packages\b",
                re.IGNORECASE,
            ),
            "engineering suite",
            exp.engineering_skills,
        ),
        (
            re.compile(
                r"\b(?:These|The|all)\s+([A-Za-z0-9]+)\s+(?:skill\s+)?(?:scenario\s+)?specifications\b",
                re.IGNORECASE,
            ),
            "scenario specification",
            scenarios_total,
        ),
        (
            re.compile(
                r"\bnot\s+([A-Za-z0-9]+)\s+(?:executed|completed\s+model)\s+evaluations\b",
                re.IGNORECASE,
            ),
            "evaluation scenario",
            scenarios_total,
        ),
        (
            re.compile(
                r"\b([A-Za-z0-9]+)\s+raw\s+cases\b",
                re.IGNORECASE,
            ),
            "evaluation case",
            scenarios_total,
        ),
        (
            re.compile(
                r"in\s+baseline\s+and\s+with-skill\s+configurations\s+\(([A-Za-z0-9]+)\s+runs\)",
                re.IGNORECASE,
            ),
            "evaluation run",
            paired_runs_total,
        ),
    )


def find_prose_count_mismatches(
    text: str,
    rules: Sequence[tuple[re.Pattern[str], str, int | None]],
) -> list[ProseCountMismatch]:
    """Scan documentation prose for count mismatches and return 1-based line records."""
    mismatches: list[ProseCountMismatch] = []
    for pattern, label, expected in rules:
        if expected is None:
            continue
        for match in pattern.finditer(text):
            found = parse_prose_int(match.group(1))
            if found is not None and found != expected:
                line_no = text.count("\n", 0, match.start()) + 1
                snippet = " ".join(match.group(0).split())
                mismatches.append(
                    ProseCountMismatch(
                        line_no=line_no,
                        label=label,
                        found=found,
                        expected=expected,
                        snippet=snippet,
                    )
                )
    return mismatches

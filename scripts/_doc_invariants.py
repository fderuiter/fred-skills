"""Stdlib-only documentation count invariants and git-tag helpers."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from skill_tools import git_tag_exists

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
    "discover_untagged_release_notes",
    "find_prose_count_mismatches",
    "git_tag_exists",
    "parse_prose_int",
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
    if not version_path.is_file():
        if fallback_root is None or not (fallback_root / "VERSION").is_file():
            return []
        version_path = fallback_root / "VERSION"
    ver = version_path.read_text(encoding="utf-8").strip()
    if not ver or git_tag_exists(root, f"v{ver}"):
        return []
    candidate = f"docs/release-notes-{ver}.md"
    return [candidate] if (root / candidate).is_file() else []


def build_prose_count_rules(
    exp: DocCountExpectations,
) -> tuple[tuple[re.Pattern[str], str, int | None], ...]:
    """Build shared prose count patterns and expected counts for documentation checks."""
    scenarios_total = exp.total_skills * 3 if exp.total_skills is not None else None
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
            re.compile(r"\bacross\s+the\s+([A-Za-z0-9]+)\s+skills\b", re.IGNORECASE),
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
                r"\b(?:These|all)\s+([A-Za-z0-9]+)\s+(?:scenario\s+)?specifications\b",
                re.IGNORECASE,
            ),
            "scenario specification",
            scenarios_total,
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

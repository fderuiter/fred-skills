#!/usr/bin/env python3
"""Triage failed audit runs, scrub secrets, and export diagnostic bundles."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import platform
import re
import shlex
import sys
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, NamedTuple, NoReturn

REDACTION_TIERS: tuple[str, ...] = ("skeleton", "evidence", "full")
OMITTED_TOOL_OUTPUT_PLACEHOLDER = "[OMITTED_TOOL_OUTPUT]"

# Specific secret key names for recursive JSON and key-value scrubbing.
_SENSITIVE_KEY_EXACT = frozenset(
    {
        "api_key",
        "apikey",
        "x_api_key",
        "secret",
        "client_secret",
        "app_secret",
        "webhook_secret",
        "signing_secret",
        "secret_key",
        "aws_secret_access_key",
        "access_key_secret",
        "private_key",
        "password",
        "passwd",
        "pwd",
        "db_password",
        "database_password",
        "redis_password",
        "token",
        "auth_token",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "bearer_token",
        "github_token",
        "gitlab_token",
        "slack_token",
        "npm_token",
        "pypi_token",
        "authorization",
        "proxy_authorization",
        "x_auth_token",
        "cookie",
        "set_cookie",
    }
)

_NON_SECRET_TOKEN_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "tokens",
        "token_count",
        "token_limit",
        "context_tokens",
    }
)

_SECRET_KEY_REGEX_PART = (
    r"(?:[a-zA-Z0-9_-]*?(?:api[_-]?key|apikey|secret|password|passwd|pwd|private[_-]?key)"
    r"|(?:auth[_-]?|access[_-]?|refresh[_-]?|session[_-]?|bearer[_-]?|id[_-]?|client[_-]?"
    r"|github[_-]?|gitlab[_-]?|slack[_-]?|npm[_-]?|pypi[_-]?|x[_-]?auth[_-]?)?token)"
)

# Redaction patterns for credential scrubbing: (compiled_regex, replacement, category)
CREDENTIAL_PATTERNS_CATEGORIZED: list[tuple[re.Pattern[str], str, str]] = [
    # Authorization header: Authorization: Bearer / Basic / Token ...
    (
        re.compile(
            r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)(?!\[REDACTED_)([^\s\r\n'\"\\]+)"
        ),
        r"\1[REDACTED_AUTH_TOKEN]",
        "auth_token",
    ),
    (
        re.compile(r"(?i)\b(bearer\s+)(?!\[REDACTED_)([a-zA-Z0-9_\-\.]{16,})\b"),
        r"\1[REDACTED_BEARER_TOKEN]",
        "bearer_token",
    ),
    # URI credentials: scheme://user:password@host or scheme://:password@host
    (
        re.compile(
            r"\b([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s\"'\\?#@:]*:)(?!\[REDACTED_)([^/\s\"'\\?#@]+)(@)"
        ),
        r"\1[REDACTED_URI_PASSWORD]\3",
        "uri_password",
    ),
    # OpenAI / Anthropic / general sk- API keys
    (
        re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"),
        "[REDACTED_API_KEY]",
        "api_key",
    ),
    # GitHub personal access tokens
    (
        re.compile(r"\b(gh[pousr]_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{30,})\b"),
        "[REDACTED_GITHUB_TOKEN]",
        "github_token",
    ),
    # GitLab / npm / PyPI / Google API tokens
    (
        re.compile(
            r"\b(glpat-[a-zA-Z0-9_\-]{20,}|npm_[a-zA-Z0-9]{36}|pypi-[a-zA-Z0-9_\-]{32,}|AIza[0-9A-Za-z_\-]{35})\b"
        ),
        "[REDACTED_API_KEY]",
        "api_key",
    ),
    # Slack tokens
    (
        re.compile(r"\b(xox[baprs]-[a-zA-Z0-9_\-]{10,})\b"),
        "[REDACTED_SLACK_TOKEN]",
        "slack_token",
    ),
    # AWS Access Key ID
    (
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        "[REDACTED_AWS_KEY]",
        "aws_key",
    ),
    # JWT tokens (3 base64url segments separated by dots)
    (
        re.compile(
            r"\b(eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,})\b"
        ),
        "[REDACTED_JWT_TOKEN]",
        "jwt_token",
    ),
    # Private Key blocks (including literal \n in JSON-encoded strings)
    (
        re.compile(
            r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
        "private_key",
    ),
    # Escaped JSON key-value secrets: \"api_key\": \"...\"
    (
        re.compile(
            rf"(?i)(\\[\"']{_SECRET_KEY_REGEX_PART}\\[\"']\s*:\s*\\[\"'])(?!\[REDACTED_)([^\"'\\]+)(\\[\"'])"
        ),
        r"\1[REDACTED_SECRET]\3",
        "json_secret",
    ),
    # Quoted JSON key-value secrets: "api_key": "..." or 'password': '...'
    (
        re.compile(
            rf"(?i)(([\"']){_SECRET_KEY_REGEX_PART}\2\s*:\s*([\"']))(?!\[REDACTED_)([^\"'\\]*(?:\\.[^\"'\\]*)*)\3"
        ),
        r"\1[REDACTED_SECRET]\3",
        "json_secret",
    ),
    # Generic key-value secrets (quoted: password="...", api_key='...', secret: "...")
    (
        re.compile(
            rf"(?i)\b({_SECRET_KEY_REGEX_PART}\s*[:=]\s*(['\"]))(?!\[REDACTED_)(.*?)\2"
        ),
        r"\1[REDACTED_SECRET]\2",
        "secret",
    ),
    # Generic key-value secrets (unquoted: password=..., token: ..., excluding numeric token counts)
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd|auth[_-]?token|access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*)(?!\[REDACTED_)(?![0-9]+\b)(?![a-zA-Z0-9_-]+\s*=)(?![\{\[\"\'])([^\s'\",;&\\\r\n\}]+)"
        ),
        r"\1[REDACTED_SECRET]",
        "secret",
    ),
]

# Signatures for failure detection
TIMEOUT_PATTERNS = [
    re.compile(
        r"(?i)(?:command|process|tool|execution|query)\s+(?:timed out|has timed out)"
    ),
    re.compile(r"(?i)timed out after \d+(?:\.\d+)?\s*(?:seconds|s|sec)"),
    re.compile(r"\bTimeoutError\b:?"),
    re.compile(r"(?i)\bdeadline exceeded\b"),
    re.compile(r"(?i)\bsubprocess\.TimeoutExpired\b"),
    re.compile(r"(?i)execution timed out after"),
]

SCHEMA_PATTERNS = [
    re.compile(r"\bjsonschema(?:\.exceptions)?\.ValidationError\b"),
    re.compile(r"'[A-Za-z0-9_.-]+' is a required property"),
    re.compile(r"is not one of \[[^\r\n]+\]"),
    re.compile(r"'1\.0' was expected"),
    re.compile(
        r"Additional properties are not allowed \([^\n)]*w(?:as|ere) unexpected\)"
    ),
    re.compile(r"(?i)\bInvalid (?:architecture|review|testing)(?:\.json| artifact)\b"),
    re.compile(r"(?i)\bschema mismatch\b"),
    re.compile(r"(?i)\bschema validation (?:failed|error)\b"),
    re.compile(r"(?i)is not valid under any of the given schemas"),
    re.compile(
        r"Duplicate IDs in (?:evidence|contracts|findings|issues|decisions|test_cases):"
    ),
    re.compile(r"Reversed lines:"),
    re.compile(
        r"(?:Evidence|Finding file|Deliverable) path must be repository-relative"
    ),
    re.compile(r"Unsafe relative path:"),
    re.compile(r"Expected a nonempty repository-relative POSIX path"),
    re.compile(r"Unknown (?:evidence|contracts|findings|issues) reference"),
    re.compile(r"Actionable defect lacks contract/evidence:"),
    re.compile(r"Confirmed finding lacks executed command:"),
    re.compile(r"Refuted candidate must be info:"),
    re.compile(r"Issue dependency cycle:"),
]

CONTEXT_PATTERNS = [
    re.compile(r"(?i)\bcontext_length_exceeded\b"),
    re.compile(r"(?i)maximum context length is \d+ tokens"),
    re.compile(r"(?i)prompt is too long"),
    re.compile(r"(?i)prompt exceeds context window"),
    re.compile(r"(?i)token limit exceeded"),
    re.compile(r"(?i)context window (?:limit|exceeded)"),
    re.compile(r"(?i)\bmodel_context_window_exceeded\b"),
]

# Hyphen-safe general error token matcher (ignores 'error' inside 'repo-audit-error-semantics')
GENERAL_ERROR_RE = re.compile(
    r"(?i)(?<![a-zA-Z0-9_-])(?:fatal|traceback|error|exception)(?![a-zA-Z0-9_-])"
)

_DEFAULT_SUITE_SKILLS: dict[str, tuple[str, ...]] = {
    "repository-audits": (
        "repo-audit-program",
        "repo-audit-diagnostics",
        "repo-audit-behavioral-contracts",
        "repo-audit-invariants",
        "repo-audit-state-machines",
        "repo-audit-concurrency",
        "repo-audit-lifecycle",
        "repo-audit-temporal-coupling",
        "repo-audit-domain-models",
        "repo-audit-failure-modes",
        "repo-audit-trust-boundaries",
        "repo-audit-agent-configs",
        "repo-audit-transactions",
        "repo-audit-idempotency",
        "repo-audit-error-semantics",
        "repo-audit-capacity",
        "repo-audit-cancellation",
        "repo-audit-cache-coherence",
        "repo-audit-data-integrity",
        "repo-audit-boundary-conditions",
        "repo-audit-protocol-drift",
        "repo-audit-configuration",
        "repo-audit-compatibility",
        "repo-audit-complexity",
        "repo-audit-canonicalization",
        "repo-audit-side-effects",
        "repo-audit-sources-of-truth",
        "repo-audit-architecture",
        "repo-audit-test-contracts",
        "repo-audit-cohesion",
        "repo-audit-observability",
        "repo-audit-nondeterminism",
        "repo-audit-test-oracles",
        "repo-audit-api-consistency",
        "repo-audit-accessibility-state",
        "repo-audit-dead-code",
        "repo-audit-synthesis",
    ),
    "repository-workflows": (
        "repo-verify-finding",
        "repo-specify-regression",
        "repo-plan-fix",
        "repo-apply-fix",
        "repo-verify-fix",
        "repo-reconcile-findings",
        "session-handoff",
        "terse-output-commit",
    ),
    "software-architecture": (
        "arch-decision-record",
        "arch-boundary-specification",
        "arch-component-decomposition",
    ),
    "code-review": (
        "review-diff-analysis",
        "review-invariant-violations",
        "review-actionable-feedback",
    ),
    "automated-testing": (
        "test-gap-analysis",
        "test-case-generation",
        "test-suite-verification",
    ),
    "spec-driven-development": (
        "spec-interview",
        "spec-authoring",
        "spec-convergence",
    ),
}

_DEFAULT_SUITE_ARTIFACTS: dict[str, tuple[str, str]] = {
    "repository-audits": (
        "audit.json",
        "shared/repo-audit/resources/assets/audit.schema.json",
    ),
    "repository-workflows": (
        "audit.json",
        "shared/repo-audit/resources/assets/audit.schema.json",
    ),
    "software-architecture": (
        "architecture.json",
        "shared/software-architecture/resources/assets/architecture.schema.json",
    ),
    "code-review": (
        "review.json",
        "shared/code-review/resources/assets/review.schema.json",
    ),
    "automated-testing": (
        "testing.json",
        "shared/automated-testing/resources/assets/testing.schema.json",
    ),
    "spec-driven-development": (
        "spec.json",
        "shared/spec-driven-development/resources/assets/spec.schema.json",
    ),
}

_DEFAULT_TASK_TO_SUITE: dict[str, str] = {
    "review-pr": "repository-audits",
    "investigate-bug": "repository-audits",
    "release-ready": "repository-audits",
    "understand-repo": "repository-audits",
    "plan-findings": "repository-audits",
    "verify-fix": "repository-workflows",
    "review-ai-code": "repository-audits",
    "upgrade-dependency": "repository-audits",
    "database-migration": "repository-audits",
    "flaky-tests": "repository-audits",
    "api-change": "repository-audits",
    "docs-drift": "repository-audits",
    "apply-fix": "repository-workflows",
    "recover-audit": "repository-audits",
    "design-architecture": "software-architecture",
    "review-code-changes": "code-review",
    "expand-automated-tests": "automated-testing",
    "specify-feature": "spec-driven-development",
}

_DOMAIN_KIND_KEY_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("remediation", frozenset({"records"})),
    (
        "reconciliation",
        frozenset(
            {
                "finding_fingerprint_matches",
                "fingerprint_matches",
                "remediation_state_transitions",
                "state_transitions",
                "unmapped_records",
            }
        ),
    ),
    (
        "finding-verification",
        frozenset(
            {
                "reproduction_status",
                "safe_control_outcomes",
                "confidence_rating",
            }
        ),
    ),
    (
        "regression",
        frozenset(
            {
                "failure_boundaries",
                "failure_boundary",
                "test_command_traces",
                "assertion_expectations",
            }
        ),
    ),
    (
        "plan",
        frozenset(
            {
                "task_breakdowns",
                "dependency_order",
                "file_scopes",
                "plan_id",
            }
        ),
    ),
    (
        "fix",
        frozenset(
            {
                "file_changes",
                "diff_summaries",
                "diff_summary",
                "execution_results",
                "compatibility_limits",
            }
        ),
    ),
    (
        "verification",
        frozenset(
            {
                "original_reproduced",
                "regression_passed",
                "before_revision",
                "after_revision",
                "safe_controls_passed",
            }
        ),
    ),
    ("review", frozenset({"verdict"})),
    ("testing", frozenset({"test_cases"})),
    (
        "spec",
        frozenset(
            {
                "interview_decisions",
                "new_file_scopes",
                "out_of_scope",
                "verification_commands",
            }
        ),
    ),
    ("architecture", frozenset({"boundaries", "decisions"})),
    ("run", frozenset({"plan", "snapshot", "authority", "checkpoints"})),
)

_DOMAIN_KIND_TO_SUITE: dict[str, str] = {
    "architecture": "software-architecture",
    "review": "code-review",
    "testing": "automated-testing",
    "spec": "spec-driven-development",
    "remediation": "repository-workflows",
    "reconciliation": "repository-workflows",
    "finding-verification": "repository-workflows",
    "regression": "repository-workflows",
    "plan": "repository-workflows",
    "fix": "repository-workflows",
    "verification": "repository-workflows",
}

_CATALOG_DIR = Path(__file__).resolve().parents[3] / "catalog"


def _suite_for_skill_list(
    skills: Iterable[str], skill_to_suite: Mapping[str, str]
) -> str:
    suites = list(
        dict.fromkeys(skill_to_suite[s] for s in skills if s in skill_to_suite)
    )
    if "repository-audits" in suites:
        return "repository-audits"
    return suites[0] if suites else "repository-audits"


def _phase_sort_key(item: tuple[int, Any]) -> tuple[int, int]:
    idx, phase = item
    if isinstance(phase, dict) and isinstance(phase.get("id"), int):
        return (0, phase["id"])
    return (1, idx)


def _append_unique_str_skills(members: list[str], raw_skills: object) -> None:
    if not isinstance(raw_skills, list):
        return
    for sk in raw_skills:
        if isinstance(sk, str) and sk and sk not in members:
            members.append(sk)


def _extract_suite_phase_members(phases: object) -> tuple[str, ...] | None:
    if not isinstance(phases, list):
        return None
    members: list[str] = []
    for _, phase in sorted(enumerate(phases), key=_phase_sort_key):
        if isinstance(phase, dict):
            _append_unique_str_skills(members, phase.get("skills", []))
    return tuple(members)


def _extract_suite_artifact_pair(artifact: object) -> tuple[str, str] | None:
    if not isinstance(artifact, dict):
        return None
    filename = artifact.get("filename")
    schema = artifact.get("schema")
    if isinstance(filename, str) and isinstance(schema, str):
        return (filename, schema)
    return None


def _apply_suite_entry(
    suite: object,
    suite_skills: dict[str, tuple[str, ...]],
    suite_artifacts: dict[str, tuple[str, str]],
) -> None:
    if not isinstance(suite, dict):
        return
    sname = suite.get("name")
    if not isinstance(sname, str) or not sname:
        return
    artifact_pair = _extract_suite_artifact_pair(suite.get("artifact"))
    if artifact_pair is not None:
        suite_artifacts[sname] = artifact_pair
    phase_members = _extract_suite_phase_members(suite.get("phases"))
    if phase_members is not None:
        suite_skills[sname] = phase_members


def _parse_suites_doc(
    suites_doc: object,
    suite_skills: dict[str, tuple[str, ...]],
    suite_artifacts: dict[str, tuple[str, str]],
) -> None:
    if not isinstance(suites_doc, dict):
        return
    suites = suites_doc.get("suites")
    if not isinstance(suites, list):
        return
    for suite in suites:
        _apply_suite_entry(suite, suite_skills, suite_artifacts)


def _resolve_recipe_suite(
    robj: dict[str, Any], skill_to_suite: Mapping[str, str]
) -> str:
    explicit_suite = robj.get("suite")
    if isinstance(explicit_suite, str) and explicit_suite:
        return explicit_suite
    raw_skills = robj.get("skills", [])
    str_skills = (
        [s for s in raw_skills if isinstance(s, str)]
        if isinstance(raw_skills, list)
        else []
    )
    return _suite_for_skill_list(str_skills, skill_to_suite)


def _apply_recipe_entry(
    rname: object,
    robj: object,
    skill_to_suite: Mapping[str, str],
    live_tasks: dict[str, str],
) -> None:
    if not isinstance(rname, str) or not isinstance(robj, dict):
        return
    rsuite = _resolve_recipe_suite(robj, skill_to_suite)
    live_tasks[rname] = rsuite
    raw_aliases = robj.get("aliases", [])
    if not isinstance(raw_aliases, list):
        return
    for alias in raw_aliases:
        if isinstance(alias, str) and alias and alias not in live_tasks:
            live_tasks[alias] = rsuite


def _apply_top_recipe_aliases(top_aliases: object, live_tasks: dict[str, str]) -> None:
    if not isinstance(top_aliases, dict):
        return
    for alias, target in top_aliases.items():
        if not isinstance(alias, str) or not alias or alias in live_tasks:
            continue
        if isinstance(target, str) and target in live_tasks:
            live_tasks[alias] = live_tasks[target]


def _parse_recipes_doc(
    recipes_doc: object,
    skill_to_suite: Mapping[str, str],
    task_to_suite: dict[str, str],
) -> None:
    if not isinstance(recipes_doc, dict):
        return
    recipes = recipes_doc.get("recipes")
    if not isinstance(recipes, dict):
        return
    live_tasks: dict[str, str] = {}
    for rname, robj in recipes.items():
        _apply_recipe_entry(rname, robj, skill_to_suite, live_tasks)
    _apply_top_recipe_aliases(recipes_doc.get("aliases"), live_tasks)
    if live_tasks:
        task_to_suite.update(live_tasks)


def _load_catalog_metadata(
    catalog_dir: Path | None = _CATALOG_DIR,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, str]],
    dict[str, str],
]:
    suite_skills = dict(_DEFAULT_SUITE_SKILLS)
    suite_artifacts = dict(_DEFAULT_SUITE_ARTIFACTS)
    task_to_suite = dict(_DEFAULT_TASK_TO_SUITE)
    if catalog_dir is None or not catalog_dir.is_dir():
        return suite_skills, suite_artifacts, task_to_suite

    suites_path = catalog_dir / "suites.json"
    if suites_path.is_file():
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            suites_doc = json.loads(suites_path.read_text(encoding="utf-8"))
            _parse_suites_doc(suites_doc, suite_skills, suite_artifacts)

    skill_to_suite = {
        skill: sname for sname, skills in suite_skills.items() for skill in skills
    }

    recipes_path = catalog_dir / "recipes.json"
    if recipes_path.is_file():
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            recipes_doc = json.loads(recipes_path.read_text(encoding="utf-8"))
            _parse_recipes_doc(recipes_doc, skill_to_suite, task_to_suite)

    return suite_skills, suite_artifacts, task_to_suite


_SUITE_SKILLS, _SUITE_ARTIFACTS, _TASK_TO_SUITE = _load_catalog_metadata()

KNOWN_SPECIALISTS: list[str] = [
    skill for skills in _SUITE_SKILLS.values() for skill in skills
]

_SKILL_TO_SUITE: dict[str, str] = {
    skill: suite_name
    for suite_name, skills in _SUITE_SKILLS.items()
    for skill in skills
}

_DOMAIN_ARTIFACTS: dict[str, tuple[str, str]] = {
    fname: (suite_name, schema_rel)
    for suite_name, (fname, schema_rel) in _SUITE_ARTIFACTS.items()
    if fname != "audit.json"
}

_COORDINATOR_SKILLS = frozenset({"repo-audit-program", "repo-audit-diagnostics"})

_SPECIALIST_PATTERNS: dict[str, re.Pattern[str]] = {
    specialist: re.compile(
        rf"(?<![a-zA-Z0-9_-]){re.escape(specialist)}(?![a-zA-Z0-9_-])"
    )
    for specialist in KNOWN_SPECIALISTS
}


class ParsedTurn:
    """Normalized turn or event parsed from a JSONL or JSON transcript."""

    __slots__ = ("is_failed", "line_number", "role", "text")

    def __init__(
        self,
        line_number: int,
        role: str,
        text: str,
        is_failed: bool = False,
    ) -> None:
        self.line_number = line_number
        self.role = role
        self.text = text
        self.is_failed = is_failed


def _validate_redaction_tier(tier: str) -> str:
    if tier not in REDACTION_TIERS:
        choices = ", ".join(REDACTION_TIERS)
        raise ValueError(f"Invalid redaction tier {tier!r}; expected one of: {choices}")
    return tier


def _is_sensitive_json_key(key: str) -> bool:
    norm = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    if not norm or norm in _NON_SECRET_TOKEN_KEYS or norm.endswith("_tokens"):
        return False
    if norm in _SENSITIVE_KEY_EXACT:
        return True
    if any(
        part in norm
        for part in (
            "password",
            "passwd",
            "secret",
            "api_key",
            "apikey",
            "private_key",
            "access_key",
        )
    ):
        return True
    return norm.endswith("_token") and norm not in {
        "page_token",
        "next_page_token",
        "continuation_token",
        "sync_token",
        "stop_token",
    }


def _scrub_string_patterns(text: str, stats: dict[str, int]) -> str:
    scrubbed = text
    for pattern, replacement, category in CREDENTIAL_PATTERNS_CATEGORIZED:
        scrubbed, count = pattern.subn(replacement, scrubbed)
        if count:
            stats[category] = stats.get(category, 0) + count
    return scrubbed


def _try_parse_json_container(text: str) -> dict[str, Any] | list[Any] | None:
    stripped = text.strip()
    if len(stripped) < 2:
        return None
    if not (
        (stripped[0] == "{" and stripped[-1] == "}")
        or (stripped[0] == "[" and stripped[-1] == "]")
    ):
        return None
    with contextlib.suppress(ValueError, TypeError, RecursionError):
        parsed = json.loads(stripped)
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


_CODEX_TOOL_OUTPUT_TYPES = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "local_shell_call_output",
    }
)
_EXEC_END_OUTPUT_KEYS = frozenset(
    {"stdout", "stderr", "output", "aggregated_output", "formatted_output"}
)
_TOOL_USE_RESULT_KEYS = frozenset({"stdout", "stderr", "output", "content"})
_TOOL_ROLES = frozenset({"tool", "function", "tool_result"})
_ROLE_TOOL_OUTPUT_KEYS = frozenset({"content", "output", "result"})
_STRUCTURED_TURN_KEYS: tuple[str, ...] = ("messages", "turns", "events", "transcript")
_PLAIN_TOOL_LINE_RE = re.compile(
    r"(?i)^(?:\[[^\]]+\]\s*)?(?:tool[_ ]?(?:output|result)|stdout|stderr)\s*:"
)


def _is_nonempty_exec_or_result_field(
    obj_type: Any, parent_key: str | None, key: str, val: Any
) -> bool:
    if not val:
        return False
    if obj_type == "exec_command_end":
        return key in _EXEC_END_OUTPUT_KEYS
    if parent_key == "toolUseResult":
        return key in _TOOL_USE_RESULT_KEYS
    return False


def _should_omit_dict_field(
    container: dict[str, Any], key: str, val: Any, parent_key: str | None
) -> bool:
    obj_type = container.get("type")
    if obj_type == "tool_result" and key == "content":
        return True
    if obj_type in _CODEX_TOOL_OUTPUT_TYPES and key == "output":
        return True
    if container.get("role") in _TOOL_ROLES and key in _ROLE_TOOL_OUTPUT_KEYS:
        return True
    if _is_nonempty_exec_or_result_field(obj_type, parent_key, key, val):
        return True
    return key == "toolUseResult" and isinstance(val, (str, list))


def _omit_tool_value(val: Any, omitted_counter: list[int] | None) -> str:
    if val != OMITTED_TOOL_OUTPUT_PLACEHOLDER and omitted_counter is not None:
        omitted_counter[0] += 1
    return OMITTED_TOOL_OUTPUT_PLACEHOLDER


def _scrub_json_dict(
    value: dict[str, Any],
    stats: dict[str, int],
    *,
    parent_key: str | None,
    omit_tool_outputs: bool,
    omitted_counter: list[int] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in value.items():
        if omit_tool_outputs and _should_omit_dict_field(value, k, v, parent_key):
            result[k] = _omit_tool_value(v, omitted_counter)
            continue
        result[k] = _scrub_json_value(
            v,
            stats,
            parent_key=k,
            omit_tool_outputs=omit_tool_outputs,
            omitted_counter=omitted_counter,
        )
    return result


def _needs_key_based_redaction(
    parent_key: str | None,
    scrubbed_str: str,
    stats: dict[str, int],
    before_total: int,
) -> bool:
    if not parent_key or not _is_sensitive_json_key(parent_key):
        return False
    stripped = scrubbed_str.strip()
    if not stripped or stripped.startswith("[REDACTED_"):
        return False
    return sum(stats.values()) == before_total


def _redact_sensitive_key_value(
    parent_key: str, scrubbed_str: str, stats: dict[str, int]
) -> str:
    norm_key = re.sub(r"[^a-z0-9]+", "_", parent_key.lower()).strip("_")
    if norm_key in {"authorization", "proxy_authorization"}:
        prefix_match = re.match(
            r"(?i)^((?:bearer|basic|token)\s+)(.+)$", scrubbed_str.strip()
        )
        stats["auth_token"] = stats.get("auth_token", 0) + 1
        if prefix_match:
            return f"{prefix_match.group(1)}[REDACTED_AUTH_TOKEN]"
        return "[REDACTED_AUTH_TOKEN]"
    stats["json_secret"] = stats.get("json_secret", 0) + 1
    return "[REDACTED_SECRET]"


def _scrub_json_string(
    value: str,
    stats: dict[str, int],
    *,
    parent_key: str | None,
    omit_tool_outputs: bool,
    omitted_counter: list[int] | None,
) -> str:
    nested = _try_parse_json_container(value)
    if nested is not None:
        scrubbed_nested = _scrub_json_value(
            nested,
            stats,
            parent_key=None,
            omit_tool_outputs=omit_tool_outputs,
            omitted_counter=omitted_counter,
        )
        serialized = json.dumps(scrubbed_nested, ensure_ascii=False)
        return _scrub_string_patterns(serialized, stats)

    before_total = sum(stats.values())
    scrubbed_str = _scrub_string_patterns(value, stats)
    if parent_key and _needs_key_based_redaction(
        parent_key, scrubbed_str, stats, before_total
    ):
        return _redact_sensitive_key_value(parent_key, scrubbed_str, stats)
    return scrubbed_str


def _scrub_json_value(
    value: Any,
    stats: dict[str, int],
    *,
    parent_key: str | None = None,
    omit_tool_outputs: bool = False,
    omitted_counter: list[int] | None = None,
) -> Any:
    if isinstance(value, dict):
        return _scrub_json_dict(
            value,
            stats,
            parent_key=parent_key,
            omit_tool_outputs=omit_tool_outputs,
            omitted_counter=omitted_counter,
        )
    if isinstance(value, list):
        return [
            _scrub_json_value(
                item,
                stats,
                parent_key=parent_key,
                omit_tool_outputs=omit_tool_outputs,
                omitted_counter=omitted_counter,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _scrub_json_string(
            value,
            stats,
            parent_key=parent_key,
            omit_tool_outputs=omit_tool_outputs,
            omitted_counter=omitted_counter,
        )
    return value


def _compute_default_cited_lines(text: str) -> set[int]:
    failure = detect_failures(text)
    line_no = failure.get("line_number")
    return {line_no} if isinstance(line_no, int) and line_no >= 1 else set()


def _line_ending(raw_line: str) -> str:
    if raw_line.endswith("\r\n"):
        return "\r\n"
    return "\n" if raw_line.endswith("\n") else ""


def _should_omit_line_tools(tier: str, idx: int, resolved_cited: set[int]) -> bool:
    if tier == "skeleton":
        return True
    return tier == "evidence" and idx not in resolved_cited


def _parse_jsonl_records(
    lines: list[str],
) -> list[tuple[int, str, dict[str, Any] | None]] | None:
    if not any(ln.strip() for ln in lines):
        return None
    records: list[tuple[int, str, dict[str, Any] | None]] = []
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            records.append((idx, raw_line, None))
            continue
        parsed_line = _try_parse_json_container(stripped)
        if not isinstance(parsed_line, dict):
            return None
        records.append((idx, raw_line, parsed_line))
    return records


def _scrub_jsonl_records(
    records: list[tuple[int, str, dict[str, Any] | None]],
    tier: str,
    resolved_cited: set[int],
    stats: dict[str, int],
    omitted_counter: list[int],
) -> str:
    out_lines: list[str] = []
    for idx, raw_line, parsed_obj in records:
        if parsed_obj is None:
            out_lines.append(raw_line)
            continue
        omit_line_tools = _should_omit_line_tools(tier, idx, resolved_cited)
        scrubbed_obj = _scrub_json_value(
            parsed_obj,
            stats,
            omit_tool_outputs=omit_line_tools,
            omitted_counter=omitted_counter,
        )
        serialized_line = _scrub_string_patterns(
            json.dumps(scrubbed_obj, ensure_ascii=False), stats
        )
        out_lines.append(serialized_line + _line_ending(raw_line))
    return "".join(out_lines)


def _has_structured_turn_array(whole_json: object) -> bool:
    if not isinstance(whole_json, dict):
        return False
    return any(isinstance(whole_json.get(k), list) for k in _STRUCTURED_TURN_KEYS)


def _scrub_evidence_json_dict(
    whole_json: dict[str, Any],
    resolved_cited: set[int],
    stats: dict[str, int],
    omitted_counter: list[int],
) -> dict[str, Any]:
    copied = dict(whole_json)
    for key in _STRUCTURED_TURN_KEYS:
        items = copied.get(key)
        if isinstance(items, list):
            copied[key] = [
                _scrub_json_value(
                    item,
                    stats,
                    omit_tool_outputs=item_idx not in resolved_cited,
                    omitted_counter=omitted_counter,
                )
                for item_idx, item in enumerate(items, start=1)
            ]
    for k, v in copied.items():
        if k not in _STRUCTURED_TURN_KEYS:
            copied[k] = _scrub_json_value(
                v,
                stats,
                parent_key=k,
                omit_tool_outputs=False,
                omitted_counter=omitted_counter,
            )
    return copied


def _scrub_structured_json_document(
    text: str,
    whole_json: dict[str, Any] | list[Any],
    tier: str,
    resolved_cited: set[int],
    stats: dict[str, int],
    omitted_counter: list[int],
) -> str:
    if (
        tier == "evidence"
        and resolved_cited
        and isinstance(whole_json, dict)
        and _has_structured_turn_array(whole_json)
    ):
        scrubbed_obj: Any = _scrub_evidence_json_dict(
            whole_json, resolved_cited, stats, omitted_counter
        )
    else:
        omit_tools = tier == "skeleton" or (tier == "evidence" and not resolved_cited)
        scrubbed_obj = _scrub_json_value(
            whole_json,
            stats,
            omit_tool_outputs=omit_tools,
            omitted_counter=omitted_counter,
        )
    trailing_nl = "\n" if text.endswith("\n") else ""
    return _scrub_string_patterns(
        json.dumps(scrubbed_obj, indent=2, ensure_ascii=False) + trailing_nl,
        stats,
    )


def _scrub_plain_text_line(
    raw_line: str,
    omit_line_tools: bool,
    stats: dict[str, int],
    omitted_counter: list[int],
) -> str:
    stripped = raw_line.strip()
    parsed_inline = _try_parse_json_container(stripped)
    if isinstance(parsed_inline, dict):
        scrubbed_inline = _scrub_json_value(
            parsed_inline,
            stats,
            omit_tool_outputs=omit_line_tools,
            omitted_counter=omitted_counter,
        )
        serialized = _scrub_string_patterns(
            json.dumps(scrubbed_inline, ensure_ascii=False), stats
        )
        return serialized + _line_ending(raw_line)
    if omit_line_tools and _PLAIN_TOOL_LINE_RE.match(stripped):
        prefix = raw_line.split(":", 1)[0]
        omitted_counter[0] += 1
        return (
            f"{_scrub_string_patterns(prefix, stats)}: "
            f"{OMITTED_TOOL_OUTPUT_PLACEHOLDER}{_line_ending(raw_line)}"
        )
    return _scrub_string_patterns(raw_line, stats)


def _scrub_plain_text_transcript(
    text: str,
    lines: list[str],
    tier: str,
    resolved_cited: set[int],
    stats: dict[str, int],
    omitted_counter: list[int],
) -> str:
    if not lines:
        return _scrub_string_patterns(text, stats)
    out_plain = [
        _scrub_plain_text_line(
            raw_line,
            _should_omit_line_tools(tier, idx, resolved_cited),
            stats,
            omitted_counter,
        )
        for idx, raw_line in enumerate(lines, start=1)
    ]
    return _scrub_string_patterns("".join(out_plain), stats)


def scrub_credentials_with_stats(
    text: str,
    tier: str = "full",
    cited_lines: Collection[int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Scrub secrets recursively across text/JSON/JSONL and apply redaction tier."""
    _validate_redaction_tier(tier)
    stats: dict[str, int] = {}
    omitted_counter = [0]

    resolved_cited: set[int] = set()
    if tier == "evidence":
        resolved_cited = (
            set(cited_lines)
            if cited_lines is not None
            else _compute_default_cited_lines(text)
        )

    lines = text.splitlines(keepends=True)
    jsonl_records = _parse_jsonl_records(lines)
    if jsonl_records is not None:
        scrubbed_text = _scrub_jsonl_records(
            jsonl_records, tier, resolved_cited, stats, omitted_counter
        )
    elif (whole_json := _try_parse_json_container(text)) is not None:
        scrubbed_text = _scrub_structured_json_document(
            text, whole_json, tier, resolved_cited, stats, omitted_counter
        )
    else:
        scrubbed_text = _scrub_plain_text_transcript(
            text, lines, tier, resolved_cited, stats, omitted_counter
        )

    credentials_redacted = sum(stats.values())
    tool_outputs_omitted = omitted_counter[0]
    summary = {
        "tier": tier,
        "credentials_redacted": credentials_redacted,
        "tool_outputs_omitted": tool_outputs_omitted,
        "total_redactions": credentials_redacted + tool_outputs_omitted,
        "by_category": dict(sorted(stats.items())),
    }
    return scrubbed_text, summary


def scrub_credentials(
    text: str,
    tier: str = "full",
    cited_lines: Collection[int] | None = None,
) -> str:
    """Scrub tokens, passwords, JSON fields, URI secrets, and keys from text."""
    scrubbed, _ = scrub_credentials_with_stats(text, tier=tier, cited_lines=cited_lines)
    return scrubbed


_NESTED_STR_KEYS: tuple[str, ...] = (
    "output",
    "stderr",
    "stdout",
    "error",
    "message",
    "text",
    "result",
)
_DICT_CONTENT_KEYS: tuple[str, ...] = (
    "message",
    "error",
    "stderr",
    "stdout",
    "output",
    "text",
    "content",
    "result",
)


def _extract_nested_str_field(val: Any) -> str | None:
    if isinstance(val, str) and val.strip():
        return val
    if isinstance(val, dict):
        sub = _stringify_content_value(val)
        return sub if sub else None
    return None


def _stringify_nested_json_str(content: str) -> str:
    nested = _try_parse_json_container(content)
    if not isinstance(nested, dict):
        return content
    parts: list[str] = []
    for key in _NESTED_STR_KEYS:
        part = _extract_nested_str_field(nested.get(key))
        if part is not None:
            parts.append(part)
    return "\n".join(parts) if parts else content


def _extract_list_item_text(item: dict[str, Any]) -> str:
    text = (
        item.get("text")
        or item.get("content")
        or item.get("output")
        or item.get("message")
    )
    return _stringify_content_value(text) if text is not None else ""


def _stringify_content_list(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            rendered = _extract_list_item_text(item)
            if rendered:
                parts.append(rendered)
    return "\n".join(parts)


def _stringify_content_dict(content: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _DICT_CONTENT_KEYS:
        if key in content and content[key] is not None:
            rendered = _stringify_content_value(content[key])
            if rendered:
                parts.append(rendered)
    if parts:
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _stringify_content_value(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _stringify_nested_json_str(content)
    if isinstance(content, list):
        return _stringify_content_list(content)
    if isinstance(content, dict):
        return _stringify_content_dict(content)
    return str(content)


def _has_nonzero_exit_code(value: dict[str, Any]) -> bool:
    for code_key in ("exit_code", "returncode", "exitCode", "return_code"):
        code = value.get(code_key)
        if isinstance(code, int) and code != 0:
            return True
    return False


def _check_dict_tool_failure(value: dict[str, Any]) -> bool:
    if value.get("is_error") is True or value.get("timed_out") is True:
        return True
    if str(value.get("status", "")).lower() in {
        "error",
        "failed",
        "timeout",
        "timed_out",
    }:
        return True
    if _has_nonzero_exit_code(value):
        return True
    meta = value.get("metadata")
    if isinstance(meta, dict) and _check_nested_tool_failure(meta):
        return True
    return value.get("error") not in (None, False, "", {})


def _check_nested_tool_failure(value: Any) -> bool:
    if isinstance(value, dict):
        return _check_dict_tool_failure(value)
    if isinstance(value, str):
        nested = _try_parse_json_container(value)
        if isinstance(nested, dict):
            return _check_dict_tool_failure(nested)
    return False


_EXPLICIT_ERROR_TYPES = frozenset({"error", "api_error", "stream_error", "exception"})
_EXPLICIT_ERROR_LEVELS = frozenset({"error", "fatal"})
_SKIPPED_RECORD_TYPES = frozenset(
    {"session_meta", "turn_context", "summary", "file-history-snapshot"}
)
_USER_LIKE_ROLES = frozenset({"user", "human", "system", "developer"})


def _is_explicit_error_record(obj: dict[str, Any], rec_type: str) -> bool:
    if rec_type in _EXPLICIT_ERROR_TYPES:
        return True
    return str(obj.get("level", "")).lower() in _EXPLICIT_ERROR_LEVELS


def _extract_explicit_error_turns(
    obj: dict[str, Any], line_no: int
) -> list[ParsedTurn]:
    err_text = _stringify_content_value(
        obj.get("error") or obj.get("message") or obj.get("payload") or obj
    )
    return [ParsedTurn(line_no, "error", err_text, is_failed=True)]


def _build_exec_command_end_turn(payload: dict[str, Any], line_no: int) -> ParsedTurn:
    failed = _check_nested_tool_failure(payload)
    raw_out = (
        payload.get("stderr")
        or payload.get("aggregated_output")
        or payload.get("formatted_output")
        or payload.get("stdout")
        or ""
    )
    out_text = _stringify_content_value(raw_out)
    cmd = payload.get("command")
    if isinstance(cmd, list):
        cmd_str = " ".join(str(c) for c in cmd)
        out_text = f"{cmd_str}: {out_text}".strip(": ")
    return ParsedTurn(line_no, "tool_output", out_text, is_failed=failed)


def _extract_codex_event_msg_turns(
    payload: dict[str, Any], line_no: int
) -> list[ParsedTurn]:
    ptype = str(payload.get("type", "")).lower()
    if ptype in {"error", "stream_error", "api_error"}:
        err_text = _stringify_content_value(
            payload.get("message") or payload.get("error") or payload
        )
        return [ParsedTurn(line_no, "error", err_text, is_failed=True)]
    if ptype == "exec_command_end":
        return [_build_exec_command_end_turn(payload, line_no)]
    if ptype == "user_message":
        msg_text = _stringify_content_value(
            payload.get("message") or payload.get("text")
        )
        return [ParsedTurn(line_no, "user", msg_text)]
    if ptype in {"agent_message", "assistant_message"}:
        msg_text = _stringify_content_value(
            payload.get("message") or payload.get("text")
        )
        return [ParsedTurn(line_no, "assistant", msg_text)]
    return []


def _extract_codex_response_item_turns(
    payload: dict[str, Any], line_no: int
) -> list[ParsedTurn]:
    ptype = str(payload.get("type", "")).lower()
    if ptype == "message":
        role = str(payload.get("role", "assistant")).lower()
        norm_role = "user" if role in _USER_LIKE_ROLES else "assistant"
        text = _stringify_content_value(payload.get("content"))
        return [ParsedTurn(line_no, norm_role, text, is_failed=False)]
    if ptype in {"function_call", "custom_tool_call", "local_shell_call"}:
        name = payload.get("name") or payload.get("tool_name") or ""
        args = _stringify_content_value(
            payload.get("arguments") or payload.get("input") or payload.get("action")
        )
        return [
            ParsedTurn(line_no, "tool_use", f"{name} {args}".strip(), is_failed=False)
        ]
    if ptype in _CODEX_TOOL_OUTPUT_TYPES:
        raw_output = payload.get("output")
        failed = _check_nested_tool_failure(payload) or _check_nested_tool_failure(
            raw_output
        )
        return [
            ParsedTurn(
                line_no,
                "tool_output",
                _stringify_content_value(raw_output),
                is_failed=failed,
            )
        ]
    return []


def _extract_claude_system_turns(obj: dict[str, Any], line_no: int) -> list[ParsedTurn]:
    subtype = str(obj.get("subtype", "")).lower()
    if "error" in subtype or obj.get("is_error") is True or obj.get("error"):
        err_text = _stringify_content_value(
            obj.get("error") or obj.get("message") or obj.get("content")
        )
        return [ParsedTurn(line_no, "error", err_text, is_failed=True)]
    return []


def _extract_claude_result_turns(obj: dict[str, Any], line_no: int) -> list[ParsedTurn]:
    is_err = bool(obj.get("is_error")) or "error" in str(obj.get("subtype", "")).lower()
    res_text = _stringify_content_value(
        obj.get("result") or obj.get("error") or obj.get("message")
    )
    if is_err:
        return [ParsedTurn(line_no, "error", res_text, is_failed=True)]
    if res_text:
        return [ParsedTurn(line_no, "assistant", res_text, is_failed=False)]
    return []


def _is_envelope_api_error(obj: dict[str, Any], msg: dict[str, Any]) -> bool:
    if obj.get("isApiErrorMessage") or obj.get("is_error") or msg.get("is_error"):
        return True
    if obj.get("error") not in (None, False, "", {}):
        return True
    return str(msg.get("stop_reason", "")).lower() == "model_context_window_exceeded"


def _extract_dict_content_block(
    block: dict[str, Any],
    obj: dict[str, Any],
    role: str,
    is_api_error: bool,
    line_no: int,
) -> ParsedTurn | None:
    btype = str(block.get("type", "")).lower()
    if btype == "tool_result":
        failed = bool(block.get("is_error")) or _check_nested_tool_failure(
            obj.get("toolUseResult")
        )
        btext = _stringify_content_value(block.get("content"))
        if not btext and obj.get("toolUseResult"):
            btext = _stringify_content_value(obj.get("toolUseResult"))
        return ParsedTurn(line_no, "tool_output", btext, is_failed=failed)
    if btype in {"tool_use", "function_call"}:
        name = block.get("name", "")
        inp = _stringify_content_value(block.get("input") or block.get("arguments"))
        return ParsedTurn(line_no, "tool_use", f"{name} {inp}".strip(), is_failed=False)
    if btype in {"text", "input_text", "output_text"}:
        btext = _stringify_content_value(block.get("text"))
        if is_api_error:
            return ParsedTurn(line_no, "error", btext, is_failed=True)
        target_role = "user" if role in _USER_LIKE_ROLES else "assistant"
        return ParsedTurn(line_no, target_role, btext, is_failed=False)
    return None


def _extract_str_content_block(
    block: str, role: str, is_api_error: bool, line_no: int
) -> ParsedTurn:
    if is_api_error:
        return ParsedTurn(line_no, "error", block, is_failed=True)
    target_role = "user" if role in {"user", "human", "system"} else "assistant"
    return ParsedTurn(line_no, target_role, block, is_failed=False)


def _extract_content_block_turns(
    content: list[Any],
    obj: dict[str, Any],
    role: str,
    is_api_error: bool,
    line_no: int,
) -> list[ParsedTurn]:
    turns: list[ParsedTurn] = []
    for block in content:
        if isinstance(block, dict):
            parsed_turn = _extract_dict_content_block(
                block, obj, role, is_api_error, line_no
            )
            if parsed_turn is not None:
                turns.append(parsed_turn)
        elif isinstance(block, str):
            turns.append(_extract_str_content_block(block, role, is_api_error, line_no))
    return turns


def _resolve_direct_tool_output(obj: dict[str, Any], msg: dict[str, Any]) -> Any:
    if msg.get("content") is not None:
        return msg.get("content")
    if obj.get("output") is not None:
        return obj.get("output")
    return obj.get("result")


def _extract_direct_tool_turns(
    obj: dict[str, Any],
    msg: dict[str, Any],
    rec_type: str,
    role: str,
    line_no: int,
) -> list[ParsedTurn] | None:
    if rec_type in {"tool_result", "tool_output", "function_call_output"} or role in {
        "tool",
        "function",
        "tool_result",
    }:
        raw_out = _resolve_direct_tool_output(obj, msg)
        failed = _check_nested_tool_failure(obj) or _check_nested_tool_failure(msg)
        return [
            ParsedTurn(
                line_no,
                "tool_output",
                _stringify_content_value(raw_out),
                is_failed=failed,
            )
        ]
    if rec_type in {"tool_use", "function_call"}:
        name = obj.get("name") or msg.get("name") or ""
        inp = _stringify_content_value(
            obj.get("input") or obj.get("arguments") or msg.get("input")
        )
        return [
            ParsedTurn(line_no, "tool_use", f"{name} {inp}".strip(), is_failed=False)
        ]
    return None


def _extract_openai_tool_calls(tool_calls: object, line_no: int) -> list[ParsedTurn]:
    if not isinstance(tool_calls, list):
        return []
    turns: list[ParsedTurn] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        raw_fn = tc.get("function")
        fn: dict[str, Any] = raw_fn if isinstance(raw_fn, dict) else tc
        call_text = (
            f"{fn.get('name', '')} {_stringify_content_value(fn.get('arguments'))}"
        ).strip()
        turns.append(ParsedTurn(line_no, "tool_use", call_text, is_failed=False))
    return turns


def _extract_scalar_and_openai_turns(
    obj: dict[str, Any],
    msg: dict[str, Any],
    content: Any,
    role: str,
    is_api_error: bool,
    line_no: int,
) -> list[ParsedTurn]:
    fallback = obj.get("error") or obj.get("text") or obj.get("output")
    text_val = _stringify_content_value(content if content is not None else fallback)
    if is_api_error:
        return [ParsedTurn(line_no, "error", text_val, is_failed=True)]
    if role in _USER_LIKE_ROLES:
        return [ParsedTurn(line_no, "user", text_val, is_failed=False)]
    if role in {"assistant", "model", "agent"}:
        turns = _extract_openai_tool_calls(msg.get("tool_calls"), line_no)
        if text_val:
            turns.append(ParsedTurn(line_no, "assistant", text_val, is_failed=False))
        return turns
    return []


def _extract_message_envelope_turns(
    obj: dict[str, Any], rec_type: str, line_no: int
) -> list[ParsedTurn]:
    raw_msg = obj.get("message")
    msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else obj
    role = str(msg.get("role") or rec_type or "").lower()
    is_api_error = _is_envelope_api_error(obj, msg)

    content = msg.get("content")
    if isinstance(content, list):
        block_turns = _extract_content_block_turns(
            content, obj, role, is_api_error, line_no
        )
        if block_turns:
            return block_turns

    direct_turns = _extract_direct_tool_turns(obj, msg, rec_type, role, line_no)
    if direct_turns is not None:
        return direct_turns

    return _extract_scalar_and_openai_turns(
        obj, msg, content, role, is_api_error, line_no
    )


def _extract_turns_from_record(obj: dict[str, Any], line_no: int) -> list[ParsedTurn]:
    """Extract normalized role-separated turns from a single JSON/JSONL record."""
    rec_type = str(obj.get("type", "")).lower()
    if _is_explicit_error_record(obj, rec_type):
        return _extract_explicit_error_turns(obj, line_no)
    payload = obj.get("payload")
    if rec_type == "event_msg" and isinstance(payload, dict):
        return _extract_codex_event_msg_turns(payload, line_no)
    if rec_type == "response_item" and isinstance(payload, dict):
        return _extract_codex_response_item_turns(payload, line_no)
    if rec_type in _SKIPPED_RECORD_TYPES:
        return []
    if rec_type == "system":
        return _extract_claude_system_turns(obj, line_no)
    if rec_type == "result":
        return _extract_claude_result_turns(obj, line_no)
    return _extract_message_envelope_turns(obj, rec_type, line_no)


def _try_parse_jsonl_turns(non_empty: list[tuple[int, str]]) -> list[ParsedTurn] | None:
    jsonl_turns: list[ParsedTurn] = []
    json_line_count = 0
    for idx, stripped in non_empty:
        parsed = _try_parse_json_container(stripped)
        if isinstance(parsed, dict):
            json_line_count += 1
            jsonl_turns.extend(_extract_turns_from_record(parsed, idx))
        elif json_line_count > 0:
            jsonl_turns.append(ParsedTurn(idx, "error", stripped, is_failed=False))
    if json_line_count > 0 and json_line_count >= len(non_empty) // 2:
        return jsonl_turns
    return None


def _extract_document_records(
    whole: dict[str, Any] | list[Any],
) -> list[dict[str, Any]]:
    if isinstance(whole, list):
        return [item for item in whole if isinstance(item, dict)]
    for key in ("messages", "turns", "events", "transcript", "items"):
        items = whole.get(key)
        if isinstance(items, list):
            records = [item for item in items if isinstance(item, dict)]
            if records:
                return records
            break
    return [whole]


def _parse_whole_json_turns(
    transcript_text: str, lines: list[str]
) -> list[ParsedTurn] | None:
    whole = _try_parse_json_container(transcript_text)
    if whole is None:
        return None
    doc_turns: list[ParsedTurn] = []
    for rec in _extract_document_records(whole):
        for turn in _extract_turns_from_record(rec, 1):
            turn.line_number = _locate_snippet_line(lines, turn.text) or len(lines) or 1
            doc_turns.append(turn)
    return doc_turns


def parse_transcript_turns(transcript_text: str) -> tuple[list[ParsedTurn], bool]:
    """Parse JSONL or JSON transcript into role-separated turns.

    Returns (turns, is_structured) where is_structured indicates whether the
    input was parsed as JSONL/JSON rather than plain text.
    """
    lines = transcript_text.splitlines()
    non_empty = [
        (idx, ln.strip()) for idx, ln in enumerate(lines, start=1) if ln.strip()
    ]
    if not non_empty:
        return [], False

    jsonl_turns = _try_parse_jsonl_turns(non_empty)
    if jsonl_turns is not None:
        return jsonl_turns, True

    doc_turns = _parse_whole_json_turns(transcript_text, lines)
    if doc_turns is not None:
        return doc_turns, True

    return [], False


def _locate_snippet_line(lines: list[str], snippet: str) -> int | None:
    first_line = next((ln.strip() for ln in snippet.splitlines() if ln.strip()), "")
    if not first_line:
        return None
    probe = first_line[:80]
    for idx in range(len(lines), 0, -1):
        if probe in lines[idx - 1]:
            return idx
    return None


_SIGNATURE_RULES: tuple[tuple[list[re.Pattern[str]], str, str], ...] = (
    (TIMEOUT_PATTERNS, "tool_timeout", "Tool execution timed out"),
    (SCHEMA_PATTERNS, "schema_validation_error", "Audit schema validation error"),
    (CONTEXT_PATTERNS, "context_limit", "Context window token limit exceeded"),
)


def _match_subline_signature(sub: str, line_no: int) -> dict[str, Any] | None:
    for patterns, failure_type, prefix in _SIGNATURE_RULES:
        for pattern in patterns:
            if pattern.search(sub):
                return {
                    "failure_type": failure_type,
                    "summary": f"{prefix}: {sub}",
                    "line_number": line_no,
                    "matched_line": sub,
                    "details": {"pattern": pattern.pattern},
                }
    return None


def _match_known_signature(text: str, line_no: int) -> dict[str, Any] | None:
    """Match text against context, schema, or timeout failure signatures."""
    sublines = [ln.strip() for ln in text.splitlines() if ln.strip()] or [text.strip()]
    for sub in sublines:
        matched = _match_subline_signature(sub, line_no)
        if matched is not None:
            return matched
    return None


def _build_unclassified_from_turn(turn: ParsedTurn) -> dict[str, Any]:
    sublines = [ln.strip() for ln in turn.text.splitlines() if ln.strip()]
    chosen = sublines[0] if sublines else ""
    for sub in sublines:
        if GENERAL_ERROR_RE.search(sub):
            chosen = sub
            break
    return {
        "failure_type": "unclassified_failure",
        "summary": f"Unclassified execution failure: {chosen}",
        "line_number": turn.line_number,
        "matched_line": chosen,
        "details": {"role": turn.role},
    }


def _is_normal_progress_turn(turn: ParsedTurn) -> bool:
    if turn.role in {"user", "tool_use"}:
        return True
    return turn.role in {"assistant", "tool_output"} and not turn.is_failed


def _split_terminal_turns(
    turns: list[ParsedTurn],
) -> tuple[list[ParsedTurn], list[ParsedTurn]]:
    last_recovery_idx = -1
    for idx, turn in enumerate(turns):
        if idx < len(turns) - 1 and _is_normal_progress_turn(turn):
            last_recovery_idx = idx
    return turns[last_recovery_idx + 1 :], turns[: last_recovery_idx + 1]


def _match_turns_backward(turns: Iterable[ParsedTurn]) -> dict[str, Any] | None:
    for turn in turns:
        matched = _match_known_signature(turn.text, turn.line_number)
        if matched is not None:
            matched["details"]["role"] = turn.role
            return matched
    return None


def _match_terminal_assistant_turn(turns: list[ParsedTurn]) -> dict[str, Any] | None:
    for turn in reversed(turns):
        if turn.role == "user":
            return None
        if turn.role == "assistant":
            matched = _match_known_signature(turn.text, turn.line_number)
            if matched is not None:
                matched["details"]["role"] = turn.role
            return matched
    return None


def _match_terminal_tool_general_error(
    turns: list[ParsedTurn],
) -> dict[str, Any] | None:
    for turn in reversed(turns):
        if turn.role == "tool_output":
            if GENERAL_ERROR_RE.search(turn.text):
                return _build_unclassified_from_turn(turn)
            return None
    return None


def _unexpected_end_failure(lines: list[str], matched_line: str) -> dict[str, Any]:
    return {
        "failure_type": "unclassified_failure",
        "summary": "Session ended unexpectedly without an explicit error signature",
        "line_number": len(lines) if lines else 1,
        "matched_line": matched_line,
        "details": {},
    }


def _fallback_structured_end_failure(
    turns: list[ParsedTurn], lines: list[str]
) -> dict[str, Any]:
    last_turn_line = next(
        (ln.strip() for ln in reversed(turns[-1].text.splitlines()) if ln.strip()),
        "",
    )
    matched_line = last_turn_line or (lines[-1].strip() if lines else "")
    return _unexpected_end_failure(lines, matched_line)


def _failed_turns(turns: list[ParsedTurn]) -> list[ParsedTurn]:
    return [t for t in turns if t.is_failed]


def _detect_structured_failure(
    turns: list[ParsedTurn], lines: list[str]
) -> dict[str, Any]:
    terminal_turns, earlier_turns = _split_terminal_turns(turns)
    terminal_failed = _failed_turns(terminal_turns)
    if matched := _match_turns_backward(reversed(terminal_failed)):
        return matched
    if terminal_failed:
        return _build_unclassified_from_turn(terminal_failed[-1])

    earlier_failed = _failed_turns(earlier_turns)
    if matched := _match_turns_backward(reversed(earlier_failed)):
        return matched

    tool_outputs = (t for t in reversed(turns) if t.role == "tool_output")
    if matched := _match_turns_backward(tool_outputs):
        return matched

    if matched := _match_terminal_assistant_turn(turns):
        return matched
    if earlier_failed:
        return _build_unclassified_from_turn(earlier_failed[-1])
    if matched := _match_terminal_tool_general_error(turns):
        return matched

    return _fallback_structured_end_failure(turns, lines)


def _detect_plaintext_failure(lines: list[str]) -> dict[str, Any]:
    for idx, line in enumerate(lines, start=1):
        matched = _match_known_signature(line, idx)
        if matched is not None:
            return matched
    for idx, line in enumerate(lines, start=1):
        if GENERAL_ERROR_RE.search(line):
            return {
                "failure_type": "unclassified_failure",
                "summary": f"Unclassified execution failure: {line.strip()}",
                "line_number": idx,
                "matched_line": line.strip(),
                "details": {},
            }
    return _unexpected_end_failure(lines, lines[-1].strip() if lines else "")


def detect_failures(transcript_text: str) -> dict[str, Any]:
    """Detect timeouts, schema errors, context limits, or unclassified failures."""
    lines = transcript_text.splitlines()
    turns, is_structured = parse_transcript_turns(transcript_text)
    if is_structured and turns:
        return _detect_structured_failure(turns, lines)
    return _detect_plaintext_failure(lines)


def _topic_for_specialist(specialist: str) -> str:
    for prefix in ("repo-audit-", "repo-", "arch-", "review-", "test-", "spec-"):
        if specialist.startswith(prefix):
            return specialist[len(prefix) :]
    return specialist


def _find_specialist_in_segment(text: str, *, prefer_first: bool = False) -> str | None:
    """Find a specialist skill mentioned in a single turn/line segment."""
    domain_matches: list[tuple[int, str]] = []
    coord_matches: list[tuple[int, str]] = []

    for specialist, pattern in _SPECIALIST_PATTERNS.items():
        found = list(pattern.finditer(text))
        if found:
            pos = found[0].start() if prefer_first else found[-1].start()
            if specialist in _COORDINATOR_SKILLS:
                coord_matches.append((pos, specialist))
            else:
                domain_matches.append((pos, specialist))

    pool = domain_matches or coord_matches
    if not pool:
        return None
    pool.sort(key=lambda item: item[0], reverse=not prefer_first)
    return pool[0][1]


def _find_topic_keyword_in_segment(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    matches: list[tuple[int, str, str]] = []
    for specialist in _SUITE_SKILLS["repository-audits"]:
        if specialist in _COORDINATOR_SKILLS:
            continue
        keyword = specialist.removeprefix("repo-audit-")
        pattern = re.compile(
            rf"(?<![a-z0-9_-]){re.escape(keyword)}(?![a-z0-9_-])(?!\.(?:json|schema|md)|-template)"
        )
        found = list(pattern.finditer(lowered))
        if found:
            matches.append((found[-1].start(), specialist, keyword))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1], matches[0][2]


def _is_audit_ledger_shape(data: Mapping[str, Any]) -> bool:
    return "findings" in data and any(
        key in data for key in ("evidence", "coverage", "contracts", "run_id")
    )


def _classify_fallback_payload(data: dict[str, Any]) -> str | None:
    if "summary" in data and "findings" in data:
        return "review"
    if "component" in data and "framework" in data:
        return "testing"
    if "title" in data and "status" in data and "findings" not in data:
        return "architecture"
    return "run" if "run_id" in data else None


def _classify_domain_payload(data: object) -> str | None:
    if not isinstance(data, dict) or _is_audit_ledger_shape(data):
        return None
    keys = set(data)
    for kind, markers in _DOMAIN_KIND_KEY_SETS:
        if keys & markers:
            return kind
    return _classify_fallback_payload(data)


def _detect_artifact_suite(audit_data: object) -> str | None:
    kind = _classify_domain_payload(audit_data)
    if kind is None:
        return None
    return _DOMAIN_KIND_TO_SUITE.get(kind)


def _suite_from_expected_outputs(outputs: object) -> str | None:
    if not isinstance(outputs, list):
        return None
    for fname, (sname, _) in _DOMAIN_ARTIFACTS.items():
        if fname in outputs:
            return sname
    return None


def _suite_from_plan_skills(skills: object) -> str | None:
    if not isinstance(skills, list):
        return None
    suites = [
        _SKILL_TO_SUITE[s]
        for s in skills
        if isinstance(s, str) and s in _SKILL_TO_SUITE
    ]
    if "repository-audits" in suites:
        return "repository-audits"
    return suites[0] if suites else None


def _suite_from_run_data(run_data: dict[str, Any] | None) -> str | None:
    if not isinstance(run_data, dict):
        return None
    plan_data = run_data.get("plan")
    if not isinstance(plan_data, dict):
        return None
    outputs = plan_data.get("expected_outputs")
    if suite := _suite_from_expected_outputs(outputs):
        return suite
    if suite := _suite_from_plan_skills(plan_data.get("skills")):
        return suite
    task = plan_data.get("task")
    if isinstance(task, str) and task in _TASK_TO_SUITE:
        return _TASK_TO_SUITE[task]
    if isinstance(outputs, list) and "audit.json" in outputs:
        return "repository-audits"
    return None


_TRANSCRIPT_DOMAIN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\barchitecture\.(?:json|schema\.json)\b"), "software-architecture"),
    (re.compile(r"\breview\.(?:json|schema\.json)\b"), "code-review"),
    (re.compile(r"\btesting\.(?:json|schema\.json)\b"), "automated-testing"),
    (re.compile(r"\bspec\.(?:json|schema\.json)\b"), "spec-driven-development"),
)


def _detect_transcript_domain_suite(transcript_text: str) -> str | None:
    if re.search(r"\baudit\.(?:json|schema\.json)\b", transcript_text):
        return None
    for pattern, suite_name in _TRANSCRIPT_DOMAIN_PATTERNS:
        if pattern.search(transcript_text):
            return suite_name
    return None


_ARTIFACT_ID_RE = re.compile(
    r"\b((?:ADR|REV|TC|GAP|SPEC|DEC|REQ|CHK|[FEICRT])-\d+|E-[a-z0-9_-]+)\b"
)
_ARTIFACT_ID_SECTIONS: tuple[str, ...] = (
    "findings",
    "evidence",
    "contracts",
    "issues",
    "decisions",
    "test_cases",
    "interview_decisions",
    "requirements",
    "convergence_checks",
)


def _collect_section_id_refs(
    transcript_text: str, section: str, items: object
) -> tuple[list[str], bool]:
    if not isinstance(items, list):
        return [], False
    refs: list[str] = []
    has_candidates = False
    for item in items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not item_id:
            continue
        has_candidates = True
        token_re = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(item_id)}(?![A-Za-z0-9_-])"
        )
        if token_re.search(transcript_text):
            refs.append(f"{section}:{item_id}")
    return refs, has_candidates


def _collect_boundary_refs(
    transcript_text: str, boundaries: object
) -> tuple[list[str], bool]:
    if not isinstance(boundaries, list):
        return [], False
    refs: list[str] = []
    has_candidates = False
    for boundary in boundaries:
        bname = boundary.get("name") if isinstance(boundary, dict) else None
        if not isinstance(bname, str) or not bname:
            continue
        has_candidates = True
        if bname in transcript_text:
            refs.append(f"boundaries:{bname}")
    return refs, has_candidates


def _extract_artifact_references(
    transcript_text: str, audit_data: dict[str, Any] | None
) -> list[str]:
    if not audit_data:
        return sorted(set(_ARTIFACT_ID_RE.findall(transcript_text)))

    ledger_refs: list[str] = []
    has_candidates = False
    for section in _ARTIFACT_ID_SECTIONS:
        sec_refs, sec_cands = _collect_section_id_refs(
            transcript_text, section, audit_data.get(section)
        )
        ledger_refs.extend(sec_refs)
        has_candidates = has_candidates or sec_cands

    bound_refs, bound_cands = _collect_boundary_refs(
        transcript_text, audit_data.get("boundaries")
    )
    ledger_refs.extend(bound_refs)
    has_candidates = has_candidates or bound_cands

    if not ledger_refs and not has_candidates:
        return sorted(set(_ARTIFACT_ID_RE.findall(transcript_text)))
    return ledger_refs


_CORRELATED_DOMAIN_SUITES = frozenset(
    {
        "software-architecture",
        "code-review",
        "automated-testing",
        "spec-driven-development",
    }
)


def _determine_correlation_mode(
    transcript_text: str,
    audit_data: dict[str, Any] | None,
    run_suite: str | None,
) -> tuple[str | None, bool]:
    artifact_suite = _detect_artifact_suite(audit_data)
    if artifact_suite is not None:
        return artifact_suite, False
    if isinstance(audit_data, dict) and "run_id" in audit_data:
        return None, True
    if run_suite is not None:
        return None, run_suite == "repository-audits"
    inferred_domain = _detect_transcript_domain_suite(transcript_text)
    if inferred_domain is not None:
        return inferred_domain, False
    return None, True


def _find_specialist_in_turns(turns: Iterable[ParsedTurn]) -> str | None:
    for turn in turns:
        cand = _find_specialist_in_segment(turn.text, prefer_first=False)
        if cand:
            return cand
    return None


def _find_specialist_in_user_turns(before_turns: list[ParsedTurn]) -> str | None:
    for turn in reversed(before_turns):
        if turn.role == "user":
            prefer_first = "in order" in turn.text.lower()
            cand = _find_specialist_in_segment(turn.text, prefer_first=prefer_first)
            if cand:
                return cand
    return None


def _find_topic_in_turns(turns: Iterable[ParsedTurn]) -> tuple[str, str] | None:
    for turn in turns:
        kw_match = _find_topic_keyword_in_segment(turn.text)
        if kw_match:
            return kw_match
    return None


def _find_topic_in_before_turns(
    before_turns: list[ParsedTurn],
) -> tuple[str, str] | None:
    non_user_topics = (t for t in reversed(before_turns) if t.role != "user")
    if kw_match := _find_topic_in_turns(non_user_topics):
        return kw_match
    return _find_topic_in_turns(reversed(before_turns))


def _correlate_structured_turns(
    turns: list[ParsedTurn], cutoff: int, is_audit_run: bool
) -> tuple[str | None, str | None]:
    before_turns = [t for t in turns if t.line_number <= cutoff]
    after_turns = [t for t in turns if t.line_number > cutoff]

    non_user_before = (t for t in reversed(before_turns) if t.role != "user")
    if cand := _find_specialist_in_turns(non_user_before):
        return cand, None
    if cand := _find_specialist_in_user_turns(before_turns):
        return cand, None
    if cand := _find_specialist_in_turns(after_turns):
        return cand, None
    if is_audit_run and (kw_match := _find_topic_in_before_turns(before_turns)):
        return kw_match
    return None, None


def _correlate_plaintext_lines(
    lines: list[str], cutoff: int, is_audit_run: bool
) -> tuple[str | None, str | None]:
    for idx in range(cutoff, 0, -1):
        if cand := _find_specialist_in_segment(lines[idx - 1], prefer_first=False):
            return cand, None
    for idx in range(cutoff + 1, len(lines) + 1):
        if cand := _find_specialist_in_segment(lines[idx - 1], prefer_first=False):
            return cand, None
    if is_audit_run:
        for idx in range(cutoff, 0, -1):
            if kw_match := _find_topic_keyword_in_segment(lines[idx - 1]):
                return kw_match
    return None, None


def _resolve_correlated_topic(
    specialist_found: str | None,
    topic_found: str | None,
    artifact_suite: str | None,
    run_suite: str | None,
) -> str | None:
    if topic_found:
        return topic_found
    if specialist_found:
        return _topic_for_specialist(specialist_found)
    effective_suite = (
        artifact_suite
        if artifact_suite in _CORRELATED_DOMAIN_SUITES
        else (run_suite if run_suite in _CORRELATED_DOMAIN_SUITES else None)
    )
    if effective_suite is None:
        return None
    if effective_suite == "software-architecture":
        return "architecture"
    if effective_suite == "spec-driven-development":
        return "spec"
    return effective_suite


def correlate_audit_domain(
    transcript_text: str,
    audit_data: dict[str, Any] | None = None,
    failure_line: int | None = None,
    run_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Correlate failed transcript steps with the most recently active specialist skill."""
    lines = transcript_text.splitlines()
    if failure_line is None:
        failure_line = detect_failures(transcript_text).get("line_number") or len(lines)
    cutoff = max(1, min(failure_line, len(lines))) if lines else 1

    run_suite = _suite_from_run_data(run_data)
    artifact_suite, is_audit_run = _determine_correlation_mode(
        transcript_text, audit_data, run_suite
    )

    turns, is_structured = parse_transcript_turns(transcript_text)
    if is_structured and turns:
        specialist_found, topic_found = _correlate_structured_turns(
            turns, cutoff, is_audit_run
        )
    else:
        specialist_found, topic_found = _correlate_plaintext_lines(
            lines, cutoff, is_audit_run
        )

    resolved_topic = _resolve_correlated_topic(
        specialist_found, topic_found, artifact_suite, run_suite
    )
    ledger_refs = _extract_artifact_references(transcript_text, audit_data)

    return {
        "specialist_skill": specialist_found,
        "topic": resolved_topic,
        "ledger_references": ledger_refs,
    }


def _suite_artifact_triple(suite_name: str) -> tuple[str, str, str]:
    fname, schema_rel = _SUITE_ARTIFACTS.get(
        suite_name, _SUITE_ARTIFACTS["repository-audits"]
    )
    return suite_name, fname, schema_rel


def _find_workspace_domain_suite(
    workspace_dir: PurePath | None,
) -> tuple[str, str, str] | None:
    if not isinstance(workspace_dir, Path) or not workspace_dir.is_dir():
        return None
    for fname, (sname, schema_rel) in _DOMAIN_ARTIFACTS.items():
        if (workspace_dir / fname).is_file():
            return sname, fname, schema_rel
    return None


def _has_workspace_audit_ledger(workspace_dir: PurePath | None) -> bool:
    return (
        isinstance(workspace_dir, Path)
        and workspace_dir.is_dir()
        and (workspace_dir / "audit.json").is_file()
    )


def _resolve_recovery_suite(
    run_data: dict[str, Any] | None,
    workspace_dir: PurePath | None,
    specialist_skill: str | None,
) -> tuple[str, str, str]:
    if (workspace_dir is None and run_data is None) or _has_workspace_audit_ledger(
        workspace_dir
    ):
        return _suite_artifact_triple("repository-audits")
    run_suite = _suite_from_run_data(run_data)
    if run_suite is not None:
        return _suite_artifact_triple(run_suite)
    if ws_suite := _find_workspace_domain_suite(workspace_dir):
        return ws_suite
    if specialist_skill and specialist_skill in _SKILL_TO_SUITE:
        return _suite_artifact_triple(_SKILL_TO_SUITE[specialist_skill])
    return _suite_artifact_triple("repository-audits")


def _escape_inline_char(ch: str) -> str:
    if ch == "\n":
        return r"\n"
    if ch == "\r":
        return r"\r"
    if ch == "\t":
        return r"\t"
    if ch == "\x00":
        return r"\x00"
    return ch


def _inline_code_span(value: object) -> str:
    sanitized = "".join(_escape_inline_char(ch) for ch in str(value))
    if not sanitized:
        return "` `"
    max_ticks = max(
        (len(match.group(0)) for match in re.finditer(r"`+", sanitized)),
        default=0,
    )
    fence = "`" * (max_ticks + 1)
    needs_pad = (
        sanitized.startswith("`")
        or sanitized.endswith("`")
        or (
            sanitized.startswith(" ")
            and sanitized.endswith(" ")
            and bool(sanitized.strip(" "))
        )
    )
    inner = f" {sanitized} " if needs_pad else sanitized
    return f"{fence}{inner}{fence}"


def _fenced_code_block(value: object) -> str:
    raw = str(value).replace("\r\n", "\n").replace("\r", "\n")
    max_ticks = max(
        (len(match.group(0)) for match in re.finditer(r"`+", raw)),
        default=0,
    )
    fence = "`" * max(3, max_ticks + 1)
    return f"{fence}\n{raw}\n{fence}"


def _sanitize_report_inline(value: object) -> str:
    single = " ".join(str(value).splitlines())
    single = single.replace("<", "&lt;").replace(">", "&gt;")
    single = re.sub(r"^(\s*)(#+)", r"\1\\\2", single)
    if "`" in single:
        single = single.replace("`", r"\`")
    if "~~~" in single:
        single = single.replace("~~~", r"\~~~")
    return single


_SAFE_POSIX_ARG_RE = re.compile(r"\A[A-Za-z0-9_@%+=:,./-][A-Za-z0-9_@%+=:,./~-]*\Z")
_SAFE_WINDOWS_ARG_RE = re.compile(
    r"\A[A-Za-z0-9_@%+=:,./\\-][A-Za-z0-9_@%+=:,./\\~-]*\Z"
)


def _format_workspace_arg(
    workspace_dir: PurePath | None, placeholder: str, subpath: str | None = None
) -> str:
    if workspace_dir is None:
        return f"{placeholder}/{subpath}" if subpath else placeholder
    raw = f"{workspace_dir}/{subpath}" if subpath else str(workspace_dir)
    safe_re = (
        _SAFE_WINDOWS_ARG_RE
        if isinstance(workspace_dir, PureWindowsPath)
        else _SAFE_POSIX_ARG_RE
    )
    if safe_re.match(raw):
        return raw
    return shlex.quote(raw)


class _RecoveryContext(NamedTuple):
    suite_name: str
    deliverable: str
    schema_rel: str
    is_domain_suite: bool
    dir_str: str
    deliverable_arg: str
    audit_arg: str
    run_json_arg: str
    matched_span: str
    line_number: Any


def _format_last_checkpoint(run_data: dict[str, Any] | None) -> str | None:
    if not run_data:
        return None
    checkpoints = run_data.get("checkpoints")
    if checkpoints:
        last_ckpt = checkpoints[-1]
        return (
            f"{last_ckpt.get('state', 'unknown')}: "
            f"{last_ckpt.get('note', '')} (at {last_ckpt.get('at', '')})"
        )
    if run_data.get("state"):
        return f"Run state: {run_data['state']}"
    return None


def _recovery_steps_timeout(ctx: _RecoveryContext) -> list[str]:
    step4 = (
        f"4. Validate {ctx.deliverable} and resume {ctx.suite_name} workflow: python scripts/fred.py validate-run {ctx.dir_str} && python scripts/fred.py resume {ctx.dir_str}"
        if ctx.is_domain_suite
        else f"4. Re-run specialist with bounded scope or resume: python scripts/fred.py resume {ctx.dir_str}"
    )
    return [
        f"1. Check the command that timed out at line {ctx.line_number}: {ctx.matched_span}.",
        "2. Add file exclusion patterns or narrow the target scope in the tool arguments.",
        f"3. Mark current state paused: python scripts/fred.py checkpoint {ctx.dir_str} --state paused --note 'Paused after tool timeout triage'.",
        step4,
    ]


def _recovery_steps_schema(ctx: _RecoveryContext) -> list[str]:
    step1 = f"1. Inspect the schema validation error at line {ctx.line_number}: {ctx.matched_span}."
    if ctx.is_domain_suite:
        return [
            step1,
            f"2. Validate {ctx.deliverable} against {ctx.schema_rel}: python scripts/fred.py validate-run {ctx.dir_str}",
            f"3. Correct missing required properties, invalid enum values, duplicate IDs, unsafe relative paths, or undeclared keys in {ctx.deliverable_arg} (additionalProperties is false).",
            f"4. Re-verify {ctx.deliverable} conformance, then resume {ctx.suite_name} run: python scripts/fred.py resume {ctx.dir_str}",
        ]
    return [
        step1,
        f"2. Validate current audit.json ledger: python skills/repo-audit-program/scripts/validate_audit.py {ctx.audit_arg}",
        "3. Correct duplicate IDs, reversed line ranges, or missing required fields in audit.json.",
        f"4. Re-verify ledger validity, then resume audit coordination: python scripts/fred.py resume {ctx.dir_str}",
    ]


def _recovery_steps_context_limit(ctx: _RecoveryContext) -> list[str]:
    step3 = (
        f"3. Persist completed entries in {ctx.deliverable_arg} and split remaining {ctx.suite_name} phases into smaller slices."
        if ctx.is_domain_suite
        else "3. Split the specialist analysis into smaller bounded workflow slices."
    )
    return [
        f"1. Context limit reached at line {ctx.line_number}.",
        f"2. Save current progress to checkpoint: python scripts/fred.py checkpoint {ctx.dir_str} --state paused --note 'Context limit reached; clearing context'.",
        step3,
        f"4. Start fresh agent session and resume from checkpoint: python scripts/fred.py resume {ctx.dir_str}",
    ]


def _recovery_steps_unclassified(ctx: _RecoveryContext) -> list[str]:
    step1 = f"1. Review the unclassified error at line {ctx.line_number}: {ctx.matched_span}."
    if not ctx.is_domain_suite:
        return [
            step1,
            f"2. Inspect {ctx.run_json_arg} and {ctx.audit_arg} for partial progress.",
            f"3. Resume audit run from the last valid checkpoint: python scripts/fred.py resume {ctx.dir_str}",
        ]
    step3 = (
        f"3. Confirm `--allow-fixes` authority in {ctx.run_json_arg} if writing runnable test files, then validate and resume: python scripts/fred.py validate-run {ctx.dir_str} && python scripts/fred.py resume {ctx.dir_str}"
        if ctx.suite_name == "automated-testing"
        else f"3. Validate {ctx.deliverable} and resume {ctx.suite_name} run from the last valid checkpoint: python scripts/fred.py validate-run {ctx.dir_str} && python scripts/fred.py resume {ctx.dir_str}"
    )
    return [
        step1,
        f"2. Inspect {ctx.run_json_arg} and {ctx.deliverable_arg} (schema: {ctx.schema_rel}) for partial progress.",
        step3,
    ]


def _build_recovery_steps(ftype: Any, ctx: _RecoveryContext) -> list[str]:
    if ftype == "tool_timeout":
        return _recovery_steps_timeout(ctx)
    if ftype == "schema_validation_error":
        return _recovery_steps_schema(ctx)
    if ftype == "context_limit":
        return _recovery_steps_context_limit(ctx)
    return _recovery_steps_unclassified(ctx)


def formulate_recovery_instructions(
    failure: dict[str, Any],
    run_data: dict[str, Any] | None = None,
    workspace_dir: PurePath | None = None,
    specialist_skill: str | None = None,
) -> dict[str, Any]:
    """Generate concrete recovery steps to resume a run from the last valid checkpoint."""
    suite_name, deliverable, schema_rel = _resolve_recovery_suite(
        run_data, workspace_dir, specialist_skill
    )
    is_domain_suite = deliverable != "audit.json"
    placeholder = "<run-dir>" if is_domain_suite else "<audit-run-dir>"
    ctx = _RecoveryContext(
        suite_name=suite_name,
        deliverable=deliverable,
        schema_rel=schema_rel,
        is_domain_suite=is_domain_suite,
        dir_str=_format_workspace_arg(workspace_dir, placeholder),
        deliverable_arg=_format_workspace_arg(workspace_dir, placeholder, deliverable),
        audit_arg=_format_workspace_arg(workspace_dir, placeholder, "audit.json"),
        run_json_arg=_format_workspace_arg(workspace_dir, placeholder, "run.json"),
        matched_span=_inline_code_span(failure.get("matched_line", "")),
        line_number=failure.get("line_number"),
    )

    instructions = _build_recovery_steps(failure.get("failure_type"), ctx)
    if suite_name == "automated-testing" and not any(
        "--allow-fixes" in step for step in instructions
    ):
        instructions.append(
            f"{len(instructions) + 1}. Confirm `--allow-fixes` authority in {ctx.run_json_arg} (`application_changes: true`) before writing runnable test files to the repository."
        )

    return {
        "last_checkpoint": _format_last_checkpoint(run_data),
        "instructions": instructions,
    }


def _infer_transcript_source(transcript_path: Path) -> str:
    parts_lower = [p.lower() for p in transcript_path.parts]
    if ".claude" in parts_lower and "projects" in parts_lower:
        return "claude-code"
    if ".codex" in parts_lower and "sessions" in parts_lower:
        return "codex"
    return "explicit"


def _find_cited_lines_for_bundle(
    raw_text: str, failure: dict[str, Any], correlated: dict[str, Any]
) -> set[int]:
    cited: set[int] = set()
    fline = failure.get("line_number")
    if isinstance(fline, int) and fline >= 1:
        cited.add(fline)
    raw_refs = [
        ref.split(":", 1)[-1] for ref in correlated.get("ledger_references", []) if ref
    ]
    if raw_refs:
        for idx, line in enumerate(raw_text.splitlines(), start=1):
            if any(ref in line for ref in raw_refs):
                cited.add(idx)
    return cited


def _read_workspace_json_dict(
    path: Path, label: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Read an optional workspace JSON object, logging a scrubbed warning on corruption or I/O failure."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        reason = f"unable to read workspace {label} '{path}': {exc}"
        print(scrub_credentials(f"Warning: {reason}"), file=sys.stderr)
        return None, reason
    try:
        loaded = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        reason = (
            f"malformed JSON in workspace {label} '{path}' "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        )
        print(scrub_credentials(f"Warning: {reason}"), file=sys.stderr)
        return None, reason
    if not isinstance(loaded, dict):
        reason = (
            f"expected JSON object in workspace {label} '{path}', "
            f"got {type(loaded).__name__}"
        )
        print(scrub_credentials(f"Warning: {reason}"), file=sys.stderr)
        return None, reason
    return loaded, None


def _load_first_workspace_artifact(
    workspace_dir: Path, expected_artifact: str, warnings: list[str]
) -> dict[str, Any] | None:
    candidate_files = [expected_artifact] + [
        name for name in ("audit.json", *_DOMAIN_ARTIFACTS) if name != expected_artifact
    ]
    for fname in candidate_files:
        artifact_file = workspace_dir / fname
        if not artifact_file.is_file():
            continue
        loaded_artifact, art_warn = _read_workspace_json_dict(artifact_file, fname)
        if loaded_artifact is not None:
            return loaded_artifact
        if art_warn:
            warnings.append(art_warn)
    return None


def _load_workspace_bundle_inputs(
    workspace_dir: Path | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    if not workspace_dir:
        return None, None, []
    warnings: list[str] = []
    run_data: dict[str, Any] | None = None
    run_file = workspace_dir / "run.json"
    if run_file.is_file():
        run_data, run_warn = _read_workspace_json_dict(run_file, "run.json")
        if run_warn:
            warnings.append(run_warn)
    _, expected_artifact, _ = _resolve_recovery_suite(run_data, workspace_dir, None)
    audit_data = _load_first_workspace_artifact(
        workspace_dir, expected_artifact, warnings
    )
    return run_data, audit_data, warnings


def _normalize_discovery_metadata(
    transcript_path: Path, discovery_metadata: dict[str, Any] | None
) -> dict[str, Any]:
    if discovery_metadata is None:
        return {
            "source": _infer_transcript_source(transcript_path),
            "selected_transcript": scrub_credentials(str(transcript_path)),
            "selection_reason": "Explicit transcript path supplied",
            "rejected_candidates": [],
        }
    return {
        "source": str(discovery_metadata.get("source", "workspace")),
        "selected_transcript": scrub_credentials(
            str(discovery_metadata.get("selected_transcript", transcript_path))
        ),
        "selection_reason": scrub_credentials(
            str(discovery_metadata.get("selection_reason", "matched transcript"))
        ),
        "rejected_candidates": [
            {
                "path": scrub_credentials(str(item.get("path", ""))),
                "reason": scrub_credentials(str(item.get("reason", ""))),
            }
            for item in discovery_metadata.get("rejected_candidates", [])
        ],
    }


def _extract_run_repo_path(run_data: dict[str, Any] | None) -> str | None:
    if not isinstance(run_data, dict):
        return None
    repo = run_data.get("repository")
    if isinstance(repo, dict) and isinstance(repo.get("path"), str):
        return repo["path"]
    return None


def _finalize_recovery_with_warnings(
    recovery: dict[str, Any], workspace_warnings: list[str]
) -> dict[str, Any]:
    for warn in workspace_warnings:
        recovery["instructions"].append(
            f"{len(recovery['instructions']) + 1}. Repair corrupted workspace metadata before resuming ({warn})."
        )
    recovery["instructions"] = [
        scrub_credentials(inst) for inst in recovery.get("instructions", [])
    ]
    if recovery.get("last_checkpoint"):
        recovery["last_checkpoint"] = scrub_credentials(recovery["last_checkpoint"])
    return recovery


def generate_diagnostic_bundle(
    transcript_path: Path,
    workspace_dir: Path | None = None,
    session_id: str | None = None,
    redaction_tier: str = "full",
    discovery_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a scrubbed diagnostic bundle from a session transcript."""
    _validate_redaction_tier(redaction_tier)
    raw_text = transcript_path.read_text(encoding="utf-8", errors="replace")
    run_data, audit_data, workspace_warnings = _load_workspace_bundle_inputs(
        workspace_dir
    )

    run_id = run_data.get("run_id") if isinstance(run_data, dict) else None
    resolved_session_id = session_id or run_id or transcript_path.stem

    failure = detect_failures(raw_text)
    correlated = correlate_audit_domain(
        raw_text,
        audit_data,
        failure_line=failure.get("line_number"),
        run_data=run_data,
    )

    failure["matched_line"] = scrub_credentials(failure.get("matched_line", ""))
    failure["summary"] = scrub_credentials(failure.get("summary", ""))

    recovery = formulate_recovery_instructions(
        failure,
        run_data,
        workspace_dir,
        specialist_skill=correlated.get("specialist_skill"),
    )
    recovery = _finalize_recovery_with_warnings(recovery, workspace_warnings)

    cited_lines = _find_cited_lines_for_bundle(raw_text, failure, correlated)
    scrubbed_transcript, redaction_info = scrub_credentials_with_stats(
        raw_text, tier=redaction_tier, cited_lines=cited_lines
    )

    return {
        "schema_version": "1.0",
        "session_id": resolved_session_id,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "failure_classification": failure,
        "correlated_domain": correlated,
        "environment": {
            "platform": f"{platform.system()} {platform.release()}",
            "python_version": sys.version.split()[0],
            "repository_path": _extract_run_repo_path(run_data),
            "run_id": run_id,
        },
        "recovery": recovery,
        "scrubbed_transcript": scrubbed_transcript,
        "redaction": redaction_info,
        "transcript_discovery": _normalize_discovery_metadata(
            transcript_path, discovery_metadata
        ),
    }


def render_report(bundle: dict[str, Any]) -> str:
    """Format human-readable markdown report from bundle."""
    fc = bundle["failure_classification"]
    cd = bundle.get("correlated_domain", {})
    rec = bundle.get("recovery", {})
    red = bundle.get("redaction", {})
    disc = bundle.get("transcript_discovery", {})

    instructions_md = "\n".join(rec.get("instructions", []))
    ledger_refs = ", ".join(cd.get("ledger_references", [])) or "None identified"

    tier = red.get("tier", "full")
    creds_redacted = red.get("credentials_redacted", 0)
    tools_omitted = red.get("tool_outputs_omitted", 0)
    total_redactions = red.get("total_redactions", creds_redacted + tools_omitted)

    discovery_section = ""
    if disc:
        rejected = disc.get("rejected_candidates", [])
        if rejected:
            rejected_md = "\n".join(
                f"  - {_inline_code_span(item.get('path', ''))} — {_sanitize_report_inline(item.get('reason', ''))}"
                for item in rejected
            )
        else:
            rejected_md = "  - None"
        selected_span = _inline_code_span(disc.get("selected_transcript", "unknown"))
        sel_reason = _sanitize_report_inline(disc.get("selection_reason", "unknown"))
        discovery_section = f"""
## Transcript discovery

- **Source:** {_sanitize_report_inline(disc.get("source", "unknown"))}
- **Selected Transcript:** {selected_span}
- **Selection Reason:** {sel_reason}
- **Rejected Candidates:**
{rejected_md}
"""

    matched_block = _fenced_code_block(fc.get("matched_line", ""))
    summary_text = _sanitize_report_inline(fc.get("summary", ""))
    session_id_text = _sanitize_report_inline(bundle.get("session_id", "unknown"))

    return f"""# Diagnostic Triage Report

- **Session / Run ID:** {session_id_text}
- **Timestamp:** {bundle.get("timestamp", "unknown")}
- **Failure Classification:** {fc.get("failure_type", "unknown")}
- **Root Cause Line:** {fc.get("line_number", "unknown")}
- **Correlated Domain:** {cd.get("specialist_skill") or "None identified"}

## Summary of failure

{summary_text}

{matched_block}

## Audit domain and ledger correlation

- **Specialist Skill:** {cd.get("specialist_skill") or "None identified"}
- **Domain Topic:** {cd.get("topic") or "None identified"}
- **Correlated Ledger Entries:** {ledger_refs}
{discovery_section}
## Actionable recovery instructions

{instructions_md}

## Sanitization status

- **Redaction Tier:** {tier}
- **Credentials Redacted:** {creds_redacted}
- **Tool Outputs Omitted:** {tools_omitted}
- **Total Redactions:** {total_redactions}

All private credentials, tokens, authorization headers, URI passwords, and environment secrets
have been redacted from this bundle.
"""


def validate_bundle(bundle: dict[str, Any], schema_path: Path | None = None) -> None:
    """Validate generated bundle against bundle.schema.json."""
    schema_file = (
        schema_path or Path(__file__).resolve().parents[1] / "assets/bundle.schema.json"
    )
    if not schema_file.is_file():
        raise ValueError(f"Bundle schema not found at {schema_file}")
    try:
        import jsonschema

        jsonschema.validate(bundle, json.loads(schema_file.read_text(encoding="utf-8")))
    except ImportError:
        raise ValueError(
            "jsonschema not installed, cannot validate bundle schema"
        ) from None
    except (
        ValueError,
        OSError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
    ) as e:
        raise ValueError(f"Schema validation error: {e}") from e


CANDIDATE_TRANSCRIPTS: tuple[str, ...] = (
    "transcript.jsonl",
    "transcript.json",
    "session-transcript.jsonl",
    "transcript.txt",
    "session.json",
)


class TranscriptNotFoundError(FileNotFoundError, ValueError):
    """Raised when no candidate transcript can be discovered in workspace or host directories."""

    def __init__(
        self,
        message: str,
        rejected_candidates: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.rejected_candidates: list[dict[str, str]] = rejected_candidates or []


def _resolve_home_dir(home_dir: Path | None = None) -> Path:
    """Resolve user home directory honoring explicit override, HOME, and USERPROFILE."""
    if home_dir is not None:
        return Path(home_dir)
    env_home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if env_home:
        return Path(env_home)
    return Path.home()


def _canonical_path_variants(raw_path: str | Path) -> set[str]:
    """Generate comparable path strings across symlinks and POSIX/Windows separators."""
    s = str(raw_path).strip()
    if not s:
        return set()
    variants = {s.replace("\\", "/").rstrip("/")}
    if os.name == "nt" or re.match(r"^[a-zA-Z]:[\\/]", s):
        variants.add(s.replace("\\", "/").rstrip("/").lower())
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        p = Path(s)
        if p.exists():
            resolved = str(p.resolve()).replace("\\", "/").rstrip("/")
            variants.add(resolved)
            if os.name == "nt":
                variants.add(resolved.lower())
    return {v for v in variants if v}


def _paths_equal(a: str | Path, b: str | Path) -> bool:
    va = _canonical_path_variants(a)
    vb = _canonical_path_variants(b)
    return bool(va and vb and (va & vb))


def _claude_project_slugs(raw_path: str | Path) -> set[str]:
    slugs: set[str] = set()
    for variant in _canonical_path_variants(raw_path):
        slugs.add(re.sub(r"[\\/:]+", "-", variant).strip("-").lower())
        slugs.add(re.sub(r"[^a-zA-Z0-9-]+", "-", variant).strip("-").lower())
    return {s for s in slugs if s}


class CandidateScore(NamedTuple):
    """Comparable priority score for ranking workspace and host transcript candidates."""

    match_tier: int
    is_workspace: int
    run_or_session_match: int
    workspace_or_cwd_match: int
    repo_or_slug_match: int
    primary_session: int
    mtime: float
    name_rank: int
    root_rank: int


class _ScoredCandidate(NamedTuple):
    score: CandidateScore
    path: Path
    source: str
    reason: str


class _HostMatchContext(NamedTuple):
    workspace_dir: Path | None
    session_id: str | None
    resolved_home: Path
    run_data: dict[str, Any]
    run_parse_warning: str | None
    run_id: str | None
    repo_path: str | None
    expected_slugs: set[str]
    ws_variants: set[str]
    repo_variants: set[str]
    has_workspace_match: bool


_METADATA_CWD_KEYS: tuple[str, ...] = (
    "cwd",
    "workdir",
    "project_path",
    "repository_path",
)


def _read_candidate_text(path: Path) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        print(
            scrub_credentials(
                f"Warning: unable to read candidate transcript '{path}': {exc}"
            ),
            file=sys.stderr,
        )
        return "", str(exc)


def _add_nonempty_str(target: set[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        target.add(value.strip())


def _collect_record_session_ids(obj: dict[str, Any], session_ids: set[str]) -> None:
    for id_key in ("sessionId", "session_id"):
        _add_nonempty_str(session_ids, obj.get(id_key))
    payload = obj.get("payload")
    if obj.get("type") == "session_meta" and isinstance(payload, dict):
        _add_nonempty_str(session_ids, payload.get("id"))
    elif obj.get("type") in {"session", "metadata", "", None}:
        _add_nonempty_str(session_ids, obj.get("id"))
    meta = obj.get("metadata")
    if isinstance(meta, dict):
        _add_nonempty_str(session_ids, meta.get("session_id"))


def _collect_mapping_cwds(mapping: dict[str, Any], cwds: list[str]) -> None:
    for cwd_key in _METADATA_CWD_KEYS:
        cval = mapping.get(cwd_key)
        if isinstance(cval, str) and cval.strip() and cval.strip() not in cwds:
            cwds.append(cval.strip())


def _inspect_jsonl_record(
    stripped: str, session_ids: set[str], cwds: list[str]
) -> bool:
    if not stripped.startswith("{"):
        return False
    obj: Any = None
    with contextlib.suppress(ValueError, TypeError):
        obj = json.loads(stripped)
    if not isinstance(obj, dict):
        return False
    _collect_record_session_ids(obj, session_ids)
    _collect_mapping_cwds(obj, cwds)
    payload = obj.get("payload")
    if isinstance(payload, dict):
        _collect_mapping_cwds(payload, cwds)
    return obj.get("isSidechain") is True


def _inspect_transcript_metadata(path: Path) -> dict[str, Any]:
    """Extract session IDs, working directories, sidechain flag, and text sample from a candidate."""
    session_ids: set[str] = {path.stem}
    cwds: list[str] = []
    is_sidechain = "subagents" in {p.lower() for p in path.parts}
    raw_text, read_error = _read_candidate_text(path)

    for line in raw_text.splitlines():
        if _inspect_jsonl_record(line.strip(), session_ids, cwds):
            is_sidechain = True

    return {
        "session_ids": session_ids,
        "cwds": cwds,
        "is_sidechain": is_sidechain,
        "raw_text": raw_text,
        "read_error": read_error,
    }


def _matches_session_id(
    path: Path, meta: dict[str, Any], target_session_id: str
) -> tuple[bool, str | None]:
    sid = target_session_id.strip()
    if not sid:
        return False, None
    if sid in meta["session_ids"] or path.stem == sid or path.stem.endswith(f"-{sid}"):
        return True, f"Matched session_id '{sid}'"
    if len(sid) >= 6 and sid in path.name:
        return True, f"Matched session_id '{sid}' in filename"
    if sid in meta["raw_text"]:
        return True, f"Matched session_id '{sid}' in transcript content"
    return False, None


def find_candidate_transcripts(workspace_dir: Path) -> list[Path]:
    """Scan candidate transcript files in workspace directory and immediate logs subdirectory."""
    if not workspace_dir.is_dir():
        return []

    search_dirs: list[Path] = [workspace_dir]
    logs_dir = workspace_dir / "logs"
    if logs_dir.is_dir():
        search_dirs.append(logs_dir)

    candidates: list[Path] = []
    for directory in search_dirs:
        for name in CANDIDATE_TRANSCRIPTS:
            candidate = directory / name
            if candidate.is_file():
                candidates.append(candidate)
    return candidates


def _iter_host_root_files(host_name: str, root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        return []
    paths: list[Path] = []
    with contextlib.suppress(OSError):
        paths = sorted(root.rglob("*"))
    return [
        (host_name, path)
        for path in paths
        if path.is_file() and path.suffix.lower() in {".jsonl", ".json"}
    ]


def _find_host_transcript_files(home_dir: Path) -> list[tuple[str, Path]]:
    """Collect candidate JSONL/JSON session transcripts from Claude Code and Codex host directories."""
    results: list[tuple[str, Path]] = []
    host_roots = (
        ("claude-code", home_dir / ".claude" / "projects"),
        ("codex", home_dir / ".codex" / "sessions"),
    )
    for host_name, root in host_roots:
        results.extend(_iter_host_root_files(host_name, root))
    return results


def _append_session_named_candidates(
    extra_dir: Path, session_id: str, candidates: list[Path]
) -> None:
    if not extra_dir.is_dir():
        return
    for ext in (".jsonl", ".json", ".txt"):
        extra = extra_dir / f"{session_id}{ext}"
        if extra.is_file() and extra not in candidates:
            candidates.append(extra)


def _collect_workspace_candidates(
    workspace_dir: Path | None, session_id: str | None
) -> list[Path]:
    if not workspace_dir or not workspace_dir.is_dir():
        return []
    candidates = list(find_candidate_transcripts(workspace_dir))
    if session_id:
        _append_session_named_candidates(workspace_dir, session_id, candidates)
        _append_session_named_candidates(workspace_dir / "logs", session_id, candidates)
    return candidates


def _stat_candidate_mtime(
    cand: Path, meta: dict[str, Any], rejected: list[dict[str, str]]
) -> float | None:
    if meta.get("read_error"):
        rejected.append(
            {
                "path": str(cand),
                "reason": f"unreadable transcript ({meta['read_error']})",
            }
        )
        return None
    try:
        return cand.stat().st_mtime
    except OSError as exc:
        print(
            scrub_credentials(
                f"Warning: unable to stat candidate transcript '{cand}': {exc}"
            ),
            file=sys.stderr,
        )
        rejected.append({"path": str(cand), "reason": f"unreadable transcript ({exc})"})
        return None


def _score_workspace_candidate(
    cand: Path,
    workspace_dir: Path | None,
    session_id: str | None,
    rejected: list[dict[str, str]],
) -> _ScoredCandidate | None:
    meta = _inspect_transcript_metadata(cand)
    mtime = _stat_candidate_mtime(cand, meta, rejected)
    if mtime is None:
        return None

    name_rank = (
        -CANDIDATE_TRANSCRIPTS.index(cand.name)
        if cand.name in CANDIDATE_TRANSCRIPTS
        else -len(CANDIDATE_TRANSCRIPTS)
    )
    root_rank = 0 if (workspace_dir and cand.parent == workspace_dir) else -1

    if session_id:
        matched_sid, sid_reason = _matches_session_id(cand, meta, session_id)
        if not matched_sid:
            rejected.append(
                {
                    "path": str(cand),
                    "reason": f"session_id mismatch (expected '{session_id}')",
                }
            )
            return None
        score = CandidateScore(3, 1, 1, 1, 1, 1, mtime, name_rank, root_rank)
        return _ScoredCandidate(
            score, cand, "workspace", sid_reason or "Matched session_id"
        )

    score = CandidateScore(2, 1, 1, 1, 1, 1, mtime, name_rank, root_rank)
    return _ScoredCandidate(
        score, cand, "workspace", f"Matched workspace candidate '{cand.name}'"
    )


def _matches_claude_project_slug(
    host_name: str, hpath: Path, resolved_home: Path, expected_slugs: set[str]
) -> bool:
    if host_name != "claude-code" or not expected_slugs:
        return False
    projects_root = resolved_home / ".claude" / "projects"
    with contextlib.suppress(ValueError):
        rel_parts = hpath.relative_to(projects_root).parts
        if rel_parts:
            return rel_parts[0].strip("-").lower() in expected_slugs
    return False


def _text_mentions_any_variant(raw_text: str, variants: set[str]) -> bool:
    if not variants:
        return False
    normalized_text = raw_text.replace("\\", "/")
    return any(len(v) > 3 and v in normalized_text for v in variants)


def _has_conflicting_host_cwd(
    cwds: list[str],
    has_repo_cwd: bool,
    has_ws_cwd: bool,
    has_ws_ref: bool,
    repo_path: str | None,
) -> bool:
    if not cwds or has_repo_cwd or has_ws_cwd:
        return False
    return repo_path is not None or not has_ws_ref


def _format_host_no_match_reason(ctx: _HostMatchContext) -> str:
    if ctx.run_parse_warning:
        return (
            "no matching workspace reference and workspace run.json "
            f"could not be parsed ({ctx.run_parse_warning})"
        )
    if not ctx.run_data:
        return "no matching workspace reference and workspace lacks run.json metadata"
    return (
        f"no matching run_id ({ctx.run_id or 'none'}) or "
        f"repository path ({ctx.repo_path or 'none'})"
    )


def _build_host_match_reasons(
    ctx: _HostMatchContext,
    *,
    has_run_id: bool,
    has_ws_ref: bool,
    has_repo_cwd: bool,
    slug_match: bool,
    has_conflicting_cwd: bool,
    has_repo_ref: bool,
) -> list[str]:
    reasons: list[str] = []
    if has_run_id:
        reasons.append(f"run_id '{ctx.run_id}'")
    if has_ws_ref:
        reasons.append(f"workspace '{ctx.workspace_dir}'")
    if has_repo_cwd:
        reasons.append(f"repository cwd '{ctx.repo_path}'")
    elif slug_match and not has_conflicting_cwd:
        reasons.append(
            f"Claude project directory for '{ctx.repo_path or ctx.workspace_dir}'"
        )
    elif has_repo_ref:
        reasons.append(f"repository path '{ctx.repo_path}'")
    return reasons


def _any_cwd_matches(cwds: list[str], target: str | Path | None) -> bool:
    if target is None:
        return False
    return any(_paths_equal(c, target) for c in cwds)


def _score_host_session_id_candidate(
    hpath: Path,
    host_name: str,
    meta: dict[str, Any],
    mtime: float,
    primary_session: int,
    session_id: str,
    ctx: _HostMatchContext,
    rejected: list[dict[str, str]],
) -> _ScoredCandidate | None:
    matched_sid, sid_reason = _matches_session_id(hpath, meta, session_id)
    if not matched_sid:
        rejected.append(
            {
                "path": str(hpath),
                "reason": f"session_id mismatch (expected '{session_id}')",
            }
        )
        return None
    cwd_bonus = int(_any_cwd_matches(meta["cwds"], ctx.repo_path))
    score = CandidateScore(3, 0, 1, cwd_bonus, 1, primary_session, mtime, 0, 0)
    return _ScoredCandidate(
        score,
        hpath,
        host_name,
        f"{sid_reason} in {host_name} host transcripts",
    )


def _score_host_workspace_candidate(
    hpath: Path,
    host_name: str,
    meta: dict[str, Any],
    mtime: float,
    primary_session: int,
    workspace_dir: Path,
    ctx: _HostMatchContext,
    rejected: list[dict[str, str]],
) -> _ScoredCandidate | None:
    raw_text = meta["raw_text"]
    has_run_id = bool(ctx.run_id and ctx.run_id in raw_text)
    has_ws_cwd = _any_cwd_matches(meta["cwds"], workspace_dir)
    has_ws_ref = has_ws_cwd or _text_mentions_any_variant(raw_text, ctx.ws_variants)
    has_repo_cwd = _any_cwd_matches(meta["cwds"], ctx.repo_path)
    has_conflicting_cwd = _has_conflicting_host_cwd(
        meta["cwds"], has_repo_cwd, has_ws_cwd, has_ws_ref, ctx.repo_path
    )
    if has_conflicting_cwd and not has_run_id and not has_ws_ref:
        target_label = ctx.repo_path or str(workspace_dir)
        rejected.append(
            {
                "path": str(hpath),
                "reason": f"cwd mismatch ({meta['cwds'][0]!r} does not match {target_label!r})",
            }
        )
        return None

    slug_match = _matches_claude_project_slug(
        host_name, hpath, ctx.resolved_home, ctx.expected_slugs
    )
    has_repo_ref = not has_conflicting_cwd and _text_mentions_any_variant(
        raw_text, ctx.repo_variants
    )
    reasons = _build_host_match_reasons(
        ctx,
        has_run_id=has_run_id,
        has_ws_ref=has_ws_ref,
        has_repo_cwd=has_repo_cwd,
        slug_match=slug_match,
        has_conflicting_cwd=has_conflicting_cwd,
        has_repo_ref=has_repo_ref,
    )
    if not reasons:
        rejected.append(
            {"path": str(hpath), "reason": _format_host_no_match_reason(ctx)}
        )
        return None

    score = CandidateScore(
        1,
        0,
        int(has_run_id),
        int(has_ws_ref),
        int(has_repo_cwd or slug_match),
        primary_session,
        mtime,
        0,
        0,
    )
    return _ScoredCandidate(
        score,
        hpath,
        host_name,
        f"Matched {', '.join(reasons)} in {host_name} host transcripts",
    )


def _score_host_candidate(
    host_name: str,
    hpath: Path,
    ctx: _HostMatchContext,
    rejected: list[dict[str, str]],
) -> _ScoredCandidate | None:
    meta = _inspect_transcript_metadata(hpath)
    mtime = _stat_candidate_mtime(hpath, meta, rejected)
    if mtime is None:
        return None
    primary_session = 0 if meta["is_sidechain"] else 1

    if ctx.session_id:
        return _score_host_session_id_candidate(
            hpath,
            host_name,
            meta,
            mtime,
            primary_session,
            ctx.session_id,
            ctx,
            rejected,
        )
    if ctx.has_workspace_match:
        rejected.append(
            {
                "path": str(hpath),
                "reason": "superseded by workspace-local candidate transcript",
            }
        )
        return None
    if not ctx.workspace_dir:
        rejected.append(
            {
                "path": str(hpath),
                "reason": "no workspace or session_id supplied for host matching",
            }
        )
        return None
    return _score_host_workspace_candidate(
        hpath, host_name, meta, mtime, primary_session, ctx.workspace_dir, ctx, rejected
    )


def _load_discovery_run_data(
    workspace_dir: Path | None,
) -> tuple[dict[str, Any], str | None]:
    if not workspace_dir or not workspace_dir.is_dir():
        return {}, None
    run_file = workspace_dir / "run.json"
    if not run_file.is_file():
        return {}, None
    loaded, run_parse_warning = _read_workspace_json_dict(run_file, "run.json")
    return (loaded if loaded is not None else {}), run_parse_warning


def _build_host_match_context(
    workspace_dir: Path | None,
    session_id: str | None,
    resolved_home: Path,
    has_workspace_match: bool,
) -> _HostMatchContext:
    run_data, run_parse_warning = _load_discovery_run_data(workspace_dir)
    raw_run_id = run_data.get("run_id")
    run_id = raw_run_id if isinstance(raw_run_id, str) else None
    repo_path = _extract_run_repo_path(run_data)
    expected_slugs: set[str] = set()
    if repo_path:
        expected_slugs |= _claude_project_slugs(repo_path)
    if workspace_dir:
        expected_slugs |= _claude_project_slugs(workspace_dir)
    return _HostMatchContext(
        workspace_dir=workspace_dir,
        session_id=session_id,
        resolved_home=resolved_home,
        run_data=run_data,
        run_parse_warning=run_parse_warning,
        run_id=run_id,
        repo_path=repo_path,
        expected_slugs=expected_slugs,
        ws_variants=_canonical_path_variants(workspace_dir) if workspace_dir else set(),
        repo_variants=_canonical_path_variants(repo_path) if repo_path else set(),
        has_workspace_match=has_workspace_match,
    )


def _raise_transcript_not_found(
    workspace_dir: Path | None,
    session_id: str | None,
    rejected: list[dict[str, str]],
) -> NoReturn:
    patterns_str = ", ".join(CANDIDATE_TRANSCRIPTS)
    if workspace_dir is not None:
        msg = (
            f"No candidate transcript found in workspace '{workspace_dir}' "
            f"(searched patterns: {patterns_str} in workspace and logs/, "
            "plus ~/.claude/projects/ and ~/.codex/sessions/). "
            "Supply an explicit transcript file path via positional argument or --transcript."
        )
    else:
        msg = (
            f"No matching session transcript found for session_id '{session_id}' "
            "in ~/.claude/projects/ or ~/.codex/sessions/. "
            "Supply an explicit transcript file path via positional argument or --transcript."
        )
    raise TranscriptNotFoundError(msg, rejected_candidates=rejected)


def _superseded_candidate_reason(
    other_score: CandidateScore, best_score: CandidateScore, best_name: str
) -> str:
    if other_score.primary_session < best_score.primary_session:
        return "subagent sidechain transcript superseded by primary session"
    if other_score.mtime < best_score.mtime:
        return f"older modification time than selected '{best_name}'"
    return f"lower match or naming priority than selected '{best_name}'"


def discover_transcript_details(
    workspace_dir: Path | None = None,
    session_id: str | None = None,
    home_dir: Path | None = None,
) -> dict[str, Any]:
    """Discover matching transcript across workspace and host directories with provenance."""
    resolved_home = _resolve_home_dir(home_dir)
    rejected: list[dict[str, str]] = []
    scored_matches: list[_ScoredCandidate] = []

    for cand in _collect_workspace_candidates(workspace_dir, session_id):
        scored = _score_workspace_candidate(cand, workspace_dir, session_id, rejected)
        if scored is not None:
            scored_matches.append(scored)

    host_ctx = _build_host_match_context(
        workspace_dir, session_id, resolved_home, bool(scored_matches)
    )
    for host_name, hpath in _find_host_transcript_files(resolved_home):
        scored = _score_host_candidate(host_name, hpath, host_ctx, rejected)
        if scored is not None:
            scored_matches.append(scored)

    if not scored_matches:
        _raise_transcript_not_found(workspace_dir, session_id, rejected)

    scored_matches.sort(key=lambda item: item.score, reverse=True)
    best = scored_matches[0]
    for other in scored_matches[1:]:
        rejected.append(
            {
                "path": str(other.path),
                "reason": _superseded_candidate_reason(
                    other.score, best.score, best.path.name
                ),
            }
        )

    return {
        "selected_transcript": best.path,
        "source": best.source,
        "selection_reason": best.reason,
        "rejected_candidates": rejected,
    }


def discover_transcript(
    workspace_dir: Path,
    session_id: str | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Discover the best matching transcript across workspace_dir and host session stores."""
    details = discover_transcript_details(
        workspace_dir=workspace_dir, session_id=session_id, home_dir=home_dir
    )
    return Path(details["selected_transcript"])


def export_bundle(bundle: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    """Export diagnostic bundle JSON and Markdown report to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "diagnostic_bundle.json"
    report_path = output_dir / "diagnostic_report.md"

    json_path.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report_path.write_text(render_report(bundle), encoding="utf-8")

    return json_path, report_path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "transcript",
        type=Path,
        nargs="?",
        default=None,
        help="Session transcript file (.json, .jsonl, .txt, .md)",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        dest="transcript_opt",
        default=None,
        help="Session transcript file (.json, .jsonl, .txt, .md)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Optional audit run workspace directory with run.json/audit.json",
    )
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        dest="workspace_dir",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--output",
        type=Path,
        dest="output",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write scrubbed diagnostic_bundle.json and report",
    )
    parser.add_argument("--session-id", help="Explicit session or run ID")
    parser.add_argument(
        "--redaction-tier",
        choices=REDACTION_TIERS,
        default="full",
        help="Transcript redaction tier: skeleton, evidence, or full (default: full)",
    )
    parser.add_argument(
        "--check-schema",
        action="store_true",
        help="Validate generated bundle against bundle.schema.json",
    )
    return parser


def _discover_cli_transcript(
    workspace: Path | None, session_id: str | None
) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        discovery_meta = discover_transcript_details(workspace, session_id=session_id)
        transcript = Path(discovery_meta["selected_transcript"])
        print(f"Discovered transcript: {transcript}", file=sys.stderr)
        return transcript, discovery_meta
    except TranscriptNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None, None


def _resolve_cli_transcript(
    transcript: Path | None, workspace: Path | None, session_id: str | None
) -> tuple[Path | None, dict[str, Any] | None]:
    if transcript:
        if not transcript.is_file():
            print(f"Error: Transcript file not found: {transcript}", file=sys.stderr)
            return None, None
        return transcript, None

    if workspace and workspace.is_dir():
        return _discover_cli_transcript(workspace, session_id)
    if workspace:
        print(f"Error: Workspace directory not found: {workspace}", file=sys.stderr)
        return None, None
    if session_id:
        return _discover_cli_transcript(None, session_id)

    patterns_str = ", ".join(CANDIDATE_TRANSCRIPTS)
    print(
        f"Error: Transcript file path is required or specify --workspace <dir> containing a candidate transcript ({patterns_str})",
        file=sys.stderr,
    )
    return None, None


def main() -> int:
    args = _build_arg_parser().parse_args()
    workspace = args.workspace or getattr(args, "workspace_dir", None)
    output_dir = args.output_dir or getattr(args, "output", None)
    raw_transcript = args.transcript or getattr(args, "transcript_opt", None)

    transcript, discovery_meta = _resolve_cli_transcript(
        raw_transcript, workspace, args.session_id
    )
    if transcript is None or not transcript.is_file():
        return 2

    bundle = generate_diagnostic_bundle(
        transcript,
        workspace,
        args.session_id,
        redaction_tier=args.redaction_tier,
        discovery_metadata=discovery_meta,
    )

    if args.check_schema:
        try:
            validate_bundle(bundle)
        except ValueError as e:
            print(f"Schema validation error: {e}", file=sys.stderr)
            return 1

    if output_dir:
        json_path, report_path = export_bundle(bundle, output_dir)
        print(f"Exported diagnostic bundle: {json_path}")
        print(f"Exported diagnostic report: {report_path}")
    else:
        print(json.dumps(bundle, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

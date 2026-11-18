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
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

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

# Backward-compatible export of (pattern, replacement) tuples
CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (pattern, replacement)
    for pattern, replacement, _ in CREDENTIAL_PATTERNS_CATEGORIZED
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
    re.compile(
        r"'(?:schema_version|contracts|evidence|findings|issues)' is a required property"
    ),
    re.compile(r"(?i)\bschema mismatch\b"),
    re.compile(r"(?i)\bschema validation (?:failed|error)\b"),
    re.compile(r"(?i)is not valid under any of the given schemas"),
    re.compile(r"Duplicate IDs in (?:evidence|contracts|findings|issues):"),
    re.compile(r"Reversed lines:"),
    re.compile(r"Evidence path must be repository-relative:"),
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

KNOWN_SPECIALISTS = [
    "repo-audit-behavioral-contracts",
    "repo-audit-invariants",
    "repo-audit-state-machines",
    "repo-audit-concurrency",
    "repo-audit-lifecycle",
    "repo-audit-temporal-coupling",
    "repo-audit-domain-models",
    "repo-audit-failure-modes",
    "repo-audit-trust-boundaries",
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
    "repo-audit-program",
]

_COORDINATOR_SKILLS = frozenset({"repo-audit-program", "repo-audit-diagnostics"})


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


def _scrub_json_value(
    value: Any,
    stats: dict[str, int],
    *,
    parent_key: str | None = None,
    omit_tool_outputs: bool = False,
    omitted_counter: list[int] | None = None,
) -> Any:
    if isinstance(value, dict):
        obj_type = value.get("type")
        obj_role = value.get("role")
        result: dict[str, Any] = {}

        # Detect tool output containers for skeleton/evidence omission
        is_claude_tool_result = obj_type == "tool_result"
        is_codex_tool_output = obj_type in {
            "function_call_output",
            "custom_tool_call_output",
            "local_shell_call_output",
        }
        is_codex_exec_end = obj_type == "exec_command_end"
        is_role_tool = obj_role in {"tool", "function", "tool_result"}

        for k, v in value.items():
            # Apply tool output body omission when requested
            if omit_tool_outputs:
                if (
                    (is_claude_tool_result and k == "content")
                    or (is_codex_tool_output and k == "output")
                    or (is_role_tool and k in {"content", "output", "result"})
                    or (
                        is_codex_exec_end
                        and k
                        in {
                            "stdout",
                            "stderr",
                            "output",
                            "aggregated_output",
                            "formatted_output",
                        }
                        and v
                    )
                    or (
                        parent_key == "toolUseResult"
                        and k in {"stdout", "stderr", "output", "content"}
                        and v
                    )
                ):
                    if (
                        v != OMITTED_TOOL_OUTPUT_PLACEHOLDER
                        and omitted_counter is not None
                    ):
                        omitted_counter[0] += 1
                    result[k] = OMITTED_TOOL_OUTPUT_PLACEHOLDER
                    continue
                if k == "toolUseResult" and isinstance(v, (str, list)):
                    if (
                        v != OMITTED_TOOL_OUTPUT_PLACEHOLDER
                        and omitted_counter is not None
                    ):
                        omitted_counter[0] += 1
                    result[k] = OMITTED_TOOL_OUTPUT_PLACEHOLDER
                    continue

            result[k] = _scrub_json_value(
                v,
                stats,
                parent_key=k,
                omit_tool_outputs=omit_tool_outputs,
                omitted_counter=omitted_counter,
            )
        return result

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
        # First check if string itself is an embedded/escaped JSON object or array
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

        if (
            parent_key
            and _is_sensitive_json_key(parent_key)
            and scrubbed_str.strip()
            and not scrubbed_str.strip().startswith("[REDACTED_")
            and sum(stats.values()) == before_total
        ):
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

        return scrubbed_str

    return value


def _compute_default_cited_lines(text: str) -> set[int]:
    failure = detect_failures(text)
    line_no = failure.get("line_number")
    return {line_no} if isinstance(line_no, int) and line_no >= 1 else set()


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
    non_empty = [ln.strip() for ln in lines if ln.strip()]

    # Case 1: Single-line-per-record JSONL (1 or more lines where non-empty lines parse as JSON dicts)
    jsonl_records: list[tuple[int, str, Any]] = []
    is_jsonl = bool(non_empty)
    if is_jsonl:
        for idx, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                jsonl_records.append((idx, raw_line, None))
                continue
            parsed_line = _try_parse_json_container(stripped)
            if isinstance(parsed_line, dict):
                jsonl_records.append((idx, raw_line, parsed_line))
            else:
                is_jsonl = False
                break

    if is_jsonl and jsonl_records:
        out_lines: list[str] = []
        for idx, raw_line, parsed_obj in jsonl_records:
            if parsed_obj is None:
                out_lines.append(raw_line)
                continue
            omit_line_tools = tier == "skeleton" or (
                tier == "evidence" and idx not in resolved_cited
            )
            newline = (
                "\r\n"
                if raw_line.endswith("\r\n")
                else ("\n" if raw_line.endswith("\n") else "")
            )
            scrubbed_obj = _scrub_json_value(
                parsed_obj,
                stats,
                omit_tool_outputs=omit_line_tools,
                omitted_counter=omitted_counter,
            )
            serialized_line = json.dumps(scrubbed_obj, ensure_ascii=False)
            serialized_line = _scrub_string_patterns(serialized_line, stats)
            out_lines.append(serialized_line + newline)
        scrubbed_text = "".join(out_lines)
    else:
        # Case 2: Multi-line standalone JSON document
        whole_json = _try_parse_json_container(text)
        if whole_json is not None:
            omit_tools = tier == "skeleton" or (
                tier == "evidence" and not resolved_cited
            )
            if (
                tier == "evidence"
                and resolved_cited
                and isinstance(whole_json, dict)
                and any(
                    isinstance(whole_json.get(k), list)
                    for k in ("messages", "turns", "events", "transcript")
                )
            ):
                # Apply per-turn omission on structured JSON arrays
                copied = dict(whole_json)
                for key in ("messages", "turns", "events", "transcript"):
                    items = copied.get(key)
                    if isinstance(items, list):
                        new_items = []
                        for item_idx, item in enumerate(items, start=1):
                            item_omit = item_idx not in resolved_cited
                            new_items.append(
                                _scrub_json_value(
                                    item,
                                    stats,
                                    omit_tool_outputs=item_omit,
                                    omitted_counter=omitted_counter,
                                )
                            )
                        copied[key] = new_items
                for k, v in copied.items():
                    if k not in {"messages", "turns", "events", "transcript"}:
                        copied[k] = _scrub_json_value(
                            v,
                            stats,
                            parent_key=k,
                            omit_tool_outputs=False,
                            omitted_counter=omitted_counter,
                        )
                scrubbed_obj = copied
            else:
                scrubbed_obj = _scrub_json_value(
                    whole_json,
                    stats,
                    omit_tool_outputs=omit_tools,
                    omitted_counter=omitted_counter,
                )
            trailing_nl = "\n" if text.endswith("\n") else ""
            scrubbed_text = _scrub_string_patterns(
                json.dumps(scrubbed_obj, indent=2, ensure_ascii=False) + trailing_nl,
                stats,
            )
        else:
            # Case 3: Plain-text or mixed log transcript
            out_plain: list[str] = []
            for idx, raw_line in enumerate(lines, start=1):
                stripped = raw_line.strip()
                parsed_inline = _try_parse_json_container(stripped)
                omit_line_tools = tier == "skeleton" or (
                    tier == "evidence" and idx not in resolved_cited
                )
                if isinstance(parsed_inline, dict):
                    newline = (
                        "\r\n"
                        if raw_line.endswith("\r\n")
                        else ("\n" if raw_line.endswith("\n") else "")
                    )
                    scrubbed_inline = _scrub_json_value(
                        parsed_inline,
                        stats,
                        omit_tool_outputs=omit_line_tools,
                        omitted_counter=omitted_counter,
                    )
                    out_plain.append(
                        _scrub_string_patterns(
                            json.dumps(scrubbed_inline, ensure_ascii=False), stats
                        )
                        + newline
                    )
                else:
                    if omit_line_tools and re.match(
                        r"(?i)^(?:\[[^\]]+\]\s*)?(?:tool[_ ]?(?:output|result)|stdout|stderr)\s*:",
                        stripped,
                    ):
                        newline = (
                            "\r\n"
                            if raw_line.endswith("\r\n")
                            else ("\n" if raw_line.endswith("\n") else "")
                        )
                        prefix = raw_line.split(":", 1)[0]
                        omitted_counter[0] += 1
                        out_plain.append(
                            f"{_scrub_string_patterns(prefix, stats)}: {OMITTED_TOOL_OUTPUT_PLACEHOLDER}{newline}"
                        )
                    else:
                        out_plain.append(_scrub_string_patterns(raw_line, stats))
            scrubbed_text = (
                _scrub_string_patterns("".join(out_plain), stats)
                if lines
                else _scrub_string_patterns(text, stats)
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


def _stringify_content_value(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        nested = _try_parse_json_container(content)
        if isinstance(nested, dict):
            parts: list[str] = []
            for key in (
                "output",
                "stderr",
                "stdout",
                "error",
                "message",
                "text",
                "result",
            ):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
                elif isinstance(val, dict):
                    sub = _stringify_content_value(val)
                    if sub:
                        parts.append(sub)
            if parts:
                return "\n".join(parts)
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("output")
                    or item.get("message")
                )
                if text is not None:
                    rendered = _stringify_content_value(text)
                    if rendered:
                        parts.append(rendered)
        return "\n".join(parts)
    if isinstance(content, dict):
        parts = []
        for key in (
            "message",
            "error",
            "stderr",
            "stdout",
            "output",
            "text",
            "content",
            "result",
        ):
            if key in content and content[key] is not None:
                rendered = _stringify_content_value(content[key])
                if rendered:
                    parts.append(rendered)
        if parts:
            return "\n".join(parts)
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _check_nested_tool_failure(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("is_error") is True or value.get("timed_out") is True:
            return True
        status = str(value.get("status", "")).lower()
        if status in {"error", "failed", "timeout", "timed_out"}:
            return True
        for code_key in ("exit_code", "returncode", "exitCode", "return_code"):
            code = value.get(code_key)
            if isinstance(code, int) and code != 0:
                return True
        meta = value.get("metadata")
        if isinstance(meta, dict) and _check_nested_tool_failure(meta):
            return True
        err = value.get("error")
        if err not in (None, False, "", {}):
            return True
    elif isinstance(value, str):
        nested = _try_parse_json_container(value)
        if isinstance(nested, dict):
            return _check_nested_tool_failure(nested)
    return False


def _extract_turns_from_record(obj: dict[str, Any], line_no: int) -> list[ParsedTurn]:
    """Extract normalized role-separated turns from a single JSON/JSONL record."""
    turns: list[ParsedTurn] = []
    rec_type = str(obj.get("type", "")).lower()

    # 1. Explicit error events (Claude Code, Codex, generic)
    if rec_type in {"error", "api_error", "stream_error", "exception"} or str(
        obj.get("level", "")
    ).lower() in {"error", "fatal"}:
        err_text = _stringify_content_value(
            obj.get("error") or obj.get("message") or obj.get("payload") or obj
        )
        turns.append(ParsedTurn(line_no, "error", err_text, is_failed=True))
        return turns

    # 2. Codex event_msg wrapper
    if rec_type == "event_msg" and isinstance(obj.get("payload"), dict):
        payload = obj["payload"]
        ptype = str(payload.get("type", "")).lower()
        if ptype in {"error", "stream_error", "api_error"}:
            err_text = _stringify_content_value(
                payload.get("message") or payload.get("error") or payload
            )
            turns.append(ParsedTurn(line_no, "error", err_text, is_failed=True))
            return turns
        if ptype == "exec_command_end":
            failed = _check_nested_tool_failure(payload)
            out_text = _stringify_content_value(
                payload.get("stderr")
                or payload.get("aggregated_output")
                or payload.get("formatted_output")
                or payload.get("stdout")
                or ""
            )
            cmd = payload.get("command")
            if isinstance(cmd, list):
                cmd_str = " ".join(str(c) for c in cmd)
                out_text = f"{cmd_str}: {out_text}".strip(": ")
            turns.append(ParsedTurn(line_no, "tool_output", out_text, is_failed=failed))
            return turns
        if ptype == "user_message":
            turns.append(
                ParsedTurn(
                    line_no,
                    "user",
                    _stringify_content_value(
                        payload.get("message") or payload.get("text")
                    ),
                )
            )
            return turns
        if ptype in {"agent_message", "assistant_message"}:
            turns.append(
                ParsedTurn(
                    line_no,
                    "assistant",
                    _stringify_content_value(
                        payload.get("message") or payload.get("text")
                    ),
                )
            )
            return turns
        return turns

    # 3. Codex response_item wrapper
    if rec_type == "response_item" and isinstance(obj.get("payload"), dict):
        payload = obj["payload"]
        ptype = str(payload.get("type", "")).lower()
        if ptype == "message":
            role = str(payload.get("role", "assistant")).lower()
            norm_role = (
                "user"
                if role in {"user", "developer", "system", "human"}
                else "assistant"
            )
            text = _stringify_content_value(payload.get("content"))
            turns.append(ParsedTurn(line_no, norm_role, text, is_failed=False))
            return turns
        if ptype in {"function_call", "custom_tool_call", "local_shell_call"}:
            name = payload.get("name") or payload.get("tool_name") or ""
            args = _stringify_content_value(
                payload.get("arguments")
                or payload.get("input")
                or payload.get("action")
            )
            turns.append(
                ParsedTurn(
                    line_no, "tool_use", f"{name} {args}".strip(), is_failed=False
                )
            )
            return turns
        if ptype in {
            "function_call_output",
            "custom_tool_call_output",
            "local_shell_call_output",
        }:
            raw_output = payload.get("output")
            failed = _check_nested_tool_failure(payload) or _check_nested_tool_failure(
                raw_output
            )
            turns.append(
                ParsedTurn(
                    line_no,
                    "tool_output",
                    _stringify_content_value(raw_output),
                    is_failed=failed,
                )
            )
            return turns
        return turns

    # Skip pure session/turn metadata records
    if rec_type in {"session_meta", "turn_context", "summary", "file-history-snapshot"}:
        return turns

    # 4. Claude Code system / result records
    if rec_type == "system":
        subtype = str(obj.get("subtype", "")).lower()
        if "error" in subtype or obj.get("is_error") is True or obj.get("error"):
            err_text = _stringify_content_value(
                obj.get("error") or obj.get("message") or obj.get("content")
            )
            turns.append(ParsedTurn(line_no, "error", err_text, is_failed=True))
        return turns

    if rec_type == "result":
        is_err = (
            bool(obj.get("is_error")) or "error" in str(obj.get("subtype", "")).lower()
        )
        res_text = _stringify_content_value(
            obj.get("result") or obj.get("error") or obj.get("message")
        )
        if is_err:
            turns.append(ParsedTurn(line_no, "error", res_text, is_failed=True))
        elif res_text:
            turns.append(ParsedTurn(line_no, "assistant", res_text, is_failed=False))
        return turns

    # 5. Claude Code / Anthropic / OpenAI message envelopes
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    role = str(msg.get("role") or rec_type or "").lower()
    is_api_error = bool(
        obj.get("isApiErrorMessage")
        or obj.get("is_error")
        or msg.get("is_error")
        or (obj.get("error") not in (None, False, "", {}))
        or str(msg.get("stop_reason", "")).lower() == "model_context_window_exceeded"
    )

    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                btype = str(block.get("type", "")).lower()
                if btype == "tool_result":
                    failed = bool(block.get("is_error")) or _check_nested_tool_failure(
                        obj.get("toolUseResult")
                    )
                    btext = _stringify_content_value(block.get("content"))
                    if not btext and obj.get("toolUseResult"):
                        btext = _stringify_content_value(obj.get("toolUseResult"))
                    turns.append(
                        ParsedTurn(line_no, "tool_output", btext, is_failed=failed)
                    )
                elif btype in {"tool_use", "function_call"}:
                    name = block.get("name", "")
                    inp = _stringify_content_value(
                        block.get("input") or block.get("arguments")
                    )
                    turns.append(
                        ParsedTurn(
                            line_no,
                            "tool_use",
                            f"{name} {inp}".strip(),
                            is_failed=False,
                        )
                    )
                elif btype in {"text", "input_text", "output_text"}:
                    btext = _stringify_content_value(block.get("text"))
                    if is_api_error:
                        turns.append(
                            ParsedTurn(line_no, "error", btext, is_failed=True)
                        )
                    elif role in {"user", "human", "system", "developer"}:
                        turns.append(
                            ParsedTurn(line_no, "user", btext, is_failed=False)
                        )
                    else:
                        turns.append(
                            ParsedTurn(line_no, "assistant", btext, is_failed=False)
                        )
            elif isinstance(block, str):
                target_role = (
                    "error"
                    if is_api_error
                    else (
                        "user" if role in {"user", "human", "system"} else "assistant"
                    )
                )
                turns.append(
                    ParsedTurn(line_no, target_role, block, is_failed=is_api_error)
                )
        if turns:
            return turns

    # Direct tool_result / tool role records
    if rec_type in {"tool_result", "tool_output", "function_call_output"} or role in {
        "tool",
        "function",
        "tool_result",
    }:
        raw_out = (
            msg.get("content")
            if msg.get("content") is not None
            else (
                obj.get("output")
                if obj.get("output") is not None
                else obj.get("result")
            )
        )
        failed = _check_nested_tool_failure(obj) or _check_nested_tool_failure(msg)
        turns.append(
            ParsedTurn(
                line_no,
                "tool_output",
                _stringify_content_value(raw_out),
                is_failed=failed,
            )
        )
        return turns

    if rec_type in {"tool_use", "function_call"}:
        name = obj.get("name") or msg.get("name") or ""
        inp = _stringify_content_value(
            obj.get("input") or obj.get("arguments") or msg.get("input")
        )
        turns.append(
            ParsedTurn(line_no, "tool_use", f"{name} {inp}".strip(), is_failed=False)
        )
        return turns

    # Scalar content on user / assistant / error record
    text_val = _stringify_content_value(
        content
        if content is not None
        else (obj.get("error") or obj.get("text") or obj.get("output"))
    )
    if is_api_error:
        turns.append(ParsedTurn(line_no, "error", text_val, is_failed=True))
    elif role in {"user", "human", "system", "developer"}:
        turns.append(ParsedTurn(line_no, "user", text_val, is_failed=False))
    elif role in {"assistant", "model", "agent"}:
        # Also extract OpenAI-style tool_calls if present
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = (
                        tc.get("function")
                        if isinstance(tc.get("function"), dict)
                        else tc
                    )
                    turns.append(
                        ParsedTurn(
                            line_no,
                            "tool_use",
                            f"{fn.get('name', '')} {_stringify_content_value(fn.get('arguments'))}".strip(),
                            is_failed=False,
                        )
                    )
        if text_val:
            turns.append(ParsedTurn(line_no, "assistant", text_val, is_failed=False))
    return turns


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

    # Check JSONL (each non-empty line is a JSON object, or majority structured JSONL)
    jsonl_turns: list[ParsedTurn] = []
    json_line_count = 0
    for idx, stripped in non_empty:
        parsed = _try_parse_json_container(stripped)
        if isinstance(parsed, dict):
            json_line_count += 1
            jsonl_turns.extend(_extract_turns_from_record(parsed, idx))
        elif json_line_count > 0:
            # Trailing truncated/raw line in an otherwise JSONL session
            jsonl_turns.append(ParsedTurn(idx, "error", stripped, is_failed=False))

    if json_line_count > 0 and json_line_count >= len(non_empty) // 2:
        return jsonl_turns, True

    # Check multi-line JSON document
    whole = _try_parse_json_container(transcript_text)
    if whole is not None:
        records: list[dict[str, Any]] = []
        if isinstance(whole, list):
            records = [item for item in whole if isinstance(item, dict)]
        elif isinstance(whole, dict):
            for key in ("messages", "turns", "events", "transcript", "items"):
                if isinstance(whole.get(key), list):
                    records = [item for item in whole[key] if isinstance(item, dict)]
                    break
            if not records:
                records = [whole]

        doc_turns: list[ParsedTurn] = []
        for rec in records:
            extracted = _extract_turns_from_record(rec, 1)
            for turn in extracted:
                resolved_line = (
                    _locate_snippet_line(lines, turn.text) or len(lines) or 1
                )
                turn.line_number = resolved_line
                doc_turns.append(turn)
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


def _match_known_signature(text: str, line_no: int) -> dict[str, Any] | None:
    """Match text against context, schema, or timeout failure signatures."""
    sublines = [ln.strip() for ln in text.splitlines() if ln.strip()] or [text.strip()]
    for sub in sublines:
        for pattern in TIMEOUT_PATTERNS:
            if pattern.search(sub):
                return {
                    "failure_type": "tool_timeout",
                    "summary": f"Tool execution timed out: {sub}",
                    "line_number": line_no,
                    "matched_line": sub,
                    "details": {"pattern": pattern.pattern},
                }
        for pattern in SCHEMA_PATTERNS:
            if pattern.search(sub):
                return {
                    "failure_type": "schema_validation_error",
                    "summary": f"Audit schema validation error: {sub}",
                    "line_number": line_no,
                    "matched_line": sub,
                    "details": {"pattern": pattern.pattern},
                }
        for pattern in CONTEXT_PATTERNS:
            if pattern.search(sub):
                return {
                    "failure_type": "context_limit",
                    "summary": f"Context window token limit exceeded: {sub}",
                    "line_number": line_no,
                    "matched_line": sub,
                    "details": {"pattern": pattern.pattern},
                }
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


def detect_failures(transcript_text: str) -> dict[str, Any]:
    """Detect timeouts, schema errors, context limits, or unclassified failures."""
    lines = transcript_text.splitlines()
    turns, is_structured = parse_transcript_turns(transcript_text)

    if is_structured and turns:
        # Identify the terminal cluster of turns after the last normal progress turn
        # so recovered earlier tool errors do not overshadow a terminal crash.
        last_recovery_idx = -1
        for idx, turn in enumerate(turns):
            if idx < len(turns) - 1 and (
                turn.role in {"user", "tool_use"}
                or (turn.role in {"assistant", "tool_output"} and not turn.is_failed)
            ):
                last_recovery_idx = idx

        terminal_turns = turns[last_recovery_idx + 1 :]
        earlier_turns = turns[: last_recovery_idx + 1]

        # 1. Terminal structured error events & failed tool outputs (backward)
        terminal_failed = [t for t in terminal_turns if t.is_failed]
        for turn in reversed(terminal_failed):
            matched = _match_known_signature(turn.text, turn.line_number)
            if matched:
                matched["details"]["role"] = turn.role
                return matched
        if terminal_failed:
            return _build_unclassified_from_turn(terminal_failed[-1])

        # 2. Earlier structured error events & failed tool outputs (backward)
        earlier_failed = [t for t in earlier_turns if t.is_failed]
        for turn in reversed(earlier_failed):
            matched = _match_known_signature(turn.text, turn.line_number)
            if matched:
                matched["details"]["role"] = turn.role
                return matched

        # 3. Unflagged tool outputs backward (harnesses that omit is_error=true)
        for turn in reversed(turns):
            if turn.role == "tool_output":
                matched = _match_known_signature(turn.text, turn.line_number)
                if matched:
                    matched["details"]["role"] = turn.role
                    return matched

        # 4. Terminal assistant turn reporting provider/context/schema/timeout failure
        for turn in reversed(turns):
            if turn.role == "user":
                break
            if turn.role == "assistant":
                matched = _match_known_signature(turn.text, turn.line_number)
                if matched:
                    matched["details"]["role"] = turn.role
                    return matched
                break

        # 5. Any earlier structured failed turn without known signature
        if earlier_failed:
            return _build_unclassified_from_turn(earlier_failed[-1])

        # 6. Check terminal tool_output for explicit traceback/fatal lines
        for turn in reversed(turns):
            if turn.role == "tool_output" and GENERAL_ERROR_RE.search(turn.text):
                return _build_unclassified_from_turn(turn)
            if turn.role == "tool_output":
                break

        last_turn = turns[-1]
        last_line_text = next(
            (ln.strip() for ln in reversed(last_turn.text.splitlines()) if ln.strip()),
            "",
        ) or (lines[-1].strip() if lines else "")
        return {
            "failure_type": "unclassified_failure",
            "summary": "Session ended unexpectedly without an explicit error signature",
            "line_number": len(lines) if lines else 1,
            "matched_line": last_line_text,
            "details": {},
        }

    # Plain-text fallback: scan line by line
    for idx, line in enumerate(lines, start=1):
        matched = _match_known_signature(line, idx)
        if matched:
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

    return {
        "failure_type": "unclassified_failure",
        "summary": "Session ended unexpectedly without an explicit error signature",
        "line_number": len(lines) if lines else 1,
        "matched_line": lines[-1].strip() if lines else "",
        "details": {},
    }


def _find_specialist_in_segment(text: str, *, prefer_first: bool = False) -> str | None:
    """Find a specialist skill mentioned in a single turn/line segment."""
    domain_matches: list[tuple[int, str]] = []
    coord_matches: list[tuple[int, str]] = []

    for specialist in KNOWN_SPECIALISTS:
        pos = text.find(specialist) if prefer_first else text.rfind(specialist)
        if pos != -1:
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
    for specialist in KNOWN_SPECIALISTS:
        if specialist in _COORDINATOR_SKILLS:
            continue
        keyword = specialist.replace("repo-audit-", "")
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])")
        found = list(pattern.finditer(lowered))
        if found:
            matches.append((found[-1].start(), specialist, keyword))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1], matches[0][2]


def correlate_audit_domain(
    transcript_text: str,
    audit_data: dict[str, Any] | None = None,
    failure_line: int | None = None,
) -> dict[str, Any]:
    """Correlate failed transcript steps with the most recently active specialist skill."""
    lines = transcript_text.splitlines()
    if failure_line is None:
        failure_line = detect_failures(transcript_text).get("line_number") or len(lines)
    cutoff = max(1, min(failure_line, len(lines))) if lines else 1

    specialist_found: str | None = None
    topic_found: str | None = None

    turns, is_structured = parse_transcript_turns(transcript_text)
    if is_structured and turns:
        before_turns = [t for t in turns if t.line_number <= cutoff]
        after_turns = [t for t in turns if t.line_number > cutoff]

        # 1. Prefer non-user active turns (assistant, tool_use, tool_output, error) closest to failure_line
        for turn in reversed(before_turns):
            if turn.role != "user":
                cand = _find_specialist_in_segment(turn.text, prefer_first=False)
                if cand:
                    specialist_found = cand
                    break

        # 2. Fall back to user prompt turns at or before failure_line
        #    (if prompt says "Use these skills in order: A, B", first planned skill is starting)
        if not specialist_found:
            for turn in reversed(before_turns):
                if turn.role == "user":
                    prefer_first = "in order" in turn.text.lower()
                    cand = _find_specialist_in_segment(
                        turn.text, prefer_first=prefer_first
                    )
                    if cand:
                        specialist_found = cand
                        break

        # 3. Fall back to turns after failure_line if none appeared before
        if not specialist_found:
            for turn in after_turns:
                cand = _find_specialist_in_segment(turn.text, prefer_first=False)
                if cand:
                    specialist_found = cand
                    break

        # 4. Topic keyword fallback (backward from failure_line)
        if not specialist_found:
            for turn in reversed(before_turns):
                if turn.role != "user":
                    kw_match = _find_topic_keyword_in_segment(turn.text)
                    if kw_match:
                        specialist_found, topic_found = kw_match
                        break
            if not specialist_found:
                for turn in reversed(before_turns):
                    kw_match = _find_topic_keyword_in_segment(turn.text)
                    if kw_match:
                        specialist_found, topic_found = kw_match
                        break
    else:
        # Plain-text transcript: scan backward from cutoff to line 1
        for idx in range(cutoff, 0, -1):
            cand = _find_specialist_in_segment(lines[idx - 1], prefer_first=False)
            if cand:
                specialist_found = cand
                break

        if not specialist_found:
            for idx in range(cutoff + 1, len(lines) + 1):
                cand = _find_specialist_in_segment(lines[idx - 1], prefer_first=False)
                if cand:
                    specialist_found = cand
                    break

        if not specialist_found:
            for idx in range(cutoff, 0, -1):
                kw_match = _find_topic_keyword_in_segment(lines[idx - 1])
                if kw_match:
                    specialist_found, topic_found = kw_match
                    break

    if specialist_found and not topic_found:
        topic_found = specialist_found.replace("repo-audit-", "")

    ledger_refs: list[str] = []
    if audit_data:
        for section in ("findings", "evidence", "contracts", "issues"):
            for item in audit_data.get(section, []):
                item_id = item.get("id")
                if item_id and item_id in transcript_text:
                    ledger_refs.append(f"{section}:{item_id}")
    else:
        id_pattern = re.compile(r"\b([FEIC]-\d+|E-[a-z0-9_-]+)\b")
        ledger_refs = sorted(set(id_pattern.findall(transcript_text)))

    return {
        "specialist_skill": specialist_found,
        "topic": topic_found,
        "ledger_references": ledger_refs,
    }


def formulate_recovery_instructions(
    failure: dict[str, Any],
    run_data: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate concrete recovery steps to resume audit from the last valid checkpoint."""
    last_checkpoint = None
    instructions = []

    dir_str = str(workspace_dir) if workspace_dir else "<audit-run-dir>"

    if run_data and run_data.get("checkpoints"):
        last_ckpt = run_data["checkpoints"][-1]
        last_checkpoint = f"{last_ckpt.get('state', 'unknown')}: {last_ckpt.get('note', '')} (at {last_ckpt.get('at', '')})"
    elif run_data and run_data.get("state"):
        last_checkpoint = f"Run state: {run_data['state']}"

    ftype = failure.get("failure_type")
    if ftype == "tool_timeout":
        instructions.append(
            f"1. Check the command that timed out at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'."
        )
        instructions.append(
            "2. Add file exclusion patterns or narrow the target scope in the tool arguments."
        )
        instructions.append(
            f"3. Mark current state paused: python scripts/fred.py checkpoint {dir_str} --state paused --note 'Paused after tool timeout triage'."
        )
        instructions.append(
            f"4. Re-run specialist with bounded scope or resume: python scripts/fred.py resume {dir_str}"
        )

    elif ftype == "schema_validation_error":
        instructions.append(
            f"1. Inspect the schema validation error at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'."
        )
        instructions.append(
            f"2. Validate current audit.json ledger: python skills/repo-audit-program/scripts/validate_audit.py {dir_str}/audit.json"
        )
        instructions.append(
            "3. Correct duplicate IDs, reversed line ranges, or missing required fields in audit.json."
        )
        instructions.append(
            f"4. Re-verify ledger validity, then resume audit coordination: python scripts/fred.py resume {dir_str}"
        )

    elif ftype == "context_limit":
        instructions.append(
            f"1. Context limit reached at line {failure.get('line_number')}."
        )
        instructions.append(
            f"2. Save current progress to checkpoint: python scripts/fred.py checkpoint {dir_str} --state paused --note 'Context limit reached; clearing context'."
        )
        instructions.append(
            "3. Split the specialist analysis into smaller bounded workflow slices."
        )
        instructions.append(
            f"4. Start fresh agent session and resume from checkpoint: python scripts/fred.py resume {dir_str}"
        )

    else:
        instructions.append(
            f"1. Review the unclassified error at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'."
        )
        instructions.append(
            f"2. Inspect {dir_str}/run.json and {dir_str}/audit.json for partial progress."
        )
        instructions.append(
            f"3. Resume audit run from the last valid checkpoint: python scripts/fred.py resume {dir_str}"
        )

    return {
        "last_checkpoint": last_checkpoint,
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

    # Read workspace state if available
    run_data = None
    audit_data = None
    if workspace_dir:
        run_file = workspace_dir / "run.json"
        if run_file.is_file():
            with contextlib.suppress(OSError, ValueError):
                run_data = json.loads(run_file.read_text(encoding="utf-8"))
        audit_file = workspace_dir / "audit.json"
        if audit_file.is_file():
            with contextlib.suppress(OSError, ValueError):
                audit_data = json.loads(audit_file.read_text(encoding="utf-8"))

    resolved_session_id = (
        session_id
        or (run_data.get("run_id") if isinstance(run_data, dict) else None)
        or transcript_path.stem
    )

    # Detect failures first on raw text
    failure = detect_failures(raw_text)

    # Correlate domain and ledger entries relative to the failure line
    correlated = correlate_audit_domain(
        raw_text, audit_data, failure_line=failure.get("line_number")
    )

    # Scrub sensitive credentials from failure fields before building recovery commands
    failure["matched_line"] = scrub_credentials(failure.get("matched_line", ""))
    failure["summary"] = scrub_credentials(failure.get("summary", ""))

    # Generate actionable recovery steps
    recovery = formulate_recovery_instructions(failure, run_data, workspace_dir)
    recovery["instructions"] = [
        scrub_credentials(inst) for inst in recovery.get("instructions", [])
    ]
    if recovery.get("last_checkpoint"):
        recovery["last_checkpoint"] = scrub_credentials(recovery["last_checkpoint"])

    cited_lines = _find_cited_lines_for_bundle(raw_text, failure, correlated)
    scrubbed_transcript, redaction_info = scrub_credentials_with_stats(
        raw_text, tier=redaction_tier, cited_lines=cited_lines
    )

    if discovery_metadata is not None:
        normalized_discovery = {
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
    else:
        normalized_discovery = {
            "source": _infer_transcript_source(transcript_path),
            "selected_transcript": scrub_credentials(str(transcript_path)),
            "selection_reason": "Explicit transcript path supplied",
            "rejected_candidates": [],
        }

    bundle: dict[str, Any] = {
        "schema_version": "1.0",
        "session_id": resolved_session_id,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "failure_classification": failure,
        "correlated_domain": correlated,
        "environment": {
            "platform": f"{platform.system()} {platform.release()}",
            "python_version": sys.version.split()[0],
            "repository_path": run_data.get("repository", {}).get("path")
            if isinstance(run_data, dict)
            and isinstance(run_data.get("repository"), dict)
            else None,
            "run_id": run_data.get("run_id") if isinstance(run_data, dict) else None,
        },
        "recovery": recovery,
        "scrubbed_transcript": scrubbed_transcript,
        "redaction": redaction_info,
        "transcript_discovery": normalized_discovery,
    }

    return bundle


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
                f"  - `{item.get('path', '')}` — {item.get('reason', '')}"
                for item in rejected
            )
        else:
            rejected_md = "  - None"
        discovery_section = f"""
## Transcript discovery

- **Source:** {disc.get("source", "unknown")}
- **Selected Transcript:** `{disc.get("selected_transcript", "unknown")}`
- **Selection Reason:** {disc.get("selection_reason", "unknown")}
- **Rejected Candidates:**
{rejected_md}
"""

    return f"""# Diagnostic Triage Report

- **Session / Run ID:** {bundle.get("session_id", "unknown")}
- **Timestamp:** {bundle.get("timestamp", "unknown")}
- **Failure Classification:** {fc.get("failure_type", "unknown")}
- **Root Cause Line:** {fc.get("line_number", "unknown")}
- **Correlated Domain:** {cd.get("specialist_skill") or "None identified"}

## Summary of failure

{fc.get("summary", "")}

```
{fc.get("matched_line", "")}
```

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


def _inspect_transcript_metadata(path: Path) -> dict[str, Any]:
    """Extract session IDs, working directories, sidechain flag, and text sample from a candidate."""
    session_ids: set[str] = {path.stem}
    cwds: list[str] = []
    is_sidechain = "subagents" in {p.lower() for p in path.parts}
    raw_text = ""

    with contextlib.suppress(OSError):
        raw_text = path.read_text(encoding="utf-8", errors="replace")

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        with contextlib.suppress(ValueError, TypeError):
            obj = json.loads(stripped)
            if not isinstance(obj, dict):
                continue
            if obj.get("isSidechain") is True:
                is_sidechain = True
            for id_key in ("sessionId", "session_id"):
                val = obj.get(id_key)
                if isinstance(val, str) and val.strip():
                    session_ids.add(val.strip())
            if obj.get("type") == "session_meta" and isinstance(
                obj.get("payload"), dict
            ):
                sid = obj["payload"].get("id")
                if isinstance(sid, str) and sid.strip():
                    session_ids.add(sid.strip())
            elif isinstance(obj.get("id"), str) and obj.get("type") in {
                "session",
                "metadata",
                "",
                None,
            }:
                session_ids.add(obj["id"].strip())
            meta = obj.get("metadata")
            if isinstance(meta, dict) and isinstance(meta.get("session_id"), str):
                session_ids.add(meta["session_id"].strip())

            for cwd_key in ("cwd", "workdir", "project_path", "repository_path"):
                cval = obj.get(cwd_key)
                if isinstance(cval, str) and cval.strip() and cval.strip() not in cwds:
                    cwds.append(cval.strip())
            payload = obj.get("payload")
            if isinstance(payload, dict):
                for cwd_key in ("cwd", "workdir", "project_path", "repository_path"):
                    cval = payload.get(cwd_key)
                    if (
                        isinstance(cval, str)
                        and cval.strip()
                        and cval.strip() not in cwds
                    ):
                        cwds.append(cval.strip())

    return {
        "session_ids": session_ids,
        "cwds": cwds,
        "is_sidechain": is_sidechain,
        "raw_text": raw_text,
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


def _find_host_transcript_files(home_dir: Path) -> list[tuple[str, Path]]:
    """Collect candidate JSONL/JSON session transcripts from Claude Code and Codex host directories."""
    results: list[tuple[str, Path]] = []
    host_roots = (
        ("claude-code", home_dir / ".claude" / "projects"),
        ("codex", home_dir / ".codex" / "sessions"),
    )
    for host_name, root in host_roots:
        if not root.is_dir():
            continue
        with contextlib.suppress(OSError):
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".jsonl", ".json"}:
                    results.append((host_name, path))
    return results


def discover_transcript_details(
    workspace_dir: Path | None = None,
    session_id: str | None = None,
    home_dir: Path | None = None,
) -> dict[str, Any]:
    """Discover matching transcript across workspace and host directories with provenance."""
    resolved_home = _resolve_home_dir(home_dir)
    rejected: list[dict[str, str]] = []
    scored_matches: list[tuple[tuple[Any, ...], Path, str, str]] = []

    run_data: dict[str, Any] = {}
    if workspace_dir and workspace_dir.is_dir():
        run_file = workspace_dir / "run.json"
        if run_file.is_file():
            with contextlib.suppress(OSError, ValueError):
                loaded = json.loads(run_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    run_data = loaded

    run_id = run_data.get("run_id") if isinstance(run_data.get("run_id"), str) else None
    repo_path = (
        run_data.get("repository", {}).get("path")
        if isinstance(run_data.get("repository"), dict)
        and isinstance(run_data["repository"].get("path"), str)
        else None
    )

    # 1. Inspect workspace candidates
    ws_candidates: list[Path] = []
    if workspace_dir and workspace_dir.is_dir():
        ws_candidates = list(find_candidate_transcripts(workspace_dir))
        if session_id:
            for extra_dir in (workspace_dir, workspace_dir / "logs"):
                if not extra_dir.is_dir():
                    continue
                for ext in (".jsonl", ".json", ".txt"):
                    extra = extra_dir / f"{session_id}{ext}"
                    if extra.is_file() and extra not in ws_candidates:
                        ws_candidates.append(extra)

    for cand in ws_candidates:
        mtime = cand.stat().st_mtime
        name_rank = (
            -CANDIDATE_TRANSCRIPTS.index(cand.name)
            if cand.name in CANDIDATE_TRANSCRIPTS
            else -len(CANDIDATE_TRANSCRIPTS)
        )
        root_rank = 0 if (workspace_dir and cand.parent == workspace_dir) else -1

        if session_id:
            meta = _inspect_transcript_metadata(cand)
            matched_sid, sid_reason = _matches_session_id(cand, meta, session_id)
            if not matched_sid:
                rejected.append(
                    {
                        "path": str(cand),
                        "reason": f"session_id mismatch (expected '{session_id}')",
                    }
                )
                continue
            score = (3, 1, 1, 1, 1, 1, mtime, name_rank, root_rank)
            scored_matches.append(
                (score, cand, "workspace", sid_reason or "Matched session_id")
            )
        else:
            score = (2, 1, 1, 1, 1, 1, mtime, name_rank, root_rank)
            scored_matches.append(
                (
                    score,
                    cand,
                    "workspace",
                    f"Matched workspace candidate '{cand.name}'",
                )
            )

    # 2. Inspect Claude Code and Codex host session directories
    expected_repo_slugs = _claude_project_slugs(repo_path) if repo_path else set()
    expected_ws_slugs = _claude_project_slugs(workspace_dir) if workspace_dir else set()
    ws_variants = _canonical_path_variants(workspace_dir) if workspace_dir else set()
    repo_variants = _canonical_path_variants(repo_path) if repo_path else set()

    for host_name, hpath in _find_host_transcript_files(resolved_home):
        meta = _inspect_transcript_metadata(hpath)
        mtime = hpath.stat().st_mtime
        not_sidechain = 0 if meta["is_sidechain"] else 1

        if session_id:
            matched_sid, sid_reason = _matches_session_id(hpath, meta, session_id)
            if not matched_sid:
                rejected.append(
                    {
                        "path": str(hpath),
                        "reason": f"session_id mismatch (expected '{session_id}')",
                    }
                )
                continue
            cwd_bonus = int(
                bool(
                    repo_path and any(_paths_equal(c, repo_path) for c in meta["cwds"])
                )
            )
            score = (3, 0, 1, cwd_bonus, 1, not_sidechain, mtime, 0, 0)
            scored_matches.append(
                (
                    score,
                    hpath,
                    host_name,
                    f"{sid_reason} in {host_name} host transcripts",
                )
            )
            continue

        # When workspace already has a direct local transcript and no session_id filter was asked,
        # record host sessions as superseded by the explicit workspace transcript.
        if scored_matches and any(m[2] == "workspace" for m in scored_matches):
            rejected.append(
                {
                    "path": str(hpath),
                    "reason": "superseded by workspace-local candidate transcript",
                }
            )
            continue

        if not workspace_dir:
            rejected.append(
                {
                    "path": str(hpath),
                    "reason": "no workspace or session_id supplied for host matching",
                }
            )
            continue

        raw_text = meta["raw_text"]
        has_run_id = bool(run_id and run_id in raw_text)
        has_ws_cwd = any(_paths_equal(c, workspace_dir) for c in meta["cwds"])
        has_ws_ref = has_ws_cwd or any(
            len(v) > 3 and v in raw_text.replace("\\", "/") for v in ws_variants
        )
        has_repo_cwd = bool(
            repo_path and any(_paths_equal(c, repo_path) for c in meta["cwds"])
        )

        # Check if candidate explicitly specifies a conflicting cwd
        has_conflicting_cwd = bool(
            meta["cwds"]
            and not has_repo_cwd
            and not has_ws_cwd
            and (repo_path is not None or not has_ws_ref)
        )

        # Check Claude project directory slug
        slug_match = False
        if host_name == "claude-code":
            projects_root = resolved_home / ".claude" / "projects"
            with contextlib.suppress(ValueError):
                rel_parts = hpath.relative_to(projects_root).parts
                if rel_parts:
                    cand_slug = rel_parts[0].strip("-").lower()
                    slug_match = cand_slug in (expected_repo_slugs | expected_ws_slugs)

        has_repo_ref = bool(
            repo_variants
            and not has_conflicting_cwd
            and any(
                len(v) > 3 and v in raw_text.replace("\\", "/") for v in repo_variants
            )
        )

        if has_conflicting_cwd and not has_run_id and not has_ws_ref:
            rejected.append(
                {
                    "path": str(hpath),
                    "reason": f"cwd mismatch ({meta['cwds'][0]!r} does not match {repo_path or str(workspace_dir)!r})",
                }
            )
            continue

        reasons: list[str] = []
        if has_run_id:
            reasons.append(f"run_id '{run_id}'")
        if has_ws_ref:
            reasons.append(f"workspace '{workspace_dir}'")
        if has_repo_cwd:
            reasons.append(f"repository cwd '{repo_path}'")
        elif slug_match and not has_conflicting_cwd:
            reasons.append(
                f"Claude project directory for '{repo_path or workspace_dir}'"
            )
        elif has_repo_ref:
            reasons.append(f"repository path '{repo_path}'")

        if not reasons:
            if not run_data:
                reason = "no matching workspace reference and workspace lacks run.json metadata"
            else:
                reason = f"no matching run_id ({run_id or 'none'}) or repository path ({repo_path or 'none'})"
            rejected.append({"path": str(hpath), "reason": reason})
            continue

        score = (
            1,
            0,
            int(has_run_id),
            int(has_ws_ref),
            int(has_repo_cwd or slug_match),
            not_sidechain,
            mtime,
            0,
            0,
        )
        scored_matches.append(
            (
                score,
                hpath,
                host_name,
                f"Matched {', '.join(reasons)} in {host_name} host transcripts",
            )
        )

    if not scored_matches:
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

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    best_score, best_path, best_source, best_reason = scored_matches[0]

    for other_score, other_path, _, _ in scored_matches[1:]:
        if other_score[5] < best_score[5]:
            superseded_reason = (
                "subagent sidechain transcript superseded by primary session"
            )
        elif other_score[6] < best_score[6]:
            superseded_reason = (
                f"older modification time than selected '{best_path.name}'"
            )
        else:
            superseded_reason = (
                f"lower match or naming priority than selected '{best_path.name}'"
            )
        rejected.append({"path": str(other_path), "reason": superseded_reason})

    return {
        "selected_transcript": best_path,
        "source": best_source,
        "selection_reason": best_reason,
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


def main() -> int:
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

    args = parser.parse_args()

    workspace = args.workspace or getattr(args, "workspace_dir", None)
    output_dir = args.output_dir or getattr(args, "output", None)
    transcript = args.transcript or getattr(args, "transcript_opt", None)
    discovery_meta: dict[str, Any] | None = None

    if not transcript:
        if workspace and workspace.is_dir():
            try:
                discovery_meta = discover_transcript_details(
                    workspace, session_id=args.session_id
                )
                transcript = Path(discovery_meta["selected_transcript"])
                print(f"Discovered transcript: {transcript}", file=sys.stderr)
            except TranscriptNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 2
        elif workspace:
            print(f"Error: Workspace directory not found: {workspace}", file=sys.stderr)
            return 2
        elif args.session_id:
            try:
                discovery_meta = discover_transcript_details(
                    None, session_id=args.session_id
                )
                transcript = Path(discovery_meta["selected_transcript"])
                print(f"Discovered transcript: {transcript}", file=sys.stderr)
            except TranscriptNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 2
        else:
            patterns_str = ", ".join(CANDIDATE_TRANSCRIPTS)
            print(
                f"Error: Transcript file path is required or specify --workspace <dir> containing a candidate transcript ({patterns_str})",
                file=sys.stderr,
            )
            return 2

    if not transcript.is_file():
        print(f"Error: Transcript file not found: {transcript}", file=sys.stderr)
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

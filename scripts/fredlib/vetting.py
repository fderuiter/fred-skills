"""Offline static security vetting for skill packages and repository agent configurations.

Uses only the Python 3.12 standard library and performs zero network requests.
"""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from .common import (
    ROOT,
    FredCliError,
    non_fenced_lines,
    parse_reference_link_definition,
    read_utf8_text,
)

__all__ = [
    "SKILL_CAPABILITY_ALLOWLIST",
    "vet_markdown",
    "vet_mcp_config",
    "vet_script",
    "vet_skill",
    "vet_target",
]

SKILL_CAPABILITY_ALLOWLIST: dict[str, frozenset[str]] = {
    "repo-audit-diagnostics": frozenset(
        {
            "read:~/.claude/projects",
            "read:~/.codex/sessions",
        }
    ),
}

_EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)
_EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
_EXCLUDED_FILE_NAMES = frozenset({".DS_Store", ".gitignore", ".gitattributes"})

_JS_TS_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"})
_SCRIPT_SUFFIXES = (
    frozenset({".py", ".sh", ".bash", ".zsh", ".ksh", ".ps1", ".bat", ".cmd"})
    | _JS_TS_SUFFIXES
)

_SAFE_URLLIB_MODULES = frozenset({"urllib.parse", "urllib.error"})

_PYTHON_NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "curl",
        "curl_cffi",
        "ftplib",
        "grpc",
        "http",
        "http.client",
        "http.cookiejar",
        "http.cookies",
        "http.server",
        "httpx",
        "imaplib",
        "paramiko",
        "poplib",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib",
        "urllib.request",
        "urllib.robotparser",
        "urllib3",
        "webbrowser",
        "websocket",
        "websockets",
        "wsgiref",
        "xmlrpc",
        "xmlrpc.client",
    }
)

_PYTHON_DYNAMIC_MODULES = frozenset({"importlib", "runpy"})
_PYTHON_FFI_MODULES = frozenset({"ctypes", "cffi"})

_PYTHON_NETWORK_CALLS = frozenset(
    {
        "urlopen",
        "urlretrieve",
        "create_connection",
        "HTTPConnection",
        "HTTPSConnection",
    }
)

_OS_EXEC_ATTRS = frozenset(
    {
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)

_PYTHON_SHELL_ATTR_CALLS: dict[str, frozenset[str]] = {
    "os": _OS_EXEC_ATTRS,
    "posix": _OS_EXEC_ATTRS,
    "nt": _OS_EXEC_ATTRS,
    "pty": frozenset({"spawn", "fork"}),
    "asyncio": frozenset({"create_subprocess_shell"}),
}

_SUBPROCESS_CALL_NAMES = frozenset(
    {
        "Popen",
        "call",
        "check_call",
        "check_output",
        "run",
        "getoutput",
        "getstatusoutput",
    }
)

_SHELL_BINARY_NAMES = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "ksh",
        "dash",
        "csh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }
)

_NETWORK_BINARY_NAMES = frozenset(
    {
        "curl",
        "curl.exe",
        "wget",
        "wget.exe",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "invoke-webrequest",
        "invoke-restmethod",
        "iwr",
        "irm",
    }
)

_REFLECTIVE_EXEC_MODULES = frozenset(
    {
        "os",
        "posix",
        "nt",
        "subprocess",
        "pty",
        "asyncio",
        "builtins",
        "__builtins__",
        "importlib",
        "runpy",
        "ctypes",
        "cffi",
    }
)

_SAFE_ENV_VARS = frozenset(
    {
        "CI",
        "COLUMNS",
        "GITHUB_ACTIONS",
        "GITHUB_BASE_REF",
        "GITHUB_HEAD_REF",
        "GITHUB_REF",
        "GITHUB_REPOSITORY",
        "GITHUB_SHA",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_WORKFLOW",
        "HOME",
        "LANG",
        "LC_ALL",
        "LINES",
        "LOGNAME",
        "NO_COLOR",
        "PATH",
        "PWD",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "USERNAME",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
)

_SECRET_ENV_PATTERN = re.compile(
    r"^(?:"
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|"
    r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"AZURE_CLIENT_SECRET|AZURE_OPENAI_API_KEY|"
    r"GITHUB_TOKEN|GH_TOKEN|GITLAB_TOKEN|BITBUCKET_TOKEN|"
    r"NPM_TOKEN|PYPI_API_TOKEN|CARGO_REGISTRY_TOKEN|"
    r"SLACK_TOKEN|SLACK_BOT_TOKEN|DISCORD_TOKEN|STRIPE_SECRET_KEY|"
    r"DATABASE_URL|REDIS_URL|PRIVATE_KEY|SSH_AUTH_SOCK|"
    r"[A-Z0-9_]*(?:_API_KEY|_SECRET_KEY|_ACCESS_TOKEN|_REFRESH_TOKEN|_PRIVATE_KEY|_CLIENT_SECRET|_PASSWORD|_PASSWD)"
    r")$"
)

# Ordered from most specific to more general so scoped capabilities match first.
_SENSITIVE_PATH_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(?:^|[\\/])\.claude[\\/]projects(?:$|[\\/])"),
        "read:~/.claude/projects",
        "~/.claude/projects",
    ),
    (
        re.compile(r"(?:^|[\\/])\.codex[\\/]sessions(?:$|[\\/])"),
        "read:~/.codex/sessions",
        "~/.codex/sessions",
    ),
    (
        re.compile(r"(?:^|[\\/])\.claude(?:$|[\\/])"),
        "read:~/.claude",
        "~/.claude",
    ),
    (
        re.compile(r"(?:^|[\\/])\.codex(?:$|[\\/])"),
        "read:~/.codex",
        "~/.codex",
    ),
    (
        re.compile(
            r"(?:^|[\\/])\.ssh(?:$|[\\/])|(?:^|[\\/])id_(?:rsa|ed25519|ecdsa|dsa)$"
        ),
        "read:~/.ssh",
        "~/.ssh",
    ),
    (
        re.compile(r"(?:^|[\\/])\.aws(?:$|[\\/])"),
        "read:~/.aws",
        "~/.aws",
    ),
    (
        re.compile(r"(?:^|[\\/])\.gnupg(?:$|[\\/])"),
        "read:~/.gnupg",
        "~/.gnupg",
    ),
    (
        re.compile(r"(?:^|[\\/])\.config[\\/]gh(?:$|[\\/])"),
        "read:~/.config/gh",
        "~/.config/gh",
    ),
    (
        re.compile(
            r"(?:^|[\\/])\.config[\\/]gcloud(?:$|[\\/])|application_default_credentials\.json$"
        ),
        "read:~/.config/gcloud",
        "~/.config/gcloud",
    ),
    (
        re.compile(r"(?:^|[\\/])\.kube(?:$|[\\/])"),
        "read:~/.kube",
        "~/.kube",
    ),
    (
        re.compile(r"(?:^|[\\/])\.docker[\\/]config\.json$"),
        "read:~/.docker",
        "~/.docker/config.json",
    ),
    (
        re.compile(r"(?:^|[\\/])\.netrc$"),
        "read:~/.netrc",
        "~/.netrc",
    ),
    (
        re.compile(r"(?:^|[\\/])\.npmrc$"),
        "read:~/.npmrc",
        "~/.npmrc",
    ),
    (
        re.compile(r"(?:^|[\\/])\.pypirc$"),
        "read:~/.pypirc",
        "~/.pypirc",
    ),
    (
        re.compile(r"(?:^|[\\/])\.git-credentials$"),
        "read:~/.git-credentials",
        "~/.git-credentials",
    ),
    (
        re.compile(r"^/etc/(?:shadow|master\.passwd|gshadow)$"),
        "read:/etc/shadow",
        "/etc/shadow",
    ),
    (
        re.compile(r"(?:^|[\\/])\.env(?:\.[A-Za-z0-9_-]+)?$"),
        "read:.env",
        ".env",
    ),
)

_SHELL_NETWORK_RE = re.compile(
    r"(?:^|[;&|()\s`])"
    r"(?:curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|ftp|telnet|"
    r"Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b"
    r"|/dev/(?:tcp|udp)/"
)

_SHELL_DYNAMIC_EXEC_RE = re.compile(
    r"(?:^|[;&|()\s`])eval\s+"
    r"|\b(?:sh|bash|zsh|ksh)\s+-c\b"
    r"|\bcmd(?:\.exe)?\s+/c\b"
    r"|\b(?:powershell|pwsh)(?:\.exe)?\s+-(?:e|enc|encodedcommand|command)\b"
    r"|\b(?:Invoke-Expression|iex)\b"
    r"|\|\s*(?:sudo\s+)?(?:sh|bash|zsh|ksh|python3?|pwsh|powershell)\b",
    re.IGNORECASE,
)

_SHELL_SENSITIVE_PATH_RE = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|%USERPROFILE%)[\\/]"
    r"(?:\.claude[\\/]projects|\.codex[\\/]sessions|\.claude|\.codex|"
    r"\.ssh|\.aws|\.gnupg|\.config[\\/]gh|\.config[\\/]gcloud|\.kube|"
    r"\.docker[\\/]config\.json|\.netrc|\.npmrc|\.pypirc|\.git-credentials)"
    r"|/etc/(?:shadow|master\.passwd|gshadow)"
    r"|(?:^|[\s'\"=])(?:\./)?\.env(?:\.[A-Za-z0-9_-]+)?(?=[\s'\";|&)]|$)"
)

_SHELL_ENV_SECRET_RE = re.compile(
    r"\$\{?("
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|CLAUDE_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|"
    r"AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|"
    r"GITHUB_TOKEN|GH_TOKEN|GITLAB_TOKEN|NPM_TOKEN|PYPI_API_TOKEN|"
    r"SLACK_TOKEN|DATABASE_URL|PRIVATE_KEY|"
    r"[A-Z0-9_]*(?:_API_KEY|_SECRET_KEY|_ACCESS_TOKEN|_PRIVATE_KEY|_CLIENT_SECRET|_PASSWORD)"
    r")\}?\b"
)

_JS_NETWORK_RE = re.compile(
    r"\bfetch\s*\("
    r"|\bnew\s+(?:WebSocket|XMLHttpRequest)\s*\("
    r"""|\b(?:require|import)\s*\(?[\s'"]+(?:node:)?(?:http|https|http2|net|tls|dgram|dns|undici|axios|node-fetch|ws)\b"""
    r"""|\bfrom\s+['"](?:node:)?(?:http|https|http2|net|tls|dgram|dns|undici|axios|node-fetch|ws)['"]"""
)

_JS_EXEC_RE = re.compile(
    r"""\b(?:require|import)\s*\(?[\s'"]+(?:node:)?child_process\b"""
    r"""|\bfrom\s+['"](?:node:)?child_process['"]"""
    r"|\b(?:exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\s*\("
    r"|\b(?:Deno\.Command|Bun\.spawn|Bun\.spawnSync)\b"
)

_JS_DYNAMIC_RE = re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(|\bFunction\s*\(")

_JS_ENV_ACCESS_RE = re.compile(
    r"\b(?:process\.env|Deno\.env\.get)\s*(?:\.\s*([A-Za-z_][A-Za-z0-9_]*)|\[\s*['\"]([^'\"]+)['\"]\s*\]|\(\s*['\"]([^'\"]+)['\"]\s*\))"
)

_JS_BULK_ENV_RE = re.compile(
    r"(?:Object\.(?:entries|keys|values|assign)|JSON\.stringify|\.\.\.)\s*\(?\s*process\.env\b"
    r"|\bDeno\.env\.toObject\s*\("
)

_JS_STRING_LITERAL_RE = re.compile(r"""['"`]([^'"`\n]{2,})['"`]""")

_HIDDEN_UNICODE_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x202A, 0x202E, "bidirectional override/embedding control"),
    (0x2066, 0x2069, "bidirectional isolate control"),
    (0x200E, 0x200F, "bidirectional mark"),
    (0x061C, 0x061C, "Arabic letter mark"),
    (0x200B, 0x200D, "zero-width space/joiner"),
    (0x2060, 0x2064, "invisible word-joiner/operator"),
    (0xFEFF, 0xFEFF, "zero-width no-break space"),
    (0x180E, 0x180E, "Mongolian vowel separator"),
    (0xE0000, 0xE007F, "Unicode tag character"),
    (0xE0100, 0xE01EF, "variation selector supplement"),
)

_INLINE_CODE_RE = re.compile(r"(`+)([^`]*?)\1")

_MD_INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_MD_REF_IMAGE_USE_RE = re.compile(r"!\[([^\]]*)\](?:\[([^\]]*)\])?")
_HTML_IMG_RE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:["']([^"']+)["']|([^\s>]+))""",
    re.IGNORECASE,
)

_MD_SHIM_TOKEN_RE = re.compile(r"(?:^|(?<=\s))@([^\s,;)>\"'`]+)")
_SHIM_DOC_SUFFIXES = frozenset(
    {
        ".md",
        ".mdc",
        ".markdown",
        ".txt",
        ".prompt",
        ".rules",
        ".json",
        ".yaml",
        ".yml",
    }
)

_SVG_ENTITY_DOCTYPE_RE = re.compile(
    r"<!ENTITY\b|<!DOCTYPE\b[^>]*(?:\bSYSTEM\b|\bPUBLIC\b|\[)",
    re.IGNORECASE | re.DOTALL,
)
_SVG_XML_STYLESHEET_RE = re.compile(
    r"""<\?xml-stylesheet\b[^>]*?\bhref\s*=\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_SVG_CSS_URL_RE = re.compile(
    r"""url\(\s*['"]?([^)'"\s]+)['"]?\s*\)|@import\s+(?:url\()?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
_SVG_ACTIVE_TAGS = frozenset({"script", "foreignobject", "iframe", "embed", "object"})
_SVG_URI_ATTRS = frozenset(
    {"href", "src", "data", "action", "formaction", "to", "from", "values"}
)

_PROMPT_OVERRIDE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+"
            r"(?:all\s+|any\s+|the\s+)?"
            r"(?:previous|prior|above|preceding|earlier|system|developer)\s+"
            r"(?:instructions?|prompts?|rules?|constraints?|directives?|guardrails?|guidelines?|context)\b",
            re.IGNORECASE,
        ),
        "Instruction override directive attempts to discard prior or system constraints",
    ),
    (
        re.compile(
            r"(?<!do not )(?<!don't )(?<!never )(?<!must not )(?<!cannot )(?<!not )"
            r"\b(?:override|bypass|disable|deactivate)\s+"
            r"(?:all\s+|any\s+|the\s+)?"
            r"(?:system\s+prompt|developer\s+instructions?|"
            r"safety\s+(?:filters?|guardrails?|rules?|checks?)|"
            r"security\s+(?:controls?|checks?|guardrails?))\b",
            re.IGNORECASE,
        ),
        "Safety or system prompt bypass directive detected",
    ),
    (
        re.compile(
            r"\byou\s+are\s+now\s+(?:in\s+)?"
            r"(?:developer\s+mode|jailbreak\s+mode|unrestricted\s+mode|dan\b|"
            r"free\s+from\s+(?:all\s+)?(?:rules|constraints|restrictions))\b",
            re.IGNORECASE,
        ),
        "Role hijack or unrestricted mode override phrase detected",
    ),
    (
        re.compile(
            r"(?:^|[:.!?\s])(?:new\s+system\s+prompt|system\s+override|admin\s+override)\s*:",
            re.IGNORECASE,
        ),
        "Explicit system override header detected",
    ),
)

_MCP_INLINE_SECRET_VALUE_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{16,}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[baprs]-[A-Za-z0-9_-]{10,}\b"
    r"|\bglpat-[A-Za-z0-9_-]{16,}\b"
    r"|\bBearer\s+[A-Za-z0-9._-]{16,}\b)"
)
_MCP_SECRET_KEY_NAME_RE = re.compile(
    r"(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE[_-]?KEY|AUTHORIZATION)$",
    re.IGNORECASE,
)
_ENV_PLACEHOLDER_RE = re.compile(
    r"^(?:\$\{?[A-Za-z_][A-Za-z0-9_]*(:?-[^}]*)?\}?|%[A-Za-z_][A-Za-z0-9_]*%|<[A-Za-z0-9_.-]+>|\{\{[^}]+\}\})$"
)

_DEFAULT_HOST_SKILL_DIRS: tuple[str, ...] = (
    ".claude/skills",
    ".agents/skills",
    ".cursor/skills",
    ".windsurf/skills",
    ".github/skills",
    ".gemini/skills",
    ".opencode/skills",
    ".kiro/skills",
)

_REPO_INSTRUCTION_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/CLAUDE.md",
    "GEMINI.md",
    ".gemini/GEMINI.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".github/copilot-instructions.md",
)

_REPO_INSTRUCTION_GLOBS: tuple[str, ...] = (
    ".cursor/rules/*.md",
    ".cursor/rules/*.mdc",
    ".claude/commands/*.md",
    ".claude/agents/*.md",
    ".github/instructions/*.md",
)

_REPO_MCP_FILES: tuple[str, ...] = (
    ".mcp.json",
    ".cursor/mcp.json",
    ".vscode/mcp.json",
    ".windsurf/mcp_config.json",
)

_REPO_HOST_MANIFEST_FILES: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".cursor-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor/hooks.json",
    "hooks/hooks.json",
    "gemini-extension.json",
)

_SETTINGS_HELPER_COMMAND_KEYS = frozenset(
    {
        "apiKeyHelper",
        "awsAuthRefresh",
        "awsCredentialExport",
        "otelHeadersHelper",
    }
)

_MANIFEST_PROMPT_KEYS = frozenset(
    {
        "description",
        "instructions",
        "prompt",
        "default_prompt",
        "short_description",
        "display_name",
        "systemPrompt",
        "appendSystemPrompt",
        "systemMessage",
    }
)

_YAML_KEY_VALUE_RE = re.compile(r"^([ \t]*)(-\s+)?([A-Za-z0-9_.-]+)\s*:(.*)$")
_YAML_LIST_ITEM_RE = re.compile(r"^([ \t]*)-\s+(.+)$")


@dataclass(frozen=True, slots=True)
class _ConfigSourceContext:
    raw_text: str
    file_label: str
    fallback_line: int = 1


@dataclass(frozen=True, slots=True)
class _BoundaryContext:
    base_dir: Path
    boundary_root: Path
    source_label: str
    category: str = "agent-config"


@dataclass(frozen=True, slots=True)
class _RepoScanContext:
    repo_root: Path
    root: Path
    allowed_capabilities: Collection[str] | None
    allowed: frozenset[str]
    seen_configs: set[str] = field(default_factory=set)
    configs_scanned: list[str] = field(default_factory=list)
    seen_skills: set[str] = field(default_factory=set)
    skills_scanned: list[str] = field(default_factory=list)
    vetted_md_targets: set[Path] = field(default_factory=set)
    findings: list[dict[str, Any]] = field(default_factory=list)
    active_chain: frozenset[Path] = frozenset()


def _mark_config_scanned(ctx: _RepoScanContext, rel_label: str) -> bool:
    if rel_label in ctx.seen_configs:
        return False
    ctx.seen_configs.add(rel_label)
    ctx.configs_scanned.append(rel_label)
    return True


def _format_file_label(path: Path, root: Path | None) -> str:
    if root is None:
        return path.as_posix()
    try:
        resolved_root = root.resolve()
        if not path.is_symlink():
            resolved_path = path.resolve()
            if resolved_path.is_relative_to(resolved_root):
                return resolved_path.relative_to(resolved_root).as_posix()
            return path.as_posix()
        resolved_parent = path.parent.resolve()
        if resolved_parent.is_relative_to(resolved_root):
            return (resolved_parent.relative_to(resolved_root) / path.name).as_posix()
    except OSError:
        return path.as_posix()
    return path.as_posix()


def _finding(
    file_label: str,
    line: int,
    rule: str,
    category: str,
    message: str,
) -> dict[str, Any]:
    return {
        "file": file_label,
        "line": max(1, line),
        "rule": rule,
        "severity": "high",
        "category": category,
        "message": message,
    }


def _with_severity(finding: dict[str, Any], severity: str) -> dict[str, Any]:
    finding["severity"] = severity
    return finding


def _normalize_capabilities(
    allowed_capabilities: Collection[str] | None,
) -> frozenset[str]:
    if not allowed_capabilities:
        return frozenset()
    normalized: set[str] = set()
    for raw in allowed_capabilities:
        if not isinstance(raw, str):
            continue
        token = raw.strip().replace("\\", "/")
        if not token:
            continue
        normalized.add(token)
        if token.startswith("~/."):
            normalized.add(f"read:{token}")
        elif token.startswith("."):
            normalized.add(f"read:~/{token}")
            normalized.add(f"read:{token}")
    return frozenset(normalized)


def _capability_permitted(capability: str, allowed: frozenset[str]) -> bool:
    if "all" in allowed or capability in allowed:
        return True
    if capability.startswith("read:~/"):
        parent = capability
        while "/" in parent[len("read:~/") :]:
            parent = parent.rsplit("/", 1)[0]
            if parent in allowed:
                return True
    return capability.startswith("env:") and "env:*" in allowed


def _process_capability_permitted(allowed: frozenset[str]) -> bool:
    return _capability_permitted("process-execution", allowed) or _capability_permitted(
        "shell-execution", allowed
    )


def _match_sensitive_path(raw_path: str) -> tuple[str, str] | None:
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate:
        return None
    for pattern, capability, label in _SENSITIVE_PATH_RULES:
        if pattern.search(candidate):
            return capability, label
    return None


def _joined_part_str(val: ast.AST) -> str | None:
    if isinstance(val, ast.Constant) and isinstance(val.value, str):
        return val.value
    if isinstance(val, ast.FormattedValue) and val.format_spec is None:
        return _const_str(val.value)
    return None


def _joined_const_str(node: ast.JoinedStr) -> str | None:
    parts: list[str] = []
    for val in node.values:
        part = _joined_part_str(val)
        if part is None:
            return None
        parts.append(part)
    return "".join(parts)


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _const_str(node.left)
        right = _const_str(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        return _joined_const_str(node)
    return None


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    curr = node
    while isinstance(curr, ast.Attribute):
        parts.append(curr.attr)
        curr = curr.value
    if isinstance(curr, ast.Name):
        parts.append(curr.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _is_network_module_name(mod: str) -> bool:
    if not mod:
        return False
    if mod in _SAFE_URLLIB_MODULES or any(
        mod.startswith(f"{safe}.") for safe in _SAFE_URLLIB_MODULES
    ):
        return False
    if mod in _PYTHON_NETWORK_MODULES:
        return True
    return any(mod.startswith(f"{net}.") for net in _PYTHON_NETWORK_MODULES)


def _is_dynamic_module_name(mod: str) -> bool:
    return mod in _PYTHON_DYNAMIC_MODULES or any(
        mod.startswith(f"{dyn}.") for dyn in _PYTHON_DYNAMIC_MODULES
    )


def _is_ffi_module_name(mod: str) -> bool:
    return mod in _PYTHON_FFI_MODULES or any(
        mod.startswith(f"{ffi}.") for ffi in _PYTHON_FFI_MODULES
    )


class _PythonSecurityVisitor(ast.NodeVisitor):
    def __init__(self, file_label: str, allowed: frozenset[str]) -> None:
        self.file_label = file_label
        self.allowed = allowed
        self.findings: list[dict[str, Any]] = []
        self._imported_aliases: dict[str, str] = {}
        self._attrgetters: dict[str, list[str | None]] = {}
        self._path_vars: dict[str, list[str]] = {}
        self._safe_environ_nodes: set[int] = set()
        self._checked_path_nodes: set[int] = set()
        self._seen_keys: set[tuple[int, str, str]] = set()

    def _add(
        self, line: int, rule: str, message: str, *, severity: str = "high"
    ) -> None:
        key = (max(1, line), rule, message)
        if key not in self._seen_keys:
            self._seen_keys.add(key)
            self.findings.append(
                _with_severity(
                    _finding(self.file_label, line, rule, "script", message),
                    severity,
                )
            )

    def _check_imported_target(self, line: int, target_mod: str, label: str) -> None:
        if _is_network_module_name(target_mod) and not _capability_permitted(
            "network", self.allowed
        ):
            self._add(
                line,
                "network-client",
                f"Python script imports network module '{label}'",
            )
        if _is_dynamic_module_name(target_mod) and not _capability_permitted(
            "dynamic-execution", self.allowed
        ):
            self._add(
                line,
                "dynamic-execution",
                f"Python script imports dynamic loader module '{label}'",
            )
        if target_mod == "builtins" and not _capability_permitted(
            "dynamic-execution", self.allowed
        ):
            self._add(
                line,
                "dynamic-execution",
                "Python script imports 'builtins' namespace directly",
            )
        if _is_ffi_module_name(target_mod) and not _capability_permitted(
            "native-ffi", self.allowed
        ):
            self._add(
                line,
                "dynamic-execution",
                f"Python script imports native FFI module '{label}'",
            )
        if (
            target_mod == "subprocess" or target_mod.startswith("subprocess.")
        ) and not _process_capability_permitted(self.allowed):
            self._add(
                line,
                "process-execution",
                f"Python script imports process execution module '{label}' (requires capability 'process-execution')",
            )
        if (
            target_mod == "pty" or target_mod.startswith("pty.")
        ) and not _capability_permitted("shell-execution", self.allowed):
            self._add(
                line,
                "shell-execution",
                f"Python script imports pseudo-terminal execution module '{label}'",
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name
            if alias.asname:
                self._imported_aliases[alias.asname] = mod
            else:
                top = mod.split(".")[0]
                self._imported_aliases[top] = top
            self._check_imported_target(node.lineno, mod, mod)
        self.generic_visit(node)

    def _check_import_from_network(
        self, line: int, base_mod: str, alias_name: str, full: str
    ) -> None:
        if _capability_permitted("network", self.allowed):
            return
        if _is_network_module_name(full):
            self._add(
                line,
                "network-client",
                f"Python script imports network client '{full}'",
            )
            return
        if (
            base_mod not in {"", "urllib"}
            and _is_network_module_name(base_mod)
            and full not in _SAFE_URLLIB_MODULES
        ):
            self._add(
                line,
                "network-client",
                f"Python script imports network client '{base_mod}'",
            )
            return
        if alias_name in _PYTHON_NETWORK_CALLS:
            self._add(
                line,
                "network-client",
                f"Python script imports network client '{full}'",
            )

    def _check_import_from_loaders(self, line: int, base_mod: str, full: str) -> None:
        if (
            _is_dynamic_module_name(base_mod) or _is_dynamic_module_name(full)
        ) and not _capability_permitted("dynamic-execution", self.allowed):
            self._add(
                line,
                "dynamic-execution",
                f"Python script imports dynamic loader '{full}'",
            )
        if base_mod == "builtins" and not _capability_permitted(
            "dynamic-execution", self.allowed
        ):
            self._add(
                line,
                "dynamic-execution",
                f"Python script imports builtin '{full}'",
            )
        if (
            _is_ffi_module_name(base_mod) or _is_ffi_module_name(full)
        ) and not _capability_permitted("native-ffi", self.allowed):
            self._add(
                line,
                "dynamic-execution",
                f"Python script imports native FFI symbol '{full}'",
            )

    def _check_import_from_process(
        self, line: int, base_mod: str, alias_name: str, full: str
    ) -> None:
        if base_mod == "subprocess" and not _process_capability_permitted(self.allowed):
            self._add(
                line,
                "process-execution",
                f"Python script imports process execution symbol '{full}' (requires capability 'process-execution')",
            )
        if alias_name in _PYTHON_SHELL_ATTR_CALLS.get(base_mod, frozenset()):
            if not _capability_permitted("shell-execution", self.allowed):
                self._add(
                    line,
                    "shell-execution",
                    f"Python script imports shell/process execution '{full}'",
                )
        elif base_mod == "pty" and not _capability_permitted(
            "shell-execution", self.allowed
        ):
            self._add(
                line,
                "shell-execution",
                f"Python script imports pseudo-terminal symbol '{full}'",
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base_mod = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self._check_imported_target(node.lineno, base_mod, base_mod)
                continue
            full = f"{base_mod}.{alias.name}" if base_mod else alias.name
            bound = alias.asname or alias.name
            self._imported_aliases[bound] = full
            self._check_import_from_network(node.lineno, base_mod, alias.name, full)
            self._check_import_from_loaders(node.lineno, base_mod, full)
            self._check_import_from_process(node.lineno, base_mod, alias.name, full)
        self.generic_visit(node)

    def _resolve_sys_modules_lookup(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Subscript):
            container = self._resolve_callee(node.value)
            if container == "sys.modules":
                return _const_str(node.slice) or "sys.modules"
        if isinstance(node, ast.Call):
            callee = self._resolve_callee(node.func)
            if callee == "sys.modules.get" and node.args:
                return _const_str(node.args[0]) or "sys.modules"
        return None

    def _resolve_attrgetter_attr(self, func_expr: ast.AST) -> str | None:
        if (
            isinstance(func_expr, ast.Call)
            and len(func_expr.args) == 1
            and self._resolve_callee(func_expr.func) == "operator.attrgetter"
        ):
            return _const_str(func_expr.args[0])
        if isinstance(func_expr, ast.Name) and func_expr.id in self._attrgetters:
            attrs = self._attrgetters[func_expr.id]
            return attrs[0] if len(attrs) == 1 else None
        return None

    def _resolve_call_callee(self, func: ast.Call) -> str | None:
        inner_callee = self._resolve_callee(func.func)
        if inner_callee in {"getattr", "builtins.getattr"} and len(func.args) >= 2:
            target = self._resolve_callee(func.args[0])
            attr = _const_str(func.args[1])
            if target and attr:
                return f"{target}.{attr}"
        if not func.args:
            return None
        ag_attr = self._resolve_attrgetter_attr(func.func)
        if not ag_attr:
            return None
        target = self._resolve_callee(func.args[0])
        return f"{target}.{ag_attr}" if target else None

    def _resolve_callee(self, func: ast.AST) -> str | None:
        sys_mod = self._resolve_sys_modules_lookup(func)
        if sys_mod is not None:
            return sys_mod
        if isinstance(func, ast.Call):
            return self._resolve_call_callee(func)
        raw = _dotted_name(func)
        if raw is None:
            return None
        head, _, tail = raw.partition(".")
        if head in self._imported_aliases:
            resolved_head = self._imported_aliases[head]
            return f"{resolved_head}.{tail}" if tail else resolved_head
        return raw

    def _is_known_module_or_symbol(self, resolved: str | None) -> bool:
        if not resolved:
            return False
        root_name = resolved.split(".")[0]
        return (
            root_name in self._imported_aliases.values()
            or any(
                val == root_name or val.startswith(f"{root_name}.")
                for val in self._imported_aliases.values()
            )
            or root_name in _REFLECTIVE_EXEC_MODULES
            or _is_network_module_name(resolved)
            or root_name in {"urllib", "http", "sys", "operator"}
        )

    def _record_assignment(self, target: ast.AST, value: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        var_name = target.id
        if isinstance(value, ast.Call):
            val_callee = self._resolve_callee(value.func)
            if val_callee == "operator.attrgetter" and value.args:
                self._attrgetters[var_name] = [_const_str(arg) for arg in value.args]
                return

        resolved_val = self._resolve_callee(value)
        if resolved_val and self._is_known_module_or_symbol(resolved_val):
            self._imported_aliases[var_name] = resolved_val

        path_parts = self._extract_path_parts(value)
        if path_parts is not None:
            self._path_vars[var_name] = path_parts

    def visit_Assign(self, node: ast.Assign) -> None:
        for tgt in node.targets:
            self._record_assignment(tgt, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._record_assignment(node.target, node.value)
        self.generic_visit(node)

    def _is_os_environ_expr(self, node: ast.AST) -> bool:
        resolved = self._resolve_callee(node)
        return resolved in {"os.environ", "os.environb", "posix.environ", "nt.environ"}

    def _check_reflective_os_family(
        self,
        line: int,
        target_name: str,
        attr_values: list[str | None],
        *,
        via: str,
    ) -> None:
        root_mod = target_name.split(".")[0]
        shell_attrs = _PYTHON_SHELL_ATTR_CALLS.get(root_mod, frozenset())
        for attr in attr_values:
            if attr in {"environ", "environb", "getenv", "getenvb"}:
                if not _capability_permitted("env:*", self.allowed):
                    self._add(
                        line,
                        "undeclared-credential-read",
                        f"Reflective '{via}' environment lookup '{target_name}.{attr}' (requires capability 'env:*')",
                    )
                return
            if attr in shell_attrs:
                if not _capability_permitted("shell-execution", self.allowed):
                    self._add(
                        line,
                        "shell-execution",
                        f"Reflective '{via}' lookup invokes '{target_name}.{attr}'",
                    )
                return
        if not _capability_permitted("dynamic-execution", self.allowed):
            self._add(
                line,
                "dynamic-execution",
                f"Reflective '{via}' lookup on execution module '{target_name}'",
            )

    def _check_reflective_exec_module(
        self,
        line: int,
        target_name: str,
        attr_values: list[str | None],
        *,
        via: str,
    ) -> None:
        root_mod = target_name.split(".")[0]
        net_attr = next(
            (
                a
                for a in attr_values
                if a and _is_network_module_name(f"{target_name}.{a}")
            ),
            None,
        )
        if net_attr is not None:
            if not _capability_permitted("network", self.allowed):
                self._add(
                    line,
                    "network-client",
                    f"Reflective '{via}' lookup '{target_name}.{net_attr}' accesses network client",
                )
            return
        if root_mod == "subprocess":
            if not _process_capability_permitted(self.allowed):
                self._add(
                    line,
                    "process-execution",
                    f"Reflective '{via}' lookup on '{target_name}' spawns external processes (requires capability 'process-execution')",
                )
            return
        if root_mod in {"os", "posix", "nt", "pty", "asyncio"}:
            self._check_reflective_os_family(line, target_name, attr_values, via=via)
            return
        if root_mod in _PYTHON_FFI_MODULES:
            if not _capability_permitted("native-ffi", self.allowed):
                self._add(
                    line,
                    "dynamic-execution",
                    f"Reflective '{via}' lookup on native FFI module '{target_name}'",
                )
            return
        if not _capability_permitted("dynamic-execution", self.allowed):
            self._add(
                line,
                "dynamic-execution",
                f"Reflective '{via}' lookup on '{target_name}'",
            )

    def _check_reflective_lookup(
        self,
        line: int,
        target_node: ast.AST,
        attr_values: list[str | None],
        *,
        via: str,
    ) -> None:
        target_name = self._resolve_callee(target_node)
        if not target_name:
            return
        if _is_network_module_name(target_name) or target_name in {"urllib", "http"}:
            if not _capability_permitted("network", self.allowed):
                self._add(
                    line,
                    "network-client",
                    f"Reflective '{via}' lookup on network module '{target_name}'",
                )
            return
        if target_name == "sys.modules":
            if not _capability_permitted("dynamic-execution", self.allowed):
                self._add(
                    line,
                    "dynamic-execution",
                    f"Reflective '{via}' lookup on 'sys.modules'",
                )
            return
        if target_name.split(".")[0] in _REFLECTIVE_EXEC_MODULES:
            self._check_reflective_exec_module(line, target_name, attr_values, via=via)

    def _single_subprocess_token(self, node: ast.AST) -> str | None:
        direct = _const_str(node)
        if direct is not None:
            return direct
        parts = self._extract_path_parts(node)
        return "/".join(parts) if parts else None

    def _extract_subprocess_arg_tokens(self, node: ast.Call) -> list[str]:
        candidates: list[ast.AST] = list(node.args[:1])
        candidates.extend(
            kw.value for kw in node.keywords if kw.arg in {"args", "executable"}
        )
        tokens: list[str] = []
        for item in candidates:
            elts = (
                list(item.elts) if isinstance(item, (ast.List, ast.Tuple)) else [item]
            )
            for elt in elts:
                tok = self._single_subprocess_token(elt)
                if tok is not None:
                    tokens.append(tok)
        return tokens

    @staticmethod
    def _call_enables_shell(node: ast.Call, short: str) -> bool:
        if short in {"getoutput", "getstatusoutput"}:
            return True
        for kw in node.keywords:
            if kw.arg == "shell" and not (
                isinstance(kw.value, ast.Constant) and kw.value.value is False
            ):
                return True
        return False

    def _inspect_subprocess_python_dash_c(
        self, line: int, first_bin: str, tokens: list[str]
    ) -> None:
        if not (first_bin.startswith("python") and "-c" in tokens):
            return
        c_idx = tokens.index("-c")
        if c_idx + 1 >= len(tokens):
            return
        for sub_finding in _vet_python_source(
            tokens[c_idx + 1], self.file_label, self.allowed
        ):
            self._add(
                line,
                sub_finding["rule"],
                sub_finding["message"],
                severity=sub_finding["severity"],
            )

    def _inspect_subprocess_tokens(
        self, line: int, callee: str, tokens: list[str]
    ) -> None:
        joined = " ".join(tokens)
        first_bin = Path(tokens[0].strip().replace("\\", "/")).name.lower()
        if (
            first_bin in _NETWORK_BINARY_NAMES
            or _SHELL_NETWORK_RE.search(joined) is not None
        ) and not _capability_permitted("network", self.allowed):
            self._add(
                line,
                "network-client",
                f"Subprocess call '{callee}' invokes network command '{tokens[0]}'",
            )
        if (
            first_bin in _SHELL_BINARY_NAMES
            or _SHELL_DYNAMIC_EXEC_RE.search(joined) is not None
        ) and not _capability_permitted("shell-execution", self.allowed):
            self._add(
                line,
                "shell-execution",
                f"Subprocess call '{callee}' invokes shell interpreter '{tokens[0]}'",
            )
        self._inspect_subprocess_python_dash_c(line, first_bin, tokens)
        self._inspect_subprocess_credentials(line, first_bin, tokens, joined)

    def _inspect_subprocess_credentials(
        self, line: int, first_bin: str, tokens: list[str], joined: str
    ) -> None:
        for tok in tokens:
            self._check_path_string(line, tok)
        for path_match in _SHELL_SENSITIVE_PATH_RE.finditer(joined):
            self._check_path_string(line, path_match.group(0))
        for env_match in _SHELL_ENV_SECRET_RE.finditer(joined):
            self._check_env_name(line, env_match.group(1))
        if first_bin in {"printenv", "env"}:
            for tok in tokens[1:]:
                self._check_env_name(line, tok)

    def _inspect_subprocess_call(self, node: ast.Call, callee: str, short: str) -> None:
        if self._call_enables_shell(node, short):
            if not _capability_permitted("shell-execution", self.allowed):
                self._add(
                    node.lineno,
                    "shell-execution",
                    f"Call to '{callee}' enables shell execution",
                )
        elif not _process_capability_permitted(self.allowed):
            self._add(
                node.lineno,
                "process-execution",
                f"Call to '{callee}' spawns an external process (requires capability 'process-execution')",
            )
        tokens = self._extract_subprocess_arg_tokens(node)
        if tokens:
            self._inspect_subprocess_tokens(node.lineno, callee, tokens)

    def _check_env_name(self, line: int, env_name: str) -> None:
        cleaned = env_name.strip()
        if cleaned in _SAFE_ENV_VARS or cleaned.startswith("FRED_"):
            return
        if _SECRET_ENV_PATTERN.match(cleaned):
            cap = f"env:{cleaned}"
            if not _capability_permitted(cap, self.allowed):
                self._add(
                    line,
                    "undeclared-credential-read",
                    f"Undeclared credential environment read '{cleaned}' (requires capability '{cap}')",
                )

    def _check_env_access(self, line: int, key_node: ast.AST | None) -> None:
        key_str = _const_str(key_node)
        if key_str is None:
            return
        self._check_env_name(line, key_str)

    def _check_bulk_env_read(self, line: int) -> None:
        if not _capability_permitted("env:*", self.allowed):
            self._add(
                line,
                "undeclared-credential-read",
                "Undeclared bulk environment read 'os.environ' (requires capability 'env:*')",
            )

    def _check_path_string(self, line: int, raw_path: str) -> None:
        matched = _match_sensitive_path(raw_path)
        if matched is None:
            return
        cap, label = matched
        if not _capability_permitted(cap, self.allowed):
            self._add(
                line,
                "undeclared-credential-read",
                f"Undeclared sensitive path access '{label}' (requires capability '{cap}')",
            )

    def _extract_div_path_parts(self, node: ast.BinOp) -> list[str]:
        left_parts = self._extract_path_parts(node.left)
        if left_parts is None:
            left_parts = (
                [f"<{node.left.id}>"] if isinstance(node.left, ast.Name) else ["<expr>"]
            )
        right_str = _const_str(node.right)
        right_part = right_str if right_str is not None else "<expr>"
        return [*left_parts, right_part]

    def _extract_attr_call_path_parts(
        self, func: ast.Attribute, args: list[ast.expr], short: str | None
    ) -> list[str] | None:
        attr = func.attr
        if attr == "home" and short == "home":
            return ["~"]
        if attr == "joinpath":
            base_parts = self._extract_path_parts(func.value) or ["<path>"]
            arg_parts = [_const_str(a) or "<expr>" for a in args]
            return [*base_parts, *arg_parts]
        if attr in {"expanduser", "resolve", "absolute"} and not args:
            exp_parts = self._extract_path_parts(func.value)
            return exp_parts if exp_parts is not None else ["~"]
        return None

    def _extract_call_path_parts(self, node: ast.Call) -> list[str] | None:
        callee = self._resolve_callee(node.func)
        short = callee.rsplit(".", 1)[-1] if callee else None
        if isinstance(node.func, ast.Attribute):
            attr_res = self._extract_attr_call_path_parts(node.func, node.args, short)
            if attr_res is not None:
                return attr_res
        if short not in {
            "Path",
            "PurePath",
            "PurePosixPath",
            "PureWindowsPath",
            "expanduser",
            "join",
        }:
            return None
        if not node.args:
            return ["."]
        parts: list[str] = []
        for arg in node.args:
            sub = self._extract_path_parts(arg)
            parts.extend(sub if sub is not None else ["<expr>"])
        return parts

    def _extract_path_parts(self, node: ast.AST) -> list[str] | None:
        str_val = _const_str(node)
        if str_val is not None:
            return [str_val]
        if isinstance(node, ast.Name):
            return (
                list(self._path_vars[node.id]) if node.id in self._path_vars else None
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._extract_div_path_parts(node)
        if isinstance(node, ast.Call):
            return self._extract_call_path_parts(node)
        return None

    def _mark_inner_path_nodes(self, node: ast.AST) -> None:
        self._checked_path_nodes.add(id(node))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            self._mark_inner_path_nodes(node.left)
            self._mark_inner_path_nodes(node.right)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "joinpath",
                "expanduser",
                "resolve",
                "absolute",
                "home",
            }:
                self._mark_inner_path_nodes(node.func.value)
            callee = self._resolve_callee(node.func)
            short = callee.rsplit(".", 1)[-1] if callee else ""
            if short in {
                "Path",
                "PurePath",
                "PurePosixPath",
                "PureWindowsPath",
                "expanduser",
                "join",
                "joinpath",
            }:
                for arg in node.args:
                    self._mark_inner_path_nodes(arg)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            if node.id == "__builtins__" and not _capability_permitted(
                "dynamic-execution", self.allowed
            ):
                self._add(
                    node.lineno,
                    "dynamic-execution",
                    "Python script accesses '__builtins__' namespace directly",
                )
            elif id(node) not in self._safe_environ_nodes and self._is_os_environ_expr(
                node
            ):
                self._check_bulk_env_read(node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            if id(node) not in self._safe_environ_nodes and self._is_os_environ_expr(
                node
            ):
                self._safe_environ_nodes.add(id(node.value))
                self._check_bulk_env_read(node.lineno)

            resolved = self._resolve_callee(node)
            if resolved:
                mod_prefix, _, attr_name = resolved.rpartition(".")
                if attr_name in _PYTHON_SHELL_ATTR_CALLS.get(
                    mod_prefix, frozenset()
                ) and not _capability_permitted("shell-execution", self.allowed):
                    self._add(
                        node.lineno,
                        "shell-execution",
                        f"Python script invokes shell/process execution '{resolved}'",
                    )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
            and self._is_os_environ_expr(node.comparators[0])
        ):
            key_str = _const_str(node.left)
            if key_str is not None:
                self._safe_environ_nodes.add(id(node.comparators[0]))
                self._check_env_name(node.lineno, key_str)
        self.generic_visit(node)

    def _check_call_environ_method(self, node: ast.Call) -> None:
        if not (
            isinstance(node.func, ast.Attribute)
            and self._is_os_environ_expr(node.func.value)
            and node.func.attr in {"get", "pop", "setdefault"}
            and node.args
        ):
            return
        key_str = _const_str(node.args[0])
        if key_str is not None:
            self._safe_environ_nodes.add(id(node.func))
            self._safe_environ_nodes.add(id(node.func.value))
            self._check_env_name(node.lineno, key_str)

    def _check_call_attrgetter(self, node: ast.Call) -> None:
        if not node.args:
            return
        if isinstance(node.func, ast.Call):
            if self._resolve_callee(node.func.func) == "operator.attrgetter":
                attrs = [_const_str(arg) for arg in node.func.args]
                self._check_reflective_lookup(
                    node.lineno, node.args[0], attrs, via="operator.attrgetter"
                )
        elif isinstance(node.func, ast.Name) and node.func.id in self._attrgetters:
            self._check_reflective_lookup(
                node.lineno,
                node.args[0],
                self._attrgetters[node.func.id],
                via="operator.attrgetter",
            )

    def _check_call_network_or_dynamic(
        self, node: ast.Call, callee: str, short: str, mod_prefix: str
    ) -> None:
        if callee in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            self._check_reflective_lookup(
                node.lineno,
                node.args[0],
                [_const_str(node.args[1])],
                via="getattr",
            )
        if (
            _is_network_module_name(callee) or short in _PYTHON_NETWORK_CALLS
        ) and not _capability_permitted("network", self.allowed):
            self._add(
                node.lineno,
                "network-client",
                f"Python script invokes network client '{callee}'",
            )
        if (
            callee
            in {
                "eval",
                "exec",
                "builtins.eval",
                "builtins.exec",
                "__import__",
                "builtins.__import__",
            }
            or _is_dynamic_module_name(callee)
            or _is_dynamic_module_name(mod_prefix)
        ) and not _capability_permitted("dynamic-execution", self.allowed):
            self._add(
                node.lineno,
                "dynamic-execution",
                f"Python script invokes dynamic code execution '{callee}'",
            )
        if callee in {"compile", "builtins.compile"} and len(node.args) >= 3:
            mode_val = _const_str(node.args[2])
            if mode_val in {"eval", "exec", "single"} and not _capability_permitted(
                "dynamic-execution", self.allowed
            ):
                self._add(
                    node.lineno,
                    "dynamic-execution",
                    f"Python script compiles dynamic '{mode_val}' code object",
                )

    def _check_call_process_or_io(
        self, node: ast.Call, callee: str, short: str
    ) -> None:
        mod_prefix, _, attr_name = callee.rpartition(".")
        if attr_name in _PYTHON_SHELL_ATTR_CALLS.get(
            mod_prefix, frozenset()
        ) and not _capability_permitted("shell-execution", self.allowed):
            self._add(
                node.lineno,
                "shell-execution",
                f"Python script invokes shell/process execution '{callee}'",
            )
        if (
            mod_prefix == "subprocess"
            or (not mod_prefix and short in _SUBPROCESS_CALL_NAMES)
            or callee == "asyncio.create_subprocess_exec"
        ):
            self._inspect_subprocess_call(node, callee, short)
        if (
            callee in {"os.getenv", "os.getenvb", "posix.getenv", "nt.getenv"}
            and node.args
        ):
            key_str = _const_str(node.args[0])
            if key_str is not None:
                self._check_env_name(node.lineno, key_str)
            else:
                self._check_bulk_env_read(node.lineno)
        if short == "open" and node.args:
            arg_parts = self._extract_path_parts(node.args[0])
            if arg_parts:
                self._mark_inner_path_nodes(node.args[0])
                self._check_path_string(node.lineno, "/".join(arg_parts))

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call_environ_method(node)
        self._check_call_attrgetter(node)

        sys_mod = self._resolve_sys_modules_lookup(node)
        if sys_mod and sys_mod != "sys.modules":
            self._check_imported_target(node.lineno, sys_mod, sys_mod)

        callee = self._resolve_callee(node.func)
        if callee:
            short = callee.rsplit(".", 1)[-1]
            mod_prefix, _, _ = callee.rpartition(".")
            self._check_call_network_or_dynamic(node, callee, short, mod_prefix)
            self._check_call_process_or_io(node, callee, short)

        if id(node) not in self._checked_path_nodes:
            path_parts = self._extract_path_parts(node)
            if path_parts is not None:
                self._mark_inner_path_nodes(node)
                self._check_path_string(node.lineno, "/".join(path_parts))

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_os_environ_expr(node.value):
            self._safe_environ_nodes.add(id(node.value))
            key_str = _const_str(node.slice)
            if key_str is not None:
                self._check_env_name(node.lineno, key_str)
            else:
                self._check_bulk_env_read(node.lineno)
        else:
            sys_mod = self._resolve_sys_modules_lookup(node)
            if sys_mod and sys_mod != "sys.modules":
                self._check_imported_target(node.lineno, sys_mod, sys_mod)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div) and id(node) not in self._checked_path_nodes:
            parts = self._extract_path_parts(node)
            if parts is not None:
                self._mark_inner_path_nodes(node)
                self._check_path_string(node.lineno, "/".join(parts))
        self.generic_visit(node)


def _vet_python_source(
    source: str, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source, filename=file_label)
    except SyntaxError as exc:
        findings = [
            _with_severity(
                _finding(
                    file_label,
                    exc.lineno or 1,
                    "syntax-error",
                    "script",
                    f"Unable to parse Python script AST: {exc.msg}",
                ),
                "medium",
            )
        ]
        findings.extend(_vet_shell_source(source, file_label, allowed))
        return findings

    visitor = _PythonSecurityVisitor(file_label, allowed)
    visitor.visit(tree)
    return visitor.findings


def _vet_shell_line_credentials(
    raw_line: str, line_no: int, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path_match in _SHELL_SENSITIVE_PATH_RE.finditer(raw_line):
        matched = _match_sensitive_path(path_match.group(0))
        if matched is None:
            continue
        cap, label = matched
        if not _capability_permitted(cap, allowed):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "undeclared-credential-read",
                    "script",
                    f"Undeclared sensitive path access '{label}' (requires capability '{cap}')",
                )
            )
    for env_match in _SHELL_ENV_SECRET_RE.finditer(raw_line):
        env_name = env_match.group(1)
        if env_name in _SAFE_ENV_VARS or env_name.startswith("FRED_"):
            continue
        cap = f"env:{env_name}"
        if not _capability_permitted(cap, allowed):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "undeclared-credential-read",
                    "script",
                    f"Undeclared credential environment read '{env_name}' (requires capability '{cap}')",
                )
            )
    return findings


def _vet_shell_line(
    raw_line: str, line_no: int, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    stripped = raw_line.strip()
    if not stripped or (stripped.startswith("#") and not stripped.startswith("#!")):
        return []
    findings: list[dict[str, Any]] = []
    if _SHELL_NETWORK_RE.search(raw_line) and not _capability_permitted(
        "network", allowed
    ):
        findings.append(
            _finding(
                file_label,
                line_no,
                "network-client",
                "script",
                "Script invokes a network client or socket device",
            )
        )
    if _SHELL_DYNAMIC_EXEC_RE.search(raw_line) and not _capability_permitted(
        "shell-execution", allowed
    ):
        findings.append(
            _finding(
                file_label,
                line_no,
                "shell-execution",
                "script",
                "Script uses dynamic eval, subshell '-c', or pipe-to-shell execution",
            )
        )
    findings.extend(_vet_shell_line_credentials(raw_line, line_no, file_label, allowed))
    return findings


def _vet_shell_source(
    source: str, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(source.splitlines(), start=1):
        findings.extend(_vet_shell_line(raw_line, idx, file_label, allowed))
    return findings


def _strip_js_block_comments_line(
    raw_line: str, in_block_comment: bool
) -> tuple[str, bool]:
    line = raw_line
    if in_block_comment:
        if "*/" not in line:
            return "", True
        line = line.split("*/", 1)[1]
        in_block_comment = False
    while "/*" in line:
        before, after = line.split("/*", 1)
        if "*/" not in after:
            return before, True
        line = before + " " + after.split("*/", 1)[1]
    return line, in_block_comment


def _vet_js_ts_line_credentials(
    line: str, line_no: int, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for env_match in _JS_ENV_ACCESS_RE.finditer(line):
        env_name = (
            env_match.group(1) or env_match.group(2) or env_match.group(3) or ""
        ).strip()
        if (
            not env_name
            or env_name in _SAFE_ENV_VARS
            or env_name.startswith("FRED_")
            or not _SECRET_ENV_PATTERN.match(env_name)
        ):
            continue
        cap = f"env:{env_name}"
        if not _capability_permitted(cap, allowed):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "undeclared-credential-read",
                    "script",
                    f"Undeclared credential environment read '{env_name}' (requires capability '{cap}')",
                )
            )
    for lit_match in _JS_STRING_LITERAL_RE.finditer(line):
        matched = _match_sensitive_path(lit_match.group(1))
        if matched is None:
            continue
        cap, label = matched
        if not _capability_permitted(cap, allowed):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "undeclared-credential-read",
                    "script",
                    f"Undeclared sensitive path access '{label}' (requires capability '{cap}')",
                )
            )
    return findings


def _vet_js_ts_line(
    line: str, line_no: int, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if _JS_NETWORK_RE.search(line) and not _capability_permitted("network", allowed):
        findings.append(
            _finding(
                file_label,
                line_no,
                "network-client",
                "script",
                "JavaScript/TypeScript script invokes a network client",
            )
        )
    if _JS_EXEC_RE.search(line) and not _process_capability_permitted(allowed):
        findings.append(
            _finding(
                file_label,
                line_no,
                "shell-execution",
                "script",
                "JavaScript/TypeScript script invokes child process execution",
            )
        )
    if _JS_DYNAMIC_RE.search(line) and not _capability_permitted(
        "dynamic-execution", allowed
    ):
        findings.append(
            _finding(
                file_label,
                line_no,
                "dynamic-execution",
                "script",
                "JavaScript/TypeScript script invokes dynamic code evaluation",
            )
        )
    if _JS_BULK_ENV_RE.search(line) and not _capability_permitted("env:*", allowed):
        findings.append(
            _finding(
                file_label,
                line_no,
                "undeclared-credential-read",
                "script",
                "Undeclared bulk environment read 'process.env' (requires capability 'env:*')",
            )
        )
    findings.extend(_vet_js_ts_line_credentials(line, line_no, file_label, allowed))
    return findings


def _vet_js_ts_source(
    source: str, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    in_block_comment = False
    for idx, raw_line in enumerate(source.splitlines(), start=1):
        line, in_block_comment = _strip_js_block_comments_line(
            raw_line, in_block_comment
        )
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        findings.extend(_vet_js_ts_line(line, idx, file_label, allowed))
    return findings


def _probe_shebang(
    path: Path, *, root: Path | None = None
) -> tuple[bool, list[dict[str, Any]]]:
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return (
            False,
            [
                _finding(
                    file_label,
                    1,
                    "symlink-file",
                    "filesystem",
                    f"Symlinked file is not permitted: {file_label}",
                )
            ],
        )
    if not path.is_file():
        return False, []
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!", []
    except OSError as exc:
        return (
            False,
            [
                _finding(
                    file_label,
                    1,
                    "unreadable-file",
                    "filesystem",
                    f"Unable to read file '{file_label}': {exc}",
                )
            ],
        )


def _has_shebang(path: Path) -> bool:
    has_sb, probe_findings = _probe_shebang(path)
    return has_sb and not probe_findings


def vet_script(
    path: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Run offline AST and regex security checks on a Python, JS/TS, or shell script."""
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked script file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"Script file not found: {path}")

    allowed = _normalize_capabilities(allowed_capabilities)
    findings = _scan_hidden_unicode_bytes_or_text(path, file_label, category="script")
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            *findings,
            _finding(
                file_label,
                1,
                "binary-script",
                "script",
                "Non-UTF-8 or binary executable in scripts directory is not permitted",
            ),
        ]
    except OSError as exc:
        return [
            *findings,
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read script file '{file_label}': {exc}",
            ),
        ]

    suffix = path.suffix.lower()
    first_line = source.splitlines()[0].lower() if source else ""
    is_shebang = first_line.startswith("#!")
    if suffix == ".py" or (is_shebang and "python" in first_line):
        findings.extend(_vet_python_source(source, file_label, allowed))
    elif suffix in _JS_TS_SUFFIXES or (
        is_shebang
        and any(rt in first_line for rt in ("node", "deno", "bun", "ts-node", "tsx"))
    ):
        findings.extend(_vet_js_ts_source(source, file_label, allowed))
    else:
        findings.extend(_vet_shell_source(source, file_label, allowed))
    return findings


def _classify_hidden_unicode(ch: str, is_file_start: bool) -> str | None:
    cp = ord(ch)
    if cp == 0xFEFF and is_file_start:
        return "UTF-8 BOM / zero-width no-break space"
    for low, high, label in _HIDDEN_UNICODE_RANGES:
        if low <= cp <= high:
            return label
    return None


def _scan_hidden_unicode_text(
    text: str, file_label: str, *, category: str
) -> list[dict[str, Any]]:
    if text.isascii():
        return []
    findings: list[dict[str, Any]] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        seen_on_line: set[int] = set()
        for col_idx, ch in enumerate(line):
            label = _classify_hidden_unicode(
                ch, is_file_start=(line_idx == 1 and col_idx == 0)
            )
            if label is None or ord(ch) in seen_on_line:
                continue
            seen_on_line.add(ord(ch))
            findings.append(
                _finding(
                    file_label,
                    line_idx,
                    "hidden-unicode",
                    category,
                    f"Hidden Unicode character U+{ord(ch):04X} ({label})",
                )
            )
    return findings


def _scan_hidden_unicode_bytes_or_text(
    path: Path, file_label: str, *, category: str
) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _scan_hidden_unicode_text(text, file_label, category=category)


def _strip_inline_code(line: str) -> str:
    return _INLINE_CODE_RE.sub(" ", line)


def _is_remote_url(raw_target: str) -> bool:
    cleaned = raw_target.strip().strip("<>\"'")
    if cleaned.startswith("//"):
        return True
    parsed = urlsplit(cleaned)
    return parsed.scheme.lower() in {
        "http",
        "https",
        "ftp",
        "ws",
        "wss",
        "data",
    } and bool(parsed.netloc or parsed.scheme.lower() == "data")


def _is_remote_network_url(raw_target: str) -> bool:
    cleaned = raw_target.strip().strip("<>\"'")
    if cleaned.startswith("//"):
        return True
    parsed = urlsplit(cleaned)
    return parsed.scheme.lower() in {"http", "https", "ftp", "ws", "wss"} and bool(
        parsed.netloc
    )


def _inspect_svg_xml_stylesheets(
    svg_text: str, file_label: str
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for style_pi in _SVG_XML_STYLESHEET_RE.finditer(svg_text):
        href = style_pi.group(1).strip()
        line_no = svg_text.count("\n", 0, style_pi.start()) + 1
        if href.lower().startswith(("javascript:", "vbscript:", "data:")):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "svg-active-content",
                    "asset",
                    f"SVG xml-stylesheet directive uses executable URI '{href}'",
                )
            )
        elif _is_remote_network_url(href):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "svg-remote-url",
                    "asset",
                    f"SVG xml-stylesheet directive references remote URL '{href}'",
                )
            )
    return findings


def _parse_svg_root(
    svg_text: str, file_label: str
) -> tuple[ET.Element | None, list[dict[str, Any]]]:
    try:
        svg_root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        line_no = exc.position[0] if hasattr(exc, "position") else 1
        return None, [
            _with_severity(
                _finding(
                    file_label,
                    line_no,
                    "invalid-svg",
                    "asset",
                    f"Invalid SVG XML: {exc}",
                ),
                "medium",
            )
        ]
    root_tag = svg_root.tag.rsplit("}", 1)[-1]
    if root_tag != "svg":
        return None, [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-svg",
                    "asset",
                    f"Invalid SVG root element: {root_tag}",
                ),
                "medium",
            )
        ]
    return svg_root, []


def _inspect_svg_attributes(
    elem: ET.Element,
    raw_tag: str,
    elem_line: int,
    *,
    svg_text: str,
    file_label: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for raw_attr, raw_val in elem.attrib.items():
        local_attr = raw_attr.rsplit("}", 1)[-1].lower()
        attr_line = _find_line_number(svg_text, raw_val) if raw_val else elem_line
        if local_attr.startswith("on"):
            findings.append(
                _finding(
                    file_label,
                    _find_line_number(svg_text, local_attr) or elem_line,
                    "svg-event-handler",
                    "asset",
                    f"SVG element '<{raw_tag}>' defines inline event handler '{local_attr}'",
                )
            )
            continue
        if local_attr in _SVG_URI_ATTRS:
            cleaned = raw_val.strip()
            if cleaned.lower().startswith(("javascript:", "vbscript:", "data:")):
                findings.append(
                    _finding(
                        file_label,
                        attr_line,
                        "svg-active-content",
                        "asset",
                        f"SVG attribute '{local_attr}' uses executable or embedded URI '{cleaned}'",
                    )
                )
            elif _is_remote_network_url(cleaned):
                findings.append(
                    _finding(
                        file_label,
                        attr_line,
                        "svg-remote-url",
                        "asset",
                        f"SVG attribute '{local_attr}' references remote URL '{cleaned}'",
                    )
                )
        _inspect_svg_css_urls(
            raw_val,
            svg_text=svg_text,
            fallback_line=attr_line,
            file_label=file_label,
            findings=findings,
        )
    return findings


def _inspect_svg_element(
    elem: ET.Element, *, svg_text: str, file_label: str
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    raw_tag = elem.tag.rsplit("}", 1)[-1]
    local_tag = raw_tag.lower()
    elem_line = _find_line_number(svg_text, f"<{raw_tag}")

    if local_tag in _SVG_ACTIVE_TAGS:
        findings.append(
            _finding(
                file_label,
                elem_line,
                "svg-active-content",
                "asset",
                f"SVG contains disallowed active element '<{raw_tag}>'",
            )
        )
    if local_tag == "style":
        _inspect_svg_css_urls(
            "".join(elem.itertext()),
            svg_text=svg_text,
            fallback_line=elem_line,
            file_label=file_label,
            findings=findings,
        )
    if local_tag in {"set", "animate", "animatetransform", "animatemotion"}:
        anim_attr = (
            (elem.attrib.get("attributeName") or elem.attrib.get("attributename") or "")
            .strip()
            .lower()
        )
        if anim_attr.startswith("on"):
            findings.append(
                _finding(
                    file_label,
                    elem_line,
                    "svg-event-handler",
                    "asset",
                    f"SVG animation targets event handler attribute '{anim_attr}'",
                )
            )
    findings.extend(
        _inspect_svg_attributes(
            elem, raw_tag, elem_line, svg_text=svg_text, file_label=file_label
        )
    )
    return findings


def _vet_svg_text(svg_text: str, file_label: str) -> list[dict[str, Any]]:
    if "\x00" in svg_text:
        return [
            _finding(
                file_label,
                1,
                "invalid-svg",
                "asset",
                "Binary NUL byte in SVG file",
            )
        ]

    findings = _scan_hidden_unicode_text(svg_text, file_label, category="asset")

    entity_match = _SVG_ENTITY_DOCTYPE_RE.search(svg_text)
    if entity_match is not None:
        line_no = svg_text.count("\n", 0, entity_match.start()) + 1
        findings.append(
            _finding(
                file_label,
                line_no,
                "svg-active-content",
                "asset",
                "SVG contains disallowed XML entity or external DTD declaration",
            )
        )
        return findings

    findings.extend(_inspect_svg_xml_stylesheets(svg_text, file_label))
    svg_root, root_errors = _parse_svg_root(svg_text, file_label)
    if svg_root is None:
        findings.extend(root_errors)
        return findings

    for elem in svg_root.iter():
        findings.extend(
            _inspect_svg_element(elem, svg_text=svg_text, file_label=file_label)
        )
    return findings


def _inspect_svg_css_urls(
    css_fragment: str,
    *,
    svg_text: str,
    fallback_line: int,
    file_label: str,
    findings: list[dict[str, Any]],
) -> None:
    for match in _SVG_CSS_URL_RE.finditer(css_fragment):
        target = (match.group(1) or match.group(2) or "").strip()
        if not target or target.startswith("#"):
            continue
        line_no = _find_line_number(svg_text, target) or fallback_line
        if target.lower().startswith(("javascript:", "vbscript:", "data:")):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "svg-active-content",
                    "asset",
                    f"SVG style reference uses executable or embedded URI '{target}'",
                )
            )
        elif _is_remote_network_url(target):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "svg-remote-url",
                    "asset",
                    f"SVG style reference loads remote URL '{target}'",
                )
            )


def _vet_svg_file(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked SVG file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"SVG file not found: {path}")
    try:
        svg_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-svg",
                    "asset",
                    "SVG file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return [
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read SVG file '{file_label}': {exc}",
            )
        ]
    return _vet_svg_text(svg_text, file_label)


def _collect_markdown_ref_defs(non_fenced: list[tuple[int, str]]) -> dict[str, str]:
    ref_defs: dict[str, str] = {}
    for _, raw_line in non_fenced:
        ref_def = parse_reference_link_definition(raw_line)
        if ref_def is not None:
            ref_defs[ref_def[0]] = ref_def[1]
    return ref_defs


def _consume_markdown_html_comment(
    masked: str, line_no: int, file_label: str, in_html_comment: bool
) -> tuple[str | None, bool, list[dict[str, Any]]]:
    if in_html_comment:
        if "-->" not in masked:
            return None, True, []
        in_html_comment = False
        masked = masked.split("-->", 1)[1]
    if "<!--" not in masked:
        return masked, False, []
    finding = _finding(
        file_label,
        line_no,
        "html-comment",
        "prompt",
        "HTML comment outside fenced code block can hide prompt instructions",
    )
    after_open = masked.split("<!--", 1)[1]
    return masked, ("-->" not in after_open), [finding]


def _inspect_markdown_ref_images(
    masked: str, line_no: int, file_label: str, ref_defs: Mapping[str, str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for ref_use in _MD_REF_IMAGE_USE_RE.finditer(masked):
        if masked[ref_use.end() : ref_use.end() + 1] == "(":
            continue
        ref_key = (ref_use.group(2) or ref_use.group(1) or "").strip().lower()
        resolved_url = ref_defs.get(ref_key, "")
        if resolved_url.lower().startswith(("http://", "https://", "//")):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "remote-image",
                    "prompt",
                    f"Remote reference Markdown image '{resolved_url}' can exfiltrate context",
                )
            )
    return findings


def _inspect_markdown_images(
    masked: str, line_no: int, file_label: str, ref_defs: Mapping[str, str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    remote_found = False
    for img_match in _MD_INLINE_IMAGE_RE.finditer(masked):
        target = img_match.group(1)
        if _is_remote_url(target) and target.lower().startswith(
            ("http://", "https://", "//")
        ):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "remote-image",
                    "prompt",
                    f"Remote Markdown image '{target}' can exfiltrate context",
                )
            )
            remote_found = True

    if not remote_found:
        findings.extend(
            _inspect_markdown_ref_images(masked, line_no, file_label, ref_defs)
        )

    for html_img in _HTML_IMG_RE.finditer(masked):
        src = (html_img.group(1) or html_img.group(2) or "").strip()
        if src.lower().startswith(("http://", "https://", "//")):
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "remote-image",
                    "prompt",
                    f"Remote HTML image '{src}' can exfiltrate context",
                )
            )
    return findings


def _vet_markdown_text(text: str, file_label: str) -> list[dict[str, Any]]:
    findings = _scan_hidden_unicode_text(text, file_label, category="prompt")
    non_fenced = non_fenced_lines(text, max_indent=3)
    ref_defs = _collect_markdown_ref_defs(non_fenced)

    in_html_comment = False
    for line_no, raw_line in non_fenced:
        masked, in_html_comment, comment_findings = _consume_markdown_html_comment(
            _strip_inline_code(raw_line), line_no, file_label, in_html_comment
        )
        findings.extend(comment_findings)
        if masked is None:
            continue
        for pattern, description in _PROMPT_OVERRIDE_PATTERNS:
            if pattern.search(masked):
                findings.append(
                    _finding(
                        file_label,
                        line_no,
                        "prompt-override",
                        "prompt",
                        description,
                    )
                )
                break
        findings.extend(_inspect_markdown_images(masked, line_no, file_label, ref_defs))

    return findings


def vet_markdown(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Scan a Markdown or agent instruction file for hidden payloads and prompt risks."""
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked instruction file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"Markdown file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-encoding",
                    "prompt",
                    "Instruction file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return [
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read instruction file '{file_label}': {exc}",
            )
        ]
    return _vet_markdown_text(text, file_label)


def _strip_shim_html_comment_line(
    masked: str, in_html_comment: bool
) -> tuple[str | None, bool]:
    if in_html_comment:
        if "-->" not in masked:
            return None, True
        masked = masked.split("-->", 1)[1]
        in_html_comment = False
    if "<!--" in masked:
        before, after = masked.split("<!--", 1)
        if "-->" in after:
            return before + " " + after.split("-->", 1)[1], False
        return before, True
    return masked, in_html_comment


def _extract_markdown_shim_directives(
    text: str, base_dir: Path
) -> list[tuple[int, str]]:
    directives: list[tuple[int, str]] = []
    in_html_comment = False
    for line_no, raw_line in non_fenced_lines(text, max_indent=3):
        masked, in_html_comment = _strip_shim_html_comment_line(
            _strip_inline_code(raw_line), in_html_comment
        )
        if masked is None:
            continue
        trimmed = masked.strip()
        is_pure_shim_line = trimmed.startswith("@") and " " not in trimmed
        for match in _MD_SHIM_TOKEN_RE.finditer(masked):
            raw_token = match.group(1).rstrip(".,;:")
            if not raw_token:
                continue
            norm = raw_token.replace("\\", "/")
            if is_pure_shim_line or _looks_like_shim_path(raw_token, norm, base_dir):
                directives.append((line_no, raw_token))
    return directives


def _looks_like_shim_path(raw_token: str, norm: str, base_dir: Path) -> bool:
    if (
        norm.startswith(("/", "~/", "../", "./", "."))
        or "/.." in norm
        or PureWindowsPath(raw_token).is_absolute()
        or _is_remote_network_url(raw_token)
    ):
        return True
    suffix = Path(norm).suffix.lower()
    if suffix in _SHIM_DOC_SUFFIXES or suffix in _SCRIPT_SUFFIXES or suffix == ".svg":
        return True
    candidate = base_dir / norm
    return candidate.exists() or candidate.is_symlink()


def _has_symlink_between(candidate: Path, boundary_root: Path) -> bool:
    if candidate.is_symlink():
        return True
    resolved_boundary = boundary_root.resolve()
    curr = candidate.parent
    visited: set[Path] = set()
    while curr not in visited:
        visited.add(curr)
        if curr.is_symlink():
            return True
        try:
            if curr.resolve() == resolved_boundary:
                break
        except OSError:
            return True
        if curr.parent == curr:
            break
        curr = curr.parent
    return False


def _check_resolved_within_boundary(
    candidate: Path,
    cleaned: str,
    *,
    line: int,
    boundary: _BoundaryContext,
) -> list[dict[str, Any]]:
    resolved_boundary = boundary.boundary_root.resolve()
    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        return [
            _finding(
                boundary.source_label,
                line,
                "escaping-path",
                "filesystem",
                f"Referenced path '{cleaned}' could not be resolved safely",
            )
        ]
    if not resolved_candidate.is_relative_to(resolved_boundary):
        return [
            _finding(
                boundary.source_label,
                line,
                "escaping-path",
                "filesystem",
                f"Reference '{cleaned}' escapes repository/package boundary",
            )
        ]
    return []


def _resolve_bounded_local_path(
    raw_ref: str,
    *,
    line: int,
    boundary: _BoundaryContext,
) -> tuple[Path | None, list[dict[str, Any]]]:
    cleaned = raw_ref.strip()
    if not cleaned:
        return None, []
    if _is_remote_network_url(cleaned):
        return None, [
            _finding(
                boundary.source_label,
                line,
                "remote-url",
                boundary.category,
                f"Remote reference '{cleaned}' is not permitted in local configuration",
            )
        ]
    norm = cleaned.replace("\\", "/")
    if (
        norm.startswith(("/", "~/"))
        or norm == "~"
        or Path(cleaned).is_absolute()
        or PureWindowsPath(cleaned).is_absolute()
    ):
        return None, [
            _finding(
                boundary.source_label,
                line,
                "escaping-path",
                "filesystem",
                f"Reference '{cleaned}' escapes repository/package boundary",
            )
        ]

    candidate = boundary.base_dir / norm
    if _has_symlink_between(candidate, boundary.boundary_root):
        return None, [
            _finding(
                boundary.source_label,
                line,
                "symlink-file",
                "filesystem",
                f"Referenced path '{cleaned}' traverses or targets a symlink",
            )
        ]
    bound_errors = _check_resolved_within_boundary(
        candidate, cleaned, line=line, boundary=boundary
    )
    if bound_errors:
        return None, bound_errors
    return candidate, []


def _record_shim_target_findings(
    target_resolved: Path,
    target_label: str,
    findings: list[dict[str, Any]],
    next_ctx: _RepoScanContext,
) -> int:
    next_ctx.vetted_md_targets.add(target_resolved)
    added = 1 if _mark_config_scanned(next_ctx, target_label) else 0
    next_ctx.findings.extend(findings)
    return added


def _follow_markdown_shim_target(target_path: Path, next_ctx: _RepoScanContext) -> int:
    target_resolved = target_path.resolve()
    if (
        target_resolved in next_ctx.active_chain
        or target_resolved in next_ctx.vetted_md_targets
    ):
        return 0
    suffix = target_path.suffix.lower()
    target_label = _format_file_label(target_path, next_ctx.repo_root)
    if suffix in _SCRIPT_SUFFIXES:
        return _record_shim_target_findings(
            target_resolved,
            target_label,
            vet_script(
                target_path, next_ctx.allowed_capabilities, root=next_ctx.repo_root
            ),
            next_ctx,
        )
    if suffix == ".svg":
        return _record_shim_target_findings(
            target_resolved,
            target_label,
            _vet_svg_file(target_path, root=next_ctx.repo_root),
            next_ctx,
        )
    is_shebang, probe_findings = _probe_shebang(target_path, root=next_ctx.repo_root)
    if probe_findings:
        return _record_shim_target_findings(
            target_resolved, target_label, probe_findings, next_ctx
        )
    if is_shebang:
        return _record_shim_target_findings(
            target_resolved,
            target_label,
            vet_script(
                target_path, next_ctx.allowed_capabilities, root=next_ctx.repo_root
            ),
            next_ctx,
        )
    return _vet_markdown_with_shims(target_path, next_ctx)


def _read_instruction_text(
    inst_path: Path, rel_label: str
) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        return inst_path.read_text(encoding="utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _with_severity(
                _finding(
                    rel_label,
                    1,
                    "invalid-encoding",
                    "prompt",
                    "Instruction file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return None, [
            _finding(
                rel_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read instruction file '{rel_label}': {exc}",
            )
        ]


def _vet_markdown_with_shims(inst_path: Path, ctx: _RepoScanContext) -> int:
    rel_label = _format_file_label(inst_path, ctx.repo_root)
    if inst_path.is_symlink() or _has_symlink_between(inst_path, ctx.repo_root):
        _mark_config_scanned(ctx, rel_label)
        ctx.findings.append(
            _finding(
                rel_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked instruction file is not permitted",
            )
        )
        return 1

    resolved = inst_path.resolve()
    if not resolved.is_relative_to(ctx.repo_root):
        ctx.findings.append(
            _finding(
                rel_label,
                1,
                "escaping-path",
                "filesystem",
                f"Instruction file escapes repository boundary: {rel_label}",
            )
        )
        return 0

    if (
        resolved in ctx.active_chain
        or resolved in ctx.vetted_md_targets
        or not inst_path.is_file()
    ):
        return 0

    ctx.vetted_md_targets.add(resolved)
    scanned_count = 1 if _mark_config_scanned(ctx, rel_label) else 0
    text, read_findings = _read_instruction_text(inst_path, rel_label)
    if text is None:
        ctx.findings.extend(read_findings)
        return scanned_count

    ctx.findings.extend(_vet_markdown_text(text, rel_label))
    next_ctx = replace(ctx, active_chain=ctx.active_chain | {resolved})
    boundary = _BoundaryContext(
        base_dir=inst_path.parent,
        boundary_root=ctx.repo_root,
        source_label=rel_label,
        category="prompt",
    )
    for line_no, raw_token in _extract_markdown_shim_directives(text, inst_path.parent):
        target_path, bound_findings = _resolve_bounded_local_path(
            raw_token, line=line_no, boundary=boundary
        )
        if bound_findings:
            ctx.findings.extend(bound_findings)
            continue
        if target_path is not None and target_path.is_file():
            scanned_count += _follow_markdown_shim_target(target_path, next_ctx)
    return scanned_count


def _find_line_number(raw_text: str, needle: str) -> int:
    if not needle:
        return 1
    idx = raw_text.find(needle)
    if idx < 0:
        return 1
    return raw_text.count("\n", 0, idx) + 1


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    return hostname.lower() in {"localhost", "127.0.0.1", "::1"}


def _inspect_endpoint_url(
    url_val: str,
    *,
    owner_label: str,
    source: _ConfigSourceContext,
    insecure_rule: str = "mcp-insecure-transport",
    remote_rule: str = "mcp-remote-endpoint",
) -> list[dict[str, Any]]:
    cleaned = url_val.strip()
    if not cleaned:
        return []
    url_line = _find_line_number(source.raw_text, url_val) or source.fallback_line
    parsed = urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme == "http" and not _is_loopback_host(parsed.hostname):
        return [
            _finding(
                source.file_label,
                url_line,
                insecure_rule,
                "agent-config",
                f"{owner_label} uses unencrypted HTTP transport '{cleaned}'",
            )
        ]
    if scheme in {"https", "ws", "wss"} and not _is_loopback_host(parsed.hostname):
        return [
            _with_severity(
                _finding(
                    source.file_label,
                    url_line,
                    remote_rule,
                    "agent-config",
                    f"{owner_label} connects to remote endpoint '{cleaned}'",
                ),
                "medium",
            )
        ]
    return []


def _inspect_secret_mapping(
    mapping: Mapping[str, Any],
    *,
    owner_label: str,
    map_field: str,
    source: _ConfigSourceContext,
    rule: str = "mcp-inline-secret",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key, val in mapping.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        stripped_val = val.strip()
        if not stripped_val or _ENV_PLACEHOLDER_RE.match(stripped_val):
            continue
        if _MCP_INLINE_SECRET_VALUE_RE.search(stripped_val) or (
            _MCP_SECRET_KEY_NAME_RE.search(key) and len(stripped_val) >= 8
        ):
            val_line = _find_line_number(source.raw_text, key) or source.fallback_line
            findings.append(
                _finding(
                    source.file_label,
                    val_line,
                    rule,
                    "agent-config",
                    f"{owner_label} stores a literal credential in {map_field}.{key}",
                )
            )
    return findings


def _inspect_prompt_string(
    field_val: str,
    *,
    owner_label: str,
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    cleaned = field_val.strip()
    if not cleaned:
        return findings
    line_no = _find_line_number(source.raw_text, cleaned[:40]) or source.fallback_line
    if "<!--" in cleaned:
        findings.append(
            _finding(
                source.file_label,
                line_no,
                "html-comment",
                "agent-config",
                f"{owner_label} contains HTML comment that can conceal prompt instructions",
            )
        )
    for pattern, desc in _PROMPT_OVERRIDE_PATTERNS:
        if pattern.search(cleaned):
            findings.append(
                _finding(
                    source.file_label,
                    line_no,
                    "prompt-override",
                    "agent-config",
                    f"{owner_label}: {desc}",
                )
            )
            break
    return findings


def _inspect_mcp_command_and_args(
    command: str,
    args: list[str],
    *,
    owner_label: str,
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    cleaned_cmd = command.strip()
    if not cleaned_cmd:
        return findings
    cmd_line = _find_line_number(source.raw_text, command) or source.fallback_line
    cmd_base = Path(cleaned_cmd.replace("\\", "/")).name.lower()
    joined = " ".join([cleaned_cmd, *args])
    if (
        cmd_base in _SHELL_BINARY_NAMES
        or _SHELL_DYNAMIC_EXEC_RE.search(joined)
        or _SHELL_NETWORK_RE.search(joined)
    ):
        findings.append(
            _finding(
                source.file_label,
                cmd_line,
                "mcp-shell-execution",
                "agent-config",
                f"{owner_label} executes shell or network command '{cleaned_cmd}'",
            )
        )
    if cmd_base in {"npx", "npx.cmd"} and any(a in {"-y", "--yes"} for a in args):
        pkg_args = [a for a in args if not a.startswith("-")]
        if not pkg_args or any(
            not re.search(r"@\d", pkg.lstrip("@")) for pkg in pkg_args[:1]
        ):
            findings.append(
                _with_severity(
                    _finding(
                        source.file_label,
                        cmd_line,
                        "mcp-unpinned-package",
                        "agent-config",
                        f"{owner_label} runs 'npx -y' without an explicit pinned package version",
                    ),
                    "medium",
                )
            )
    return findings


def _inspect_mcp_server_entry(
    server_name: str,
    cfg: Mapping[str, Any],
    *,
    raw_text: str,
    file_label: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    server_line = _find_line_number(raw_text, f'"{server_name}"')
    source = _ConfigSourceContext(
        raw_text=raw_text, file_label=file_label, fallback_line=server_line
    )
    owner = f"MCP server '{server_name}'"

    for url_key in ("url", "serverUrl", "httpUrl", "sseUrl"):
        url_val = cfg.get(url_key)
        if isinstance(url_val, str) and url_val.strip():
            findings.extend(
                _inspect_endpoint_url(url_val, owner_label=owner, source=source)
            )
            break

    command = cfg.get("command")
    raw_args = cfg.get("args")
    args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
    if isinstance(command, str) and command.strip():
        findings.extend(
            _inspect_mcp_command_and_args(
                command, args, owner_label=owner, source=source
            )
        )

    for map_field in ("env", "headers"):
        mapping = cfg.get(map_field)
        if isinstance(mapping, Mapping):
            findings.extend(
                _inspect_secret_mapping(
                    mapping, owner_label=owner, map_field=map_field, source=source
                )
            )

    for text_field in ("description", "instructions", "prompt"):
        field_val = cfg.get(text_field)
        if isinstance(field_val, str) and field_val.strip():
            findings.extend(
                _inspect_prompt_string(
                    field_val, owner_label=f"{owner} {text_field}", source=source
                )
            )

    return findings


def _read_mcp_config_text(
    path: Path, file_label: str
) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-encoding",
                    "agent-config",
                    "MCP configuration file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return None, [
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read MCP configuration file '{file_label}': {exc}",
            )
        ]


def vet_mcp_config(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Inspect an MCP configuration JSON file for unsafe commands, transports, or secrets."""
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked MCP configuration file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"MCP configuration file not found: {path}")

    raw_text, read_findings = _read_mcp_config_text(path, file_label)
    if raw_text is None:
        return read_findings

    findings = _scan_hidden_unicode_text(raw_text, file_label, category="agent-config")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        findings.append(
            _with_severity(
                _finding(
                    file_label,
                    exc.lineno,
                    "invalid-json",
                    "agent-config",
                    f"Malformed MCP JSON configuration: {exc.msg}",
                ),
                "medium",
            )
        )
        return findings

    if not isinstance(data, Mapping):
        findings.append(
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-mcp-config",
                    "agent-config",
                    "MCP configuration root must be a JSON object",
                ),
                "medium",
            )
        )
        return findings

    servers = data.get("mcpServers")
    if not isinstance(servers, Mapping):
        servers = data.get("servers")
    if isinstance(servers, Mapping):
        for server_name, server_cfg in servers.items():
            if isinstance(server_name, str) and isinstance(server_cfg, Mapping):
                findings.extend(
                    _inspect_mcp_server_entry(
                        server_name,
                        server_cfg,
                        raw_text=raw_text,
                        file_label=file_label,
                    )
                )
    return findings


def _strip_yaml_comment_and_quotes(raw_val: str) -> str:
    val = raw_val.strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"') and len(val) >= 2) or (
        val.startswith("'") and val.endswith("'") and len(val) >= 2
    ):
        return val[1:-1].strip()
    in_single = False
    in_double = False
    for idx, ch in enumerate(val):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif (
            ch == "#"
            and not in_single
            and not in_double
            and (idx == 0 or val[idx - 1].isspace())
        ):
            return val[:idx].strip()
    return val.strip()


def _yaml_line_indent(line: str) -> int:
    return len(line.expandtabs(2)) - len(line.lstrip(" \t").expandtabs(2))


def _consume_yaml_block_scalar(
    lines: list[str], start_idx: int, indent: int, indicator: str
) -> tuple[str, int]:
    block_parts: list[str] = []
    idx = start_idx
    total = len(lines)
    while idx < total:
        cont = lines[idx]
        cont_strip = cont.strip()
        if not cont_strip:
            block_parts.append("")
            idx += 1
            continue
        if _yaml_line_indent(cont) <= indent:
            break
        block_parts.append(cont_strip)
        idx += 1
    joiner = "\n" if indicator.startswith("|") else " "
    return joiner.join(block_parts).strip(), idx


def _consume_yaml_list_items(
    lines: list[str], start_idx: int, indent: int
) -> list[str]:
    items: list[str] = []
    scan = start_idx
    total = len(lines)
    while scan < total:
        sub = lines[scan]
        sub_strip = sub.strip()
        if not sub_strip or sub_strip.startswith("#"):
            scan += 1
            continue
        if _yaml_line_indent(sub) < indent:
            break
        list_match = _YAML_LIST_ITEM_RE.match(sub)
        if (
            list_match
            and len(list_match.group(1).expandtabs(2)) >= indent
            and not _YAML_KEY_VALUE_RE.match(sub)
        ):
            items.append(_strip_yaml_comment_and_quotes(list_match.group(2)))
            scan += 1
            continue
        break
    return items


def _consume_yaml_folded_scalar(
    lines: list[str], start_idx: int, indent: int, first_part: str
) -> tuple[str, int]:
    scalar_parts = [first_part]
    idx = start_idx
    total = len(lines)
    while idx < total:
        cont = lines[idx]
        cont_strip = cont.strip()
        if (
            not cont_strip
            or cont_strip.startswith("#")
            or _yaml_line_indent(cont) <= indent
            or _YAML_KEY_VALUE_RE.match(cont)
            or _YAML_LIST_ITEM_RE.match(cont)
        ):
            break
        scalar_parts.append(_strip_yaml_comment_and_quotes(cont_strip))
        idx += 1
    return " ".join(scalar_parts).strip(), idx


def _parse_yaml_entries(
    raw_text: str,
) -> tuple[list[tuple[int, str, str]], dict[int, list[str]]]:
    """Extract (line_no, key, joined_value) pairs and list values from YAML text using stdlib."""
    kv_entries: list[tuple[int, str, str]] = []
    list_values: dict[int, list[str]] = {}
    lines = raw_text.splitlines()
    idx = 0
    total = len(lines)
    while idx < total:
        raw_line = lines[idx]
        stripped = raw_line.strip()
        kv_match = (
            None
            if (not stripped or stripped.startswith("#") or stripped in {"---", "..."})
            else _YAML_KEY_VALUE_RE.match(raw_line)
        )
        if not kv_match:
            idx += 1
            continue

        indent = len(kv_match.group(1).expandtabs(2)) + len(kv_match.group(2) or "")
        key = kv_match.group(3)
        rest = _strip_yaml_comment_and_quotes(kv_match.group(4))
        line_no = idx + 1

        if rest in {"|", "|-", "|+", ">", ">-", ">+"}:
            joined, idx = _consume_yaml_block_scalar(lines, idx + 1, indent, rest)
            kv_entries.append((line_no, key, joined))
        elif not rest:
            items = _consume_yaml_list_items(lines, idx + 1, indent)
            if items:
                list_values[line_no] = items
            kv_entries.append((line_no, key, ""))
            idx += 1
        else:
            joined, idx = _consume_yaml_folded_scalar(lines, idx + 1, indent, rest)
            kv_entries.append((line_no, key, joined))

    return kv_entries, list_values


def _inspect_yaml_field_value(
    key: str,
    val: str,
    *,
    source: _ConfigSourceContext,
    boundary_root: Path | None,
) -> list[dict[str, Any]]:
    key_lower = key.lower()
    line_no = source.fallback_line
    findings = _inspect_prompt_string(
        val, owner_label=f"YAML field '{key}'", source=source
    )
    if not _ENV_PLACEHOLDER_RE.match(val) and (
        _MCP_INLINE_SECRET_VALUE_RE.search(val)
        or (_MCP_SECRET_KEY_NAME_RE.search(key) and len(val) >= 8)
    ):
        findings.append(
            _finding(
                source.file_label,
                line_no,
                "mcp-inline-secret",
                "agent-config",
                f"YAML configuration stores a literal credential in '{key}'",
            )
        )
    if key_lower in {
        "url",
        "serverurl",
        "server_url",
        "httpurl",
        "sseurl",
        "endpoint",
    }:
        findings.extend(
            _inspect_endpoint_url(
                val, owner_label=f"YAML endpoint '{key}'", source=source
            )
        )
    if key_lower in {"icon_small", "icon_large", "icon", "logo"}:
        if _is_remote_url(val):
            findings.append(
                _finding(
                    source.file_label,
                    line_no,
                    "remote-image",
                    "agent-config",
                    f"YAML UI field '{key}' references remote or embedded URI '{val}'",
                )
            )
        elif boundary_root is not None:
            boundary = _BoundaryContext(
                base_dir=boundary_root,
                boundary_root=boundary_root,
                source_label=source.file_label,
            )
            _, path_errs = _resolve_bounded_local_path(
                val, line=line_no, boundary=boundary
            )
            findings.extend(path_errs)
    return findings


def _parse_yaml_command_spec(
    key_lower: str, val: str, line_no: int
) -> tuple[tuple[int, str], list[str]] | None:
    if key_lower not in {"command", "bash", "shell", "script"}:
        return None
    if val.startswith("[") and val.endswith("]"):
        parts = [
            _strip_yaml_comment_and_quotes(p) for p in val[1:-1].split(",") if p.strip()
        ]
        return ((line_no, parts[0]), parts[1:]) if parts else None
    return (line_no, val), []


def _vet_agent_yaml_text(
    raw_text: str,
    file_label: str,
    *,
    boundary_root: Path | None = None,
    allowed: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    findings = _scan_hidden_unicode_text(raw_text, file_label, category="agent-config")
    kv_entries, list_values = _parse_yaml_entries(raw_text)

    pending_cmd: tuple[int, str] | None = None
    pending_args: list[str] = []

    for line_no, key, val in kv_entries:
        key_lower = key.lower()
        if key == "args" and line_no in list_values:
            pending_args = list_values[line_no]
            continue
        if pending_cmd is not None and key_lower != "args":
            cmd_line, cmd_val = pending_cmd
            cmd_source = _ConfigSourceContext(
                raw_text=raw_text, file_label=file_label, fallback_line=cmd_line
            )
            findings.extend(
                _inspect_yaml_command(
                    cmd_val, pending_args, source=cmd_source, allowed=allowed
                )
            )
            pending_cmd = None
            pending_args = []
        if not val:
            continue
        field_source = _ConfigSourceContext(
            raw_text=raw_text, file_label=file_label, fallback_line=line_no
        )
        findings.extend(
            _inspect_yaml_field_value(
                key, val, source=field_source, boundary_root=boundary_root
            )
        )
        cmd_spec = _parse_yaml_command_spec(key_lower, val, line_no)
        if cmd_spec is not None:
            pending_cmd, pending_args = cmd_spec

    if pending_cmd is not None:
        cmd_line, cmd_val = pending_cmd
        cmd_source = _ConfigSourceContext(
            raw_text=raw_text, file_label=file_label, fallback_line=cmd_line
        )
        findings.extend(
            _inspect_yaml_command(
                cmd_val, pending_args, source=cmd_source, allowed=allowed
            )
        )
    return findings


def _vet_agent_yaml(
    path: Path,
    *,
    boundary_root: Path | None = None,
    root: Path | None = None,
    allowed: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked YAML configuration file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"YAML configuration file not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-encoding",
                    "agent-config",
                    "YAML configuration file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return [
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read YAML configuration file '{file_label}': {exc}",
            )
        ]
    return _vet_agent_yaml_text(
        raw_text,
        file_label,
        boundary_root=boundary_root,
        allowed=allowed,
    )


def _inspect_yaml_command(
    cmd_val: str,
    args: list[str],
    *,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
) -> list[dict[str, Any]]:
    line_no = source.fallback_line
    findings = _inspect_mcp_command_and_args(
        cmd_val,
        args,
        owner_label="YAML command entry",
        source=source,
    )
    joined = " ".join([cmd_val, *args])
    for sub in _vet_shell_source(joined, source.file_label, allowed):
        findings.append(
            _with_severity(
                _finding(
                    source.file_label,
                    line_no,
                    sub["rule"],
                    "agent-config",
                    sub["message"],
                ),
                sub["severity"],
            )
        )
    if not findings and not _process_capability_permitted(allowed):
        findings.append(
            _finding(
                source.file_label,
                line_no,
                "shell-execution",
                "agent-config",
                f"YAML configuration defines executable command '{cmd_val}'",
            )
        )
    return findings


class _ManifestRefs:
    def __init__(self) -> None:
        self.mcp_paths: list[tuple[int, str]] = []
        self.hook_paths: list[tuple[int, str]] = []
        self.skill_paths: list[tuple[int, str]] = []
        self.doc_paths: list[tuple[int, str]] = []
        self.script_paths: list[tuple[int, str]] = []


def _extract_local_script_from_hook_cmd(command: str) -> str | None:
    cleaned = command.strip()
    if not cleaned:
        return None
    for prefix in (
        "${CLAUDE_PROJECT_DIR}/",
        "$CLAUDE_PROJECT_DIR/",
        "${CLAUDE_PLUGIN_ROOT}/",
        "$CLAUDE_PLUGIN_ROOT/",
        "${WORKSPACE_FOLDER_PATHS}/",
    ):
        if cleaned.startswith(prefix):
            cleaned = "./" + cleaned[len(prefix) :]
            break
    tokens = cleaned.split()
    if not tokens:
        return None
    candidates = [tokens[0]]
    if (
        len(tokens) >= 2
        and Path(tokens[0]).name.lower()
        in {
            "python",
            "python3",
            "node",
            "deno",
            "bun",
            "sh",
            "bash",
            "zsh",
        }
        and not tokens[1].startswith("-")
    ):
        candidates.append(tokens[1])
    for tok in candidates:
        stripped = tok.strip("\"'")
        norm = stripped.replace("\\", "/")
        if norm.startswith(("./", "../", "hooks/", "scripts/", ".claude/", ".cursor/")):
            return stripped
    return None


def _inspect_config_shell_command(
    cmd_val: str,
    *,
    exec_message: str,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    cmd_line = _find_line_number(source.raw_text, cmd_val) or source.fallback_line
    if not _capability_permitted("shell-execution", allowed):
        findings.append(
            _finding(
                source.file_label,
                cmd_line,
                "shell-execution",
                "agent-config",
                exec_message,
            )
        )
    for sub in _vet_shell_source(cmd_val, source.file_label, allowed):
        if sub["rule"] != "shell-execution":
            findings.append(
                _with_severity(
                    _finding(
                        source.file_label,
                        cmd_line,
                        sub["rule"],
                        "agent-config",
                        sub["message"],
                    ),
                    sub["severity"],
                )
            )
    local_script = _extract_local_script_from_hook_cmd(cmd_val)
    if local_script is not None:
        refs.script_paths.append((cmd_line, local_script))
    return findings


def _iter_event_hook_dicts(event_val: Any) -> list[Mapping[str, Any]]:
    if isinstance(event_val, Mapping):
        return [event_val]
    if not isinstance(event_val, list):
        return []
    hooks: list[Mapping[str, Any]] = []
    for entry in event_val:
        if not isinstance(entry, Mapping):
            continue
        nested = entry.get("hooks")
        if isinstance(nested, list):
            hooks.extend(sub for sub in nested if isinstance(sub, Mapping))
        else:
            hooks.append(entry)
    return hooks


def _inspect_hook_list_entries(
    hooks_list: list[Any],
    *,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in hooks_list:
        if isinstance(item, str):
            refs.hook_paths.append((_find_line_number(source.raw_text, item), item))
        elif isinstance(item, Mapping):
            findings.extend(
                _inspect_single_hook_dict(
                    item,
                    event_label="hook",
                    source=source,
                    allowed=allowed,
                    refs=refs,
                )
            )
    return findings


def _inspect_hook_entries(
    hooks_obj: Any,
    *,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    if isinstance(hooks_obj, str):
        refs.hook_paths.append(
            (_find_line_number(source.raw_text, hooks_obj), hooks_obj)
        )
        return []
    if isinstance(hooks_obj, list):
        return _inspect_hook_list_entries(
            hooks_obj, source=source, allowed=allowed, refs=refs
        )
    if not isinstance(hooks_obj, Mapping):
        return []

    findings: list[dict[str, Any]] = []
    for event_name, event_val in hooks_obj.items():
        if not isinstance(event_name, str):
            continue
        for hook_dict in _iter_event_hook_dicts(event_val):
            findings.extend(
                _inspect_single_hook_dict(
                    hook_dict,
                    event_label=f"hook '{event_name}'",
                    source=source,
                    allowed=allowed,
                    refs=refs,
                )
            )
    return findings


def _inspect_hook_urls_and_secrets(
    hook: Mapping[str, Any],
    *,
    owner_label: str,
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for url_key in ("url", "endpoint", "httpUrl"):
        url_val = hook.get(url_key)
        if isinstance(url_val, str) and url_val.strip():
            findings.extend(
                _inspect_endpoint_url(url_val, owner_label=owner_label, source=source)
            )
    for map_field in ("env", "headers"):
        mapping = hook.get(map_field)
        if isinstance(mapping, Mapping):
            findings.extend(
                _inspect_secret_mapping(
                    mapping,
                    owner_label=owner_label,
                    map_field=map_field,
                    source=source,
                )
            )
    return findings


def _inspect_hook_prompts(
    hook: Mapping[str, Any],
    *,
    owner_label: str,
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for text_field in ("prompt", "instructions", "systemMessage", "description"):
        text_val = hook.get(text_field)
        if isinstance(text_val, str) and text_val.strip():
            findings.extend(
                _inspect_prompt_string(
                    text_val,
                    owner_label=f"{owner_label} {text_field}",
                    source=source,
                )
            )
    return findings


def _inspect_single_hook_dict(
    hook: Mapping[str, Any],
    *,
    event_label: str,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    hook_source = replace(
        source, fallback_line=_find_line_number(source.raw_text, '"hooks"')
    )
    owner_label = f"Agent {event_label}"

    for cmd_key in ("command", "bash", "shell", "script"):
        cmd_val = hook.get(cmd_key)
        if isinstance(cmd_val, str) and cmd_val.strip():
            findings.extend(
                _inspect_config_shell_command(
                    cmd_val,
                    exec_message=f"{owner_label} executes shell command '{cmd_val.strip()}'",
                    source=hook_source,
                    allowed=allowed,
                    refs=refs,
                )
            )

    findings.extend(
        _inspect_hook_urls_and_secrets(
            hook, owner_label=owner_label, source=hook_source
        )
    )
    findings.extend(
        _inspect_hook_prompts(hook, owner_label=owner_label, source=hook_source)
    )
    return findings


def _collect_string_or_list_refs(
    raw_val: Any, raw_text: str, out: list[tuple[int, str]]
) -> None:
    if isinstance(raw_val, str) and raw_val.strip():
        out.append((_find_line_number(raw_text, raw_val), raw_val.strip()))
    elif isinstance(raw_val, list):
        for item in raw_val:
            if isinstance(item, str) and item.strip():
                out.append((_find_line_number(raw_text, item), item.strip()))


def _inspect_manifest_base_urls(
    env_map: Mapping[str, Any],
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for base_key in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "GEMINI_API_BASE"):
        base_val = env_map.get(base_key)
        if isinstance(base_val, str) and base_val.strip():
            base_source = replace(
                source, fallback_line=_find_line_number(source.raw_text, base_key)
            )
            findings.extend(
                _inspect_endpoint_url(
                    base_val,
                    owner_label=f"Manifest env.{base_key}",
                    source=base_source,
                )
            )
    return findings


def _inspect_manifest_env_and_headers(
    data: Mapping[str, Any],
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for map_field in ("env", "headers"):
        mapping = data.get(map_field)
        if not isinstance(mapping, Mapping):
            continue
        findings.extend(
            _inspect_secret_mapping(
                mapping,
                owner_label="Manifest configuration",
                map_field=map_field,
                source=source,
            )
        )
        if map_field == "env":
            findings.extend(_inspect_manifest_base_urls(mapping, source))
    return findings


def _inspect_manifest_string_field(
    key: str,
    val: str,
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stripped_val = val.strip()
    key_line = _find_line_number(source.raw_text, key)
    if not _ENV_PLACEHOLDER_RE.match(stripped_val) and (
        _MCP_INLINE_SECRET_VALUE_RE.search(stripped_val)
        or (_MCP_SECRET_KEY_NAME_RE.search(key) and len(stripped_val) >= 8)
    ):
        findings.append(
            _finding(
                source.file_label,
                key_line,
                "mcp-inline-secret",
                "agent-config",
                f"Manifest stores a literal credential in '{key}'",
            )
        )
    if key in _MANIFEST_PROMPT_KEYS:
        findings.extend(
            _inspect_prompt_string(
                stripped_val,
                owner_label=f"Manifest '{key}'",
                source=replace(source, fallback_line=key_line),
            )
        )
    return findings


def _inspect_company_announcements(
    items: list[Any],
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    ann_source = replace(
        source,
        fallback_line=_find_line_number(source.raw_text, "companyAnnouncements"),
    )
    for item in items:
        if isinstance(item, str) and item.strip():
            findings.extend(
                _inspect_prompt_string(
                    item,
                    owner_label="Manifest 'companyAnnouncements'",
                    source=ann_source,
                )
            )
    return findings


def _inspect_manifest_top_level_fields(
    data: Mapping[str, Any],
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key, val in data.items():
        if not isinstance(key, str) or key in {"env", "headers"}:
            continue
        if isinstance(val, str) and val.strip():
            findings.extend(_inspect_manifest_string_field(key, val, source))
        elif key == "companyAnnouncements" and isinstance(val, list):
            findings.extend(_inspect_company_announcements(val, source))
    return findings


def _inspect_manifest_mcp_mapping(
    mcp_val: Mapping[Any, Any],
    source: _ConfigSourceContext,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for s_name, s_cfg in mcp_val.items():
        if isinstance(s_name, str) and isinstance(s_cfg, Mapping):
            findings.extend(
                _inspect_mcp_server_entry(
                    s_name,
                    s_cfg,
                    raw_text=source.raw_text,
                    file_label=source.file_label,
                )
            )
    return findings


def _inspect_manifest_mcp_and_hooks(
    data: Mapping[str, Any],
    *,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for mcp_key in ("mcpServers", "servers", "mcp"):
        mcp_val = data.get(mcp_key)
        if isinstance(mcp_val, Mapping):
            findings.extend(_inspect_manifest_mcp_mapping(mcp_val, source))
        elif mcp_val is not None:
            _collect_string_or_list_refs(mcp_val, source.raw_text, refs.mcp_paths)

    if "hooks" in data:
        findings.extend(
            _inspect_hook_entries(
                data["hooks"],
                source=source,
                allowed=allowed,
                refs=refs,
            )
        )
    return findings


def _collect_manifest_refs_and_icons(
    data: Mapping[str, Any],
    *,
    source: _ConfigSourceContext,
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for icon_key in ("logo", "icon"):
        icon_val = data.get(icon_key)
        if not isinstance(icon_val, str) or not icon_val.strip():
            continue
        icon_line = _find_line_number(source.raw_text, icon_val)
        if _is_remote_url(icon_val):
            findings.append(
                _finding(
                    source.file_label,
                    icon_line,
                    "remote-image",
                    "agent-config",
                    f"Manifest '{icon_key}' references remote or embedded URI '{icon_val}'",
                )
            )
        else:
            refs.doc_paths.append((icon_line, icon_val.strip()))

    _collect_string_or_list_refs(data.get("skills"), source.raw_text, refs.skill_paths)
    _collect_string_or_list_refs(
        data.get("contextFileName"), source.raw_text, refs.doc_paths
    )
    for doc_key in ("rules", "commands", "agents", "prompts"):
        _collect_string_or_list_refs(data.get(doc_key), source.raw_text, refs.doc_paths)
    return findings


def _inspect_manifest_plugins(
    plugins: Any,
    *,
    source: _ConfigSourceContext,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    if not isinstance(plugins, list):
        return []
    findings: list[dict[str, Any]] = []
    for plugin_entry in plugins:
        if isinstance(plugin_entry, Mapping):
            findings.extend(
                _inspect_manifest_object(
                    plugin_entry,
                    raw_text=source.raw_text,
                    file_label=source.file_label,
                    allowed=allowed,
                    refs=refs,
                )
            )
    return findings


def _inspect_manifest_object(
    data: Mapping[str, Any],
    *,
    raw_text: str,
    file_label: str,
    allowed: frozenset[str],
    refs: _ManifestRefs,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source = _ConfigSourceContext(raw_text=raw_text, file_label=file_label)

    for helper_key in _SETTINGS_HELPER_COMMAND_KEYS:
        helper_cmd = data.get(helper_key)
        if isinstance(helper_cmd, str) and helper_cmd.strip():
            findings.extend(
                _inspect_config_shell_command(
                    helper_cmd,
                    exec_message=f"Settings '{helper_key}' executes shell helper command '{helper_cmd.strip()}'",
                    source=source,
                    allowed=allowed,
                    refs=refs,
                )
            )

    findings.extend(_inspect_manifest_env_and_headers(data, source))
    findings.extend(_inspect_manifest_top_level_fields(data, source))
    findings.extend(
        _inspect_manifest_mcp_and_hooks(data, source=source, allowed=allowed, refs=refs)
    )
    findings.extend(_collect_manifest_refs_and_icons(data, source=source, refs=refs))
    findings.extend(
        _inspect_manifest_plugins(
            data.get("plugins"), source=source, allowed=allowed, refs=refs
        )
    )
    return findings


def _read_host_manifest_text(
    path: Path, file_label: str
) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-encoding",
                    "agent-config",
                    "Host manifest file is not valid UTF-8",
                ),
                "medium",
            )
        ]
    except OSError as exc:
        return None, [
            _finding(
                file_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read host manifest file '{file_label}': {exc}",
            )
        ]


def _vet_host_json_manifest(
    path: Path,
    *,
    root: Path | None = None,
    allowed: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], _ManifestRefs]:
    refs = _ManifestRefs()
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return (
            [
                _finding(
                    file_label,
                    1,
                    "symlink-file",
                    "filesystem",
                    "Symlinked host manifest file is not permitted",
                )
            ],
            refs,
        )
    if not path.is_file():
        raise ValueError(f"Host manifest file not found: {path}")

    raw_text, read_findings = _read_host_manifest_text(path, file_label)
    if raw_text is None:
        return read_findings, refs

    findings = _scan_hidden_unicode_text(raw_text, file_label, category="agent-config")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        findings.append(
            _with_severity(
                _finding(
                    file_label,
                    exc.lineno,
                    "invalid-json",
                    "agent-config",
                    f"Malformed host manifest JSON: {exc.msg}",
                ),
                "medium",
            )
        )
        return findings, refs

    if not isinstance(data, Mapping):
        findings.append(
            _with_severity(
                _finding(
                    file_label,
                    1,
                    "invalid-manifest",
                    "agent-config",
                    "Host manifest root must be a JSON object",
                ),
                "medium",
            )
        )
        return findings, refs

    findings.extend(
        _inspect_manifest_object(
            data,
            raw_text=raw_text,
            file_label=file_label,
            allowed=allowed,
            refs=refs,
        )
    )
    return findings, refs


def _validate_manifest_refs_in_skill(
    refs: _ManifestRefs,
    *,
    package: Path,
    source_label: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    boundary = _BoundaryContext(
        base_dir=package,
        boundary_root=package,
        source_label=source_label,
        category="agent-config",
    )
    all_refs = [
        *refs.mcp_paths,
        *refs.hook_paths,
        *refs.skill_paths,
        *refs.doc_paths,
        *refs.script_paths,
    ]
    for line_no, raw_ref in all_refs:
        if raw_ref == ".":
            continue
        _, path_errs = _resolve_bounded_local_path(
            raw_ref, line=line_no, boundary=boundary
        )
        findings.extend(path_errs)
    return findings


def _effective_skill_capabilities(
    skill_name: str,
    allowed_capabilities: Collection[str] | None,
    *,
    trust_builtin_allowlists: bool,
) -> frozenset[str]:
    explicit = _normalize_capabilities(allowed_capabilities)
    if not trust_builtin_allowlists:
        return explicit
    builtin = SKILL_CAPABILITY_ALLOWLIST.get(skill_name, frozenset())
    return explicit | builtin


def _is_mcp_filename(name_lower: str) -> bool:
    return name_lower in {".mcp.json", "mcp.json", "mcp_config.json"}


def _is_host_manifest_rel(rel_posix: str, name_lower: str) -> bool:
    return rel_posix in {
        ".cursor-plugin/plugin.json",
        ".claude-plugin/plugin.json",
        ".cursor-plugin/marketplace.json",
        ".claude-plugin/marketplace.json",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".cursor/hooks.json",
    } or name_lower in {"gemini-extension.json", "hooks.json"}


def _vet_skill_config_file(
    entry: Path,
    *,
    rel_posix: str,
    top_dir: str,
    package: Path,
    effective_caps: frozenset[str],
) -> list[dict[str, Any]] | None:
    suffix = entry.suffix.lower()
    name_lower = entry.name.lower()
    if _is_mcp_filename(name_lower):
        return vet_mcp_config(entry, root=package)
    if _is_host_manifest_rel(rel_posix, name_lower) or (
        top_dir == "hooks" and suffix == ".json"
    ):
        manifest_findings, manifest_refs = _vet_host_json_manifest(
            entry, root=package, allowed=effective_caps
        )
        return [
            *manifest_findings,
            *_validate_manifest_refs_in_skill(
                manifest_refs, package=package, source_label=rel_posix
            ),
        ]
    if suffix in {".yaml", ".yml"} and top_dir in {"agents", "hooks", ""}:
        return _vet_agent_yaml(
            entry,
            boundary_root=package,
            root=package,
            allowed=effective_caps,
        )
    if suffix == ".svg":
        return _vet_svg_file(entry, root=package)
    return None


def _vet_skill_content_file(
    entry: Path,
    *,
    rel_posix: str,
    top_dir: str,
    package: Path,
    effective_caps: frozenset[str],
) -> list[dict[str, Any]] | None:
    suffix = entry.suffix.lower()
    if top_dir == "hooks" and suffix not in {".md", ".mdc"}:
        findings: list[dict[str, Any]] = []
        if not _process_capability_permitted(effective_caps):
            findings.append(
                _finding(
                    rel_posix,
                    1,
                    "shell-execution",
                    "agent-config",
                    f"Untrusted hook script '{rel_posix}' executes automatically on host events",
                )
            )
        findings.extend(vet_script(entry, effective_caps, root=package))
        return findings
    if top_dir == "scripts" or suffix in _SCRIPT_SUFFIXES:
        return vet_script(entry, effective_caps, root=package)
    is_shebang, probe_findings = _probe_shebang(entry, root=package)
    if probe_findings:
        return probe_findings
    if is_shebang:
        return vet_script(entry, effective_caps, root=package)
    if suffix in {".md", ".mdc"}:
        return vet_markdown(entry, root=package)
    return None


def _vet_skill_entry(
    entry: Path,
    *,
    package: Path,
    package_resolved: Path,
    effective_caps: frozenset[str],
) -> tuple[int, list[dict[str, Any]]]:
    rel_parts = entry.relative_to(package).parts
    if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts):
        return 0, []
    rel_posix = entry.relative_to(package).as_posix()
    if entry.is_symlink():
        return (
            0,
            [
                _finding(
                    rel_posix,
                    1,
                    "symlink-file",
                    "filesystem",
                    f"Symlink is not permitted inside skill package: {rel_posix}",
                )
            ],
        )
    if not entry.resolve().is_relative_to(package_resolved):
        return (
            0,
            [
                _finding(
                    rel_posix,
                    1,
                    "escaping-path",
                    "filesystem",
                    f"File escapes skill package boundary: {rel_posix}",
                )
            ],
        )
    if (
        not entry.is_file()
        or entry.suffix in _EXCLUDED_FILE_SUFFIXES
        or entry.name in _EXCLUDED_FILE_NAMES
    ):
        return 0, []

    top_dir = rel_parts[0] if len(rel_parts) > 1 else ""
    matched = _vet_skill_config_file(
        entry,
        rel_posix=rel_posix,
        top_dir=top_dir,
        package=package,
        effective_caps=effective_caps,
    )
    if matched is None:
        matched = _vet_skill_content_file(
            entry,
            rel_posix=rel_posix,
            top_dir=top_dir,
            package=package,
            effective_caps=effective_caps,
        )
    if matched is None:
        return 0, []
    return 1, matched


def vet_skill(
    package: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    trust_builtin_allowlists: bool = True,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Vet a standalone skill directory for risky scripts, manifests, assets, and hidden prompt payloads."""
    if package.is_symlink():
        label = _format_file_label(package, root)
        return {
            "target": str(package),
            "kind": "skill",
            "skill": package.name,
            "passed": False,
            "scanned_files": 0,
            "findings": [
                _finding(
                    label,
                    1,
                    "symlink-directory",
                    "filesystem",
                    "Skill package directory must not be a symlink",
                )
            ],
        }
    if not package.is_dir():
        raise ValueError(f"Skill directory not found: {package}")

    skill_name = package.name
    effective_caps = _effective_skill_capabilities(
        skill_name,
        allowed_capabilities,
        trust_builtin_allowlists=trust_builtin_allowlists,
    )

    findings: list[dict[str, Any]] = []
    scanned_files = 0
    package_resolved = package.resolve()

    skill_md = package / "SKILL.md"
    if not skill_md.exists() and not skill_md.is_symlink():
        findings.append(
            _finding(
                "SKILL.md",
                1,
                "missing-skill-manifest",
                "prompt",
                "Skill directory is missing SKILL.md",
            )
        )

    sorted_entries = sorted(
        package.rglob("*"), key=lambda p: p.relative_to(package).as_posix()
    )
    for entry in sorted_entries:
        count, entry_findings = _vet_skill_entry(
            entry,
            package=package,
            package_resolved=package_resolved,
            effective_caps=effective_caps,
        )
        scanned_files += count
        findings.extend(entry_findings)

    return {
        "target": str(package),
        "kind": "skill",
        "skill": skill_name,
        "passed": len(findings) == 0,
        "scanned_files": scanned_files,
        "findings": findings,
    }


def _load_hosts_catalog_raw(
    hosts_path: Path,
) -> tuple[str | None, list[dict[str, Any]]]:
    hosts_label = "catalog/hosts.json"
    if not hosts_path.exists() and not hosts_path.is_symlink():
        return None, []
    if hosts_path.is_symlink():
        return None, [
            _finding(
                hosts_label,
                1,
                "symlink-file",
                "filesystem",
                "Symlinked hosts catalog file is not permitted",
            )
        ]
    if hosts_path.is_dir():
        return None, [
            _with_severity(
                _finding(
                    hosts_label,
                    1,
                    "invalid-manifest",
                    "agent-config",
                    "Hosts catalog path must be a JSON file",
                ),
                "medium",
            )
        ]
    try:
        return (
            read_utf8_text(
                hosts_path,
                label="Hosts catalog 'catalog/hosts.json'",
                optional=True,
            ),
            [],
        )
    except FredCliError as exc:
        if exc.code == "invalid_encoding":
            return None, [
                _with_severity(
                    _finding(
                        hosts_label,
                        1,
                        "invalid-encoding",
                        "agent-config",
                        "Hosts catalog file is not valid UTF-8",
                    ),
                    "medium",
                )
            ]
        return None, [
            _finding(
                hosts_label,
                1,
                "unreadable-file",
                "filesystem",
                f"Unable to read hosts catalog '{hosts_label}': {exc.__cause__ or exc}",
            )
        ]


def _extract_hosts_catalog_dirs(
    raw_text: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    hosts_label = "catalog/hosts.json"
    try:
        doc = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return [], [
            _with_severity(
                _finding(
                    hosts_label,
                    exc.lineno,
                    "invalid-json",
                    "agent-config",
                    f"Malformed JSON in hosts catalog: {exc.msg}",
                ),
                "medium",
            )
        ]
    if not isinstance(doc, Mapping):
        return [], [
            _with_severity(
                _finding(
                    hosts_label,
                    1,
                    "invalid-manifest",
                    "agent-config",
                    "Hosts catalog root must be a JSON object",
                ),
                "medium",
            )
        ]
    hosts = doc.get("hosts")
    if not isinstance(hosts, Mapping):
        return [], [
            _with_severity(
                _finding(
                    hosts_label,
                    1,
                    "invalid-manifest",
                    "agent-config",
                    "Hosts catalog must define a 'hosts' mapping",
                ),
                "medium",
            )
        ]
    extra_dirs: list[str] = []
    for cfg in hosts.values():
        if not isinstance(cfg, Mapping):
            return [], [
                _with_severity(
                    _finding(
                        hosts_label,
                        1,
                        "invalid-manifest",
                        "agent-config",
                        "Each entry in hosts catalog 'hosts' must be a JSON object",
                    ),
                    "medium",
                )
            ]
        proj = cfg.get("project_path")
        if isinstance(proj, str) and proj.strip():
            norm = proj.strip().strip("/")
            if norm:
                extra_dirs.append(norm)
    return extra_dirs, []


def _discover_host_skill_dirs(root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    dirs: list[str] = list(_DEFAULT_HOST_SKILL_DIRS)
    raw_text, findings = _load_hosts_catalog_raw(root / "catalog/hosts.json")
    if raw_text is None:
        return dirs, findings
    extra_dirs, parse_findings = _extract_hosts_catalog_dirs(raw_text)
    if parse_findings:
        return dirs, parse_findings
    for norm in extra_dirs:
        if norm not in dirs:
            dirs.append(norm)
    return dirs, []


def _prefix_skill_findings(
    skill_findings: list[dict[str, Any]], prefix: str
) -> list[dict[str, Any]]:
    prefixed: list[dict[str, Any]] = []
    for item in skill_findings:
        entry = dict(item)
        rel_file = str(entry.get("file", ""))
        entry["file"] = f"{prefix}/{rel_file}" if rel_file else prefix
        prefixed.append(entry)
    return prefixed


def _is_trusted_toolkit_skill(candidate: Path, root: Path) -> bool:
    try:
        skills_root = (root / "skills").resolve()
        return skills_root.is_dir() and candidate.resolve().is_relative_to(skills_root)
    except OSError:
        return False


def _vet_script_or_markdown_file(
    target: Path,
    allowed_capabilities: Collection[str] | None,
    parent: Path,
) -> list[dict[str, Any]]:
    is_shebang, probe_findings = _probe_shebang(target, root=parent)
    if probe_findings:
        return probe_findings
    if is_shebang:
        return vet_script(target, allowed_capabilities, root=parent)
    return vet_markdown(target, root=parent)


def _vet_single_file_target(
    target: Path,
    allowed_capabilities: Collection[str] | None,
) -> dict[str, Any]:
    name_lower = target.name.lower()
    suffix_lower = target.suffix.lower()
    allowed = _normalize_capabilities(allowed_capabilities)
    parent = target.parent
    rel_from_parent = (
        f"{parent.name}/{target.name}"
        if parent.name
        in {".claude", ".cursor", ".cursor-plugin", ".claude-plugin", "hooks", "agents"}
        else target.name
    )

    if _is_mcp_filename(name_lower):
        file_findings = vet_mcp_config(target, root=parent)
    elif _is_host_manifest_rel(rel_from_parent, name_lower):
        file_findings, _ = _vet_host_json_manifest(target, root=parent, allowed=allowed)
    elif suffix_lower in {".yaml", ".yml"}:
        file_findings = _vet_agent_yaml(
            target, boundary_root=parent, root=parent, allowed=allowed
        )
    elif suffix_lower == ".svg":
        file_findings = _vet_svg_file(target, root=parent)
    elif suffix_lower in _SCRIPT_SUFFIXES:
        file_findings = vet_script(target, allowed_capabilities, root=parent)
    else:
        file_findings = _vet_script_or_markdown_file(
            target, allowed_capabilities, parent
        )

    return {
        "target": str(target),
        "kind": "file",
        "passed": len(file_findings) == 0,
        "scanned_files": 1,
        "skills_scanned": [],
        "configs_scanned": [target.name],
        "findings": file_findings,
    }


def _scan_referenced_skill_dir(skill_dir: Path, ctx: _RepoScanContext) -> int:
    rel_skill = _format_file_label(skill_dir, ctx.repo_root)
    if rel_skill in {"", "."} or rel_skill in ctx.seen_skills:
        return 0
    ctx.seen_skills.add(rel_skill)
    trusted = _is_trusted_toolkit_skill(skill_dir, ctx.root)
    sub_res = vet_skill(
        skill_dir,
        ctx.allowed_capabilities,
        trust_builtin_allowlists=trusted,
        root=ctx.root,
    )
    ctx.skills_scanned.append(rel_skill)
    ctx.findings.extend(_prefix_skill_findings(sub_res["findings"], rel_skill))
    return int(sub_res["scanned_files"])


def _follow_repo_mcp_refs(
    mcp_paths: list[tuple[int, str]],
    *,
    boundary: _BoundaryContext,
    ctx: _RepoScanContext,
) -> int:
    scanned_count = 0
    for line_no, raw_mcp in mcp_paths:
        mcp_path, errs = _resolve_bounded_local_path(
            raw_mcp, line=line_no, boundary=boundary
        )
        if errs:
            ctx.findings.extend(errs)
            continue
        if mcp_path is None or not mcp_path.is_file():
            continue
        mcp_label = _format_file_label(mcp_path, ctx.repo_root)
        if _mark_config_scanned(ctx, mcp_label):
            scanned_count += 1
            ctx.findings.extend(vet_mcp_config(mcp_path, root=ctx.repo_root))
    return scanned_count


def _vet_repo_hook_file(hook_path: Path, ctx: _RepoScanContext) -> int:
    hook_label = _format_file_label(hook_path, ctx.repo_root)
    if not _mark_config_scanned(ctx, hook_label):
        return 0
    scanned_count = 1
    suffix = hook_path.suffix.lower()
    if suffix == ".json":
        sub_f, sub_refs = _vet_host_json_manifest(
            hook_path, root=ctx.repo_root, allowed=ctx.allowed
        )
        ctx.findings.extend(sub_f)
        scanned_count += _follow_repo_manifest_refs(
            sub_refs, source_label=hook_label, ctx=ctx
        )
    elif suffix in {".yaml", ".yml"}:
        ctx.findings.extend(
            _vet_agent_yaml(
                hook_path,
                boundary_root=ctx.repo_root,
                root=ctx.repo_root,
                allowed=ctx.allowed,
            )
        )
    else:
        ctx.findings.extend(
            vet_script(hook_path, ctx.allowed_capabilities, root=ctx.repo_root)
        )
    return scanned_count


def _follow_repo_hook_refs(
    hook_paths: list[tuple[int, str]],
    *,
    boundary: _BoundaryContext,
    ctx: _RepoScanContext,
) -> int:
    scanned_count = 0
    for line_no, raw_hook in hook_paths:
        hook_path, errs = _resolve_bounded_local_path(
            raw_hook, line=line_no, boundary=boundary
        )
        if errs:
            ctx.findings.extend(errs)
            continue
        if hook_path is not None and hook_path.is_file():
            scanned_count += _vet_repo_hook_file(hook_path, ctx)
    return scanned_count


def _follow_repo_script_refs(
    script_paths: list[tuple[int, str]],
    *,
    boundary: _BoundaryContext,
    ctx: _RepoScanContext,
) -> int:
    scanned_count = 0
    for line_no, raw_script in script_paths:
        script_path, errs = _resolve_bounded_local_path(
            raw_script, line=line_no, boundary=boundary
        )
        if errs:
            ctx.findings.extend(errs)
            continue
        if script_path is None or not script_path.is_file():
            continue
        script_label = _format_file_label(script_path, ctx.repo_root)
        if _mark_config_scanned(ctx, script_label):
            scanned_count += 1
            ctx.findings.extend(
                vet_script(script_path, ctx.allowed_capabilities, root=ctx.repo_root)
            )
    return scanned_count


def _has_skill_manifest(skill_dir: Path) -> bool:
    skill_md = skill_dir / "SKILL.md"
    return skill_md.exists() or skill_md.is_symlink()


def _scan_skill_ref_directory(skill_path: Path, ctx: _RepoScanContext) -> int:
    if _has_skill_manifest(skill_path):
        return _scan_referenced_skill_dir(skill_path, ctx)
    scanned_count = 0
    for child in sorted(skill_path.iterdir(), key=lambda p: p.name):
        if child.is_symlink():
            rel_child = _format_file_label(child, ctx.repo_root)
            ctx.findings.append(
                _finding(
                    rel_child,
                    1,
                    "symlink-directory",
                    "filesystem",
                    f"Skill directory must not be a symlink: {rel_child}",
                )
            )
        elif child.is_dir() and _has_skill_manifest(child):
            scanned_count += _scan_referenced_skill_dir(child, ctx)
    return scanned_count


def _follow_repo_skill_refs(
    skill_paths: list[tuple[int, str]],
    *,
    boundary: _BoundaryContext,
    ctx: _RepoScanContext,
) -> int:
    scanned_count = 0
    for line_no, raw_skill in skill_paths:
        if raw_skill in {".", "./"}:
            continue
        skill_path, errs = _resolve_bounded_local_path(
            raw_skill, line=line_no, boundary=boundary
        )
        if errs:
            ctx.findings.extend(errs)
            continue
        if skill_path is not None and skill_path.is_dir():
            scanned_count += _scan_skill_ref_directory(skill_path, ctx)
    return scanned_count


def _vet_repo_doc_directory(doc_dir: Path, ctx: _RepoScanContext) -> int:
    scanned_count = 0
    fresh_ctx = replace(ctx, active_chain=frozenset())
    for md_entry in sorted(doc_dir.rglob("*")):
        if md_entry.is_symlink():
            rel_md = _format_file_label(md_entry, ctx.repo_root)
            ctx.findings.append(
                _finding(
                    rel_md,
                    1,
                    "symlink-file",
                    "filesystem",
                    f"Symlinked instruction file is not permitted: {rel_md}",
                )
            )
        elif md_entry.is_file() and md_entry.suffix.lower() in {".md", ".mdc"}:
            scanned_count += _vet_markdown_with_shims(md_entry, fresh_ctx)
    return scanned_count


def _vet_repo_doc_target(doc_path: Path, ctx: _RepoScanContext) -> int:
    if doc_path.is_dir():
        return _vet_repo_doc_directory(doc_path, ctx)
    if not doc_path.is_file():
        return 0
    doc_suffix = doc_path.suffix.lower()
    if doc_suffix == ".svg":
        doc_label = _format_file_label(doc_path, ctx.repo_root)
        if not _mark_config_scanned(ctx, doc_label):
            return 0
        ctx.findings.extend(_vet_svg_file(doc_path, root=ctx.repo_root))
        return 1
    if doc_suffix in {".md", ".mdc", ".markdown", ".txt", ""}:
        return _vet_markdown_with_shims(
            doc_path, replace(ctx, active_chain=frozenset())
        )
    return 0


def _follow_repo_doc_refs(
    doc_paths: list[tuple[int, str]],
    *,
    boundary: _BoundaryContext,
    ctx: _RepoScanContext,
) -> int:
    scanned_count = 0
    for line_no, raw_doc in doc_paths:
        doc_path, errs = _resolve_bounded_local_path(
            raw_doc, line=line_no, boundary=boundary
        )
        if errs:
            ctx.findings.extend(errs)
            continue
        if doc_path is not None and doc_path.exists():
            scanned_count += _vet_repo_doc_target(doc_path, ctx)
    return scanned_count


def _follow_repo_manifest_refs(
    refs: _ManifestRefs,
    *,
    source_label: str,
    ctx: _RepoScanContext,
) -> int:
    boundary = _BoundaryContext(
        base_dir=ctx.repo_root,
        boundary_root=ctx.repo_root,
        source_label=source_label,
        category="agent-config",
    )
    scanned_count = _follow_repo_mcp_refs(refs.mcp_paths, boundary=boundary, ctx=ctx)
    scanned_count += _follow_repo_hook_refs(refs.hook_paths, boundary=boundary, ctx=ctx)
    scanned_count += _follow_repo_script_refs(
        refs.script_paths, boundary=boundary, ctx=ctx
    )
    scanned_count += _follow_repo_skill_refs(
        refs.skill_paths, boundary=boundary, ctx=ctx
    )
    scanned_count += _follow_repo_doc_refs(refs.doc_paths, boundary=boundary, ctx=ctx)
    return scanned_count


def _scan_host_skills_dir(rel_skill_root: str, ctx: _RepoScanContext) -> int:
    host_skills_dir = ctx.repo_root / rel_skill_root
    if host_skills_dir.is_symlink():
        ctx.findings.append(
            _finding(
                rel_skill_root,
                1,
                "symlink-directory",
                "filesystem",
                f"Host skill directory must not be a symlink: {rel_skill_root}",
            )
        )
        return 0
    if not host_skills_dir.is_dir():
        return 0

    scanned_files = 0
    for child in sorted(host_skills_dir.iterdir(), key=lambda p: p.name):
        rel_child = f"{rel_skill_root}/{child.name}"
        if child.is_symlink():
            ctx.findings.append(
                _finding(
                    rel_child,
                    1,
                    "symlink-directory",
                    "filesystem",
                    f"Skill directory must not be a symlink: {rel_child}",
                )
            )
            continue
        if not child.is_dir():
            continue
        has_scripts = (child / "scripts").exists() or (child / "hooks").exists()
        if _has_skill_manifest(child) or has_scripts:
            scanned_files += _scan_referenced_skill_dir(child, ctx)
    return scanned_files


def _scan_repo_instructions(ctx: _RepoScanContext) -> int:
    candidate_paths: list[Path] = [
        ctx.repo_root / rel for rel in _REPO_INSTRUCTION_FILES
    ]
    for pattern in _REPO_INSTRUCTION_GLOBS:
        candidate_paths.extend(sorted(ctx.repo_root.glob(pattern)))

    scanned_files = 0
    fresh_ctx = replace(ctx, active_chain=frozenset())
    for inst_path in candidate_paths:
        if inst_path.exists() or inst_path.is_symlink():
            scanned_files += _vet_markdown_with_shims(inst_path, fresh_ctx)
    return scanned_files


def _scan_repo_mcp_configs(ctx: _RepoScanContext) -> int:
    scanned_files = 0
    for rel_mcp in _REPO_MCP_FILES:
        mcp_path = ctx.repo_root / rel_mcp
        if not mcp_path.exists() and not mcp_path.is_symlink():
            continue
        rel_label = _format_file_label(mcp_path, ctx.repo_root)
        if _mark_config_scanned(ctx, rel_label):
            scanned_files += 1
            ctx.findings.extend(vet_mcp_config(mcp_path, root=ctx.repo_root))
    return scanned_files


def _scan_repo_host_manifests(ctx: _RepoScanContext) -> int:
    scanned_files = 0
    for rel_manifest in _REPO_HOST_MANIFEST_FILES:
        manifest_path = ctx.repo_root / rel_manifest
        if not manifest_path.exists() and not manifest_path.is_symlink():
            continue
        rel_label = _format_file_label(manifest_path, ctx.repo_root)
        if not _mark_config_scanned(ctx, rel_label):
            continue
        scanned_files += 1
        manifest_findings, manifest_refs = _vet_host_json_manifest(
            manifest_path,
            root=ctx.repo_root,
            allowed=ctx.allowed,
        )
        ctx.findings.extend(manifest_findings)
        scanned_files += _follow_repo_manifest_refs(
            manifest_refs,
            source_label=rel_label,
            ctx=ctx,
        )
    return scanned_files


def _vet_standalone_skill_target(
    target: Path,
    allowed_capabilities: Collection[str] | None,
    *,
    root: Path,
) -> dict[str, Any]:
    trusted = _is_trusted_toolkit_skill(target, root)
    skill_res = vet_skill(
        target,
        allowed_capabilities,
        trust_builtin_allowlists=trusted,
        root=root,
    )
    return {
        "target": str(target),
        "kind": "skill",
        "skill": skill_res["skill"],
        "passed": skill_res["passed"],
        "scanned_files": skill_res["scanned_files"],
        "skills_scanned": [skill_res["skill"]],
        "configs_scanned": [],
        "findings": skill_res["findings"],
    }


def _vet_repository_target(
    target: Path,
    allowed_capabilities: Collection[str] | None,
    *,
    root: Path,
) -> dict[str, Any]:
    ctx = _RepoScanContext(
        repo_root=target.resolve(),
        root=root,
        allowed_capabilities=allowed_capabilities,
        allowed=_normalize_capabilities(allowed_capabilities),
    )
    scanned_files = 0
    host_skill_dirs, host_catalog_findings = _discover_host_skill_dirs(root)
    if host_catalog_findings:
        scanned_files += 1
        ctx.configs_scanned.append("catalog/hosts.json")
        ctx.findings.extend(host_catalog_findings)
    for rel_skill_root in host_skill_dirs:
        scanned_files += _scan_host_skills_dir(rel_skill_root, ctx)
    scanned_files += _scan_repo_instructions(ctx)
    scanned_files += _scan_repo_mcp_configs(ctx)
    scanned_files += _scan_repo_host_manifests(ctx)
    return {
        "target": str(target),
        "kind": "repository",
        "passed": len(ctx.findings) == 0,
        "scanned_files": scanned_files,
        "skills_scanned": ctx.skills_scanned,
        "configs_scanned": ctx.configs_scanned,
        "findings": ctx.findings,
    }


def vet_target(
    target: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Vet a standalone skill directory, an individual config/script file, or a repository."""
    if target.is_symlink():
        label = _format_file_label(target, None)
        return {
            "target": str(target),
            "kind": "symlink",
            "passed": False,
            "scanned_files": 0,
            "skills_scanned": [],
            "configs_scanned": [],
            "findings": [
                _finding(
                    label,
                    1,
                    "symlink-target",
                    "filesystem",
                    "Target path must not be a symlink",
                )
            ],
        }
    if not target.exists():
        raise FileNotFoundError(f"Target path not found: {target}")
    if target.is_file():
        return _vet_single_file_target(target, allowed_capabilities)
    if _has_skill_manifest(target):
        return _vet_standalone_skill_target(target, allowed_capabilities, root=root)
    return _vet_repository_target(target, allowed_capabilities, root=root)

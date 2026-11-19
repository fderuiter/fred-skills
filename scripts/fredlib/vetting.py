"""Offline static security vetting for skill packages and repository agent configurations.

Uses only the Python 3.12 standard library and performs zero network requests.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .common import ROOT

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

_SCRIPT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ksh", ".ps1", ".bat", ".cmd"}
)

_PYTHON_NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "curl_cffi",
        "ftplib",
        "grpc",
        "http.client",
        "httpx",
        "imaplib",
        "paramiko",
        "poplib",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "telnetlib",
        "urllib.request",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc",
        "xmlrpc.client",
    }
)

_PYTHON_NETWORK_CALLS = frozenset(
    {
        "urlopen",
        "urlretrieve",
        "create_connection",
        "HTTPConnection",
        "HTTPSConnection",
    }
)

_PYTHON_SHELL_ATTR_CALLS: dict[str, frozenset[str]] = {
    "os": frozenset(
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
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
        }
    ),
    "pty": frozenset({"spawn"}),
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

_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_INLINE_CODE_RE = re.compile(r"(`+)([^`]*?)\1")

_MD_INLINE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_MD_REF_IMAGE_USE_RE = re.compile(r"!\[([^\]]*)\](?:\[([^\]]*)\])?")
_MD_REF_DEF_RE = re.compile(r"^[ \t]{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+.*)?$")
_HTML_IMG_RE = re.compile(
    r"""<img\b[^>]*?\bsrc\s*=\s*(?:["']([^"']+)["']|([^\s>]+))""",
    re.IGNORECASE,
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


def _format_file_label(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            resolved_root = root.resolve()
            if not path.is_symlink():
                resolved_path = path.resolve()
                if resolved_path.is_relative_to(resolved_root):
                    return resolved_path.relative_to(resolved_root).as_posix()
            elif path.parent.resolve().is_relative_to(resolved_root):
                parent_rel = path.parent.resolve().relative_to(resolved_root)
                return (parent_rel / path.name).as_posix()
        except OSError:
            pass
    return path.as_posix()


def _finding(
    file_label: str,
    line: int,
    rule: str,
    severity: str,
    category: str,
    message: str,
) -> dict[str, Any]:
    return {
        "file": file_label,
        "line": max(1, line),
        "rule": rule,
        "severity": severity,
        "category": category,
        "message": message,
    }


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


def _match_sensitive_path(raw_path: str) -> tuple[str, str] | None:
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate:
        return None
    for pattern, capability, label in _SENSITIVE_PATH_RULES:
        if pattern.search(candidate):
            return capability, label
    return None


def _extract_div_chain(node: ast.BinOp) -> list[str]:
    parts: list[str] = []
    curr: ast.AST = node
    while isinstance(curr, ast.BinOp) and isinstance(curr.op, ast.Div):
        right = curr.right
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            parts.append(right.value)
        else:
            parts.append("<expr>")
        curr = curr.left
    if isinstance(curr, ast.Constant) and isinstance(curr.value, str):
        parts.append(curr.value)
    elif isinstance(curr, ast.Call):
        parts.append("~")
    elif isinstance(curr, ast.Name):
        parts.append(f"<{curr.id}>")
    parts.reverse()
    return parts


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


class _PythonSecurityVisitor(ast.NodeVisitor):
    def __init__(self, file_label: str, allowed: frozenset[str]) -> None:
        self.file_label = file_label
        self.allowed = allowed
        self.findings: list[dict[str, Any]] = []
        self._imported_aliases: dict[str, str] = {}
        self._seen_keys: set[tuple[int, str, str]] = set()

    def _add(
        self, line: int, rule: str, severity: str, category: str, message: str
    ) -> None:
        key = (max(1, line), rule, message)
        if key not in self._seen_keys:
            self._seen_keys.add(key)
            self.findings.append(
                _finding(self.file_label, line, rule, severity, category, message)
            )

    def _is_network_module(self, mod: str) -> bool:
        if mod in _PYTHON_NETWORK_MODULES:
            return True
        return any(mod.startswith(f"{net}.") for net in _PYTHON_NETWORK_MODULES)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod = alias.name
            bound = alias.asname or mod.split(".")[0]
            self._imported_aliases[bound] = mod
            if self._is_network_module(mod) and not _capability_permitted(
                "network", self.allowed
            ):
                self._add(
                    node.lineno,
                    "network-client",
                    "high",
                    "script",
                    f"Python script imports network module '{mod}'",
                )
            if mod in {"ctypes", "cffi"} and not _capability_permitted(
                "native-ffi", self.allowed
            ):
                self._add(
                    node.lineno,
                    "dynamic-execution",
                    "high",
                    "script",
                    f"Python script imports native FFI module '{mod}'",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base_mod = node.module or ""
        for alias in node.names:
            full = f"{base_mod}.{alias.name}" if base_mod else alias.name
            bound = alias.asname or alias.name
            self._imported_aliases[bound] = full
            if (
                self._is_network_module(base_mod)
                or self._is_network_module(full)
                or (
                    base_mod in {"urllib.request", "http.client"}
                    or alias.name in _PYTHON_NETWORK_CALLS
                )
            ) and not _capability_permitted("network", self.allowed):
                target_label = (
                    full if self._is_network_module(full) else (base_mod or alias.name)
                )
                self._add(
                    node.lineno,
                    "network-client",
                    "high",
                    "script",
                    f"Python script imports network client '{target_label}'",
                )
        self.generic_visit(node)

    def _resolve_callee(self, func: ast.AST) -> str | None:
        raw = _dotted_name(func)
        if raw is None:
            return None
        head, _, tail = raw.partition(".")
        if head in self._imported_aliases:
            resolved_head = self._imported_aliases[head]
            return f"{resolved_head}.{tail}" if tail else resolved_head
        return raw

    def _check_shell_kwarg(self, node: ast.Call, callee: str) -> None:
        for kw in node.keywords:
            if kw.arg == "shell":
                is_false = (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False
                )
                if not is_false and not _capability_permitted(
                    "shell-execution", self.allowed
                ):
                    self._add(
                        node.lineno,
                        "shell-execution",
                        "high",
                        "script",
                        f"Call to '{callee}' enables shell=True execution",
                    )

    def _check_env_access(self, line: int, key_node: ast.AST | None) -> None:
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value, str
        ):
            return
        env_name = key_node.value.strip()
        if env_name in _SAFE_ENV_VARS or env_name.startswith("FRED_"):
            return
        if _SECRET_ENV_PATTERN.match(env_name):
            cap = f"env:{env_name}"
            if not _capability_permitted(cap, self.allowed):
                self._add(
                    line,
                    "undeclared-credential-read",
                    "high",
                    "script",
                    f"Undeclared credential environment read '{env_name}' (requires capability '{cap}')",
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
                "high",
                "script",
                f"Undeclared sensitive path access '{label}' (requires capability '{cap}')",
            )

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._resolve_callee(node.func)
        if callee:
            short = callee.rsplit(".", 1)[-1]
            if (
                self._is_network_module(callee) or short in _PYTHON_NETWORK_CALLS
            ) and not _capability_permitted("network", self.allowed):
                self._add(
                    node.lineno,
                    "network-client",
                    "high",
                    "script",
                    f"Python script invokes network client '{callee}'",
                )

            if callee in {
                "eval",
                "exec",
                "builtins.eval",
                "builtins.exec",
                "__import__",
                "builtins.__import__",
            } and not _capability_permitted("dynamic-execution", self.allowed):
                self._add(
                    node.lineno,
                    "dynamic-execution",
                    "high",
                    "script",
                    f"Python script invokes dynamic code execution '{callee}'",
                )

            if callee in {"compile", "builtins.compile"} and len(node.args) >= 3:
                mode_arg = node.args[2]
                if (
                    isinstance(mode_arg, ast.Constant)
                    and mode_arg.value in {"eval", "exec", "single"}
                    and not _capability_permitted("dynamic-execution", self.allowed)
                ):
                    self._add(
                        node.lineno,
                        "dynamic-execution",
                        "high",
                        "script",
                        f"Python script compiles dynamic '{mode_arg.value}' code object",
                    )

            mod_prefix, _, attr_name = callee.rpartition(".")
            if (
                mod_prefix in _PYTHON_SHELL_ATTR_CALLS
                and attr_name in _PYTHON_SHELL_ATTR_CALLS[mod_prefix]
                and not _capability_permitted("shell-execution", self.allowed)
            ):
                self._add(
                    node.lineno,
                    "shell-execution",
                    "high",
                    "script",
                    f"Python script invokes shell/process execution '{callee}'",
                )

            if short in _SUBPROCESS_CALL_NAMES or mod_prefix == "subprocess":
                self._check_shell_kwarg(node, callee)

            if (
                callee in {"os.getenv", "os.environ.get", "os.environ.pop"}
                and node.args
            ):
                self._check_env_access(node.lineno, node.args[0])

            if short in {
                "open",
                "Path",
                "PurePath",
                "PurePosixPath",
                "PureWindowsPath",
                "expanduser",
                "join",
            }:
                str_parts = [
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
                if str_parts:
                    self._check_path_string(node.lineno, "/".join(str_parts))

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        target_name = self._resolve_callee(node.value)
        if target_name == "os.environ":
            self._check_env_access(node.lineno, node.slice)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div):
            chain = "/".join(_extract_div_chain(node))
            self._check_path_string(node.lineno, chain)
            # Walk non-Div children only so nested `a / ".claude" / "projects"` is evaluated as a single path.
            curr: ast.AST = node
            while isinstance(curr, ast.BinOp) and isinstance(curr.op, ast.Div):
                self.visit(curr.right)
                curr = curr.left
            self.visit(curr)
            return
        self.generic_visit(node)


def _vet_python_source(
    source: str, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source, filename=file_label)
    except SyntaxError as exc:
        findings = [
            _finding(
                file_label,
                exc.lineno or 1,
                "syntax-error",
                "medium",
                "script",
                f"Unable to parse Python script AST: {exc.msg}",
            )
        ]
        findings.extend(_vet_shell_source(source, file_label, allowed))
        return findings

    visitor = _PythonSecurityVisitor(file_label, allowed)
    visitor.visit(tree)
    return visitor.findings


def _vet_shell_source(
    source: str, file_label: str, allowed: frozenset[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or (stripped.startswith("#") and not stripped.startswith("#!")):
            continue
        if _SHELL_NETWORK_RE.search(raw_line) and not _capability_permitted(
            "network", allowed
        ):
            findings.append(
                _finding(
                    file_label,
                    idx,
                    "network-client",
                    "high",
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
                    idx,
                    "shell-execution",
                    "high",
                    "script",
                    "Script uses dynamic eval, subshell '-c', or pipe-to-shell execution",
                )
            )
        for path_match in _SHELL_SENSITIVE_PATH_RE.finditer(raw_line):
            matched = _match_sensitive_path(path_match.group(0))
            if matched is not None:
                cap, label = matched
                if not _capability_permitted(cap, allowed):
                    findings.append(
                        _finding(
                            file_label,
                            idx,
                            "undeclared-credential-read",
                            "high",
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
                        idx,
                        "undeclared-credential-read",
                        "high",
                        "script",
                        f"Undeclared credential environment read '{env_name}' (requires capability '{cap}')",
                    )
                )
    return findings


def vet_script(
    path: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Run offline AST and regex security checks on a Python or shell script."""
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "high",
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
                "high",
                "script",
                "Non-UTF-8 or binary executable in scripts directory is not permitted",
            ),
        ]

    suffix = path.suffix.lower()
    first_line = source.splitlines()[0] if source else ""
    if suffix == ".py" or (not suffix and "python" in first_line.lower()):
        findings.extend(_vet_python_source(source, file_label, allowed))
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
    findings: list[dict[str, Any]] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        seen_on_line: set[int] = set()
        for col_idx, ch in enumerate(line):
            label = _classify_hidden_unicode(
                ch, is_file_start=(line_idx == 1 and col_idx == 0)
            )
            if label is not None and ord(ch) not in seen_on_line:
                seen_on_line.add(ord(ch))
                findings.append(
                    _finding(
                        file_label,
                        line_idx,
                        "hidden-unicode",
                        "high",
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
    except UnicodeDecodeError:
        return []
    return _scan_hidden_unicode_text(text, file_label, category=category)


def _strip_inline_code(line: str) -> str:
    return _INLINE_CODE_RE.sub(" ", line)


def _is_remote_url(raw_target: str) -> bool:
    cleaned = raw_target.strip().strip("<>\"'")
    if cleaned.startswith("//"):
        return True
    parsed = urlsplit(cleaned)
    return parsed.scheme.lower() in {"http", "https", "ftp", "data"} and bool(
        parsed.netloc or parsed.scheme.lower() == "data"
    )


def _non_fenced_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    fence_char = ""
    fence_len = 0
    for idx, line in enumerate(text.splitlines(), start=1):
        if fence_char:
            stripped = line.lstrip(" \t")
            if stripped.startswith(fence_char * fence_len):
                closing_run = len(stripped) - len(stripped.lstrip(fence_char))
                if closing_run >= fence_len and not stripped[closing_run:].strip():
                    fence_char = ""
                    fence_len = 0
            continue
        m = _FENCE_OPEN_RE.match(line)
        if m:
            marker = m.group(1)
            info = m.group(2)
            # Backtick info strings cannot contain backticks per CommonMark.
            if marker[0] == "~" or "`" not in info:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
        result.append((idx, line))
    return result


def _vet_markdown_text(text: str, file_label: str) -> list[dict[str, Any]]:
    findings = _scan_hidden_unicode_text(text, file_label, category="prompt")
    non_fenced = _non_fenced_lines(text)

    ref_defs: dict[str, str] = {}
    for _, raw_line in non_fenced:
        ref_match = _MD_REF_DEF_RE.match(raw_line)
        if ref_match:
            ref_defs[ref_match.group(1).strip().lower()] = ref_match.group(2).strip()

    in_html_comment = False
    for line_no, raw_line in non_fenced:
        masked = _strip_inline_code(raw_line)

        if in_html_comment:
            if "-->" in masked:
                in_html_comment = False
                masked = masked.split("-->", 1)[1]
            else:
                continue

        if "<!--" in masked:
            findings.append(
                _finding(
                    file_label,
                    line_no,
                    "html-comment",
                    "high",
                    "prompt",
                    "HTML comment outside fenced code block can hide prompt instructions",
                )
            )
            after_open = masked.split("<!--", 1)[1]
            if "-->" not in after_open:
                in_html_comment = True

        for pattern, description in _PROMPT_OVERRIDE_PATTERNS:
            if pattern.search(masked):
                findings.append(
                    _finding(
                        file_label,
                        line_no,
                        "prompt-override",
                        "high",
                        "prompt",
                        description,
                    )
                )
                break

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
                        "high",
                        "prompt",
                        f"Remote Markdown image '{target}' can exfiltrate context",
                    )
                )
                remote_found = True

        if not remote_found:
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
                            "high",
                            "prompt",
                            f"Remote reference Markdown image '{resolved_url}' can exfiltrate context",
                        )
                    )

        for html_img in _HTML_IMG_RE.finditer(masked):
            src = (html_img.group(1) or html_img.group(2) or "").strip()
            if src.lower().startswith(("http://", "https://", "//")):
                findings.append(
                    _finding(
                        file_label,
                        line_no,
                        "remote-image",
                        "high",
                        "prompt",
                        f"Remote HTML image '{src}' can exfiltrate context",
                    )
                )

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
                "high",
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
            _finding(
                file_label,
                1,
                "invalid-encoding",
                "medium",
                "prompt",
                "Instruction file is not valid UTF-8",
            )
        ]
    return _vet_markdown_text(text, file_label)


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


def _inspect_mcp_server_entry(
    server_name: str,
    cfg: Mapping[str, Any],
    *,
    raw_text: str,
    file_label: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    server_line = _find_line_number(raw_text, f'"{server_name}"')

    url_val = cfg.get("url") or cfg.get("serverUrl")
    if isinstance(url_val, str) and url_val.strip():
        url_line = _find_line_number(raw_text, url_val) or server_line
        parsed = urlsplit(url_val.strip())
        if parsed.scheme.lower() == "http" and not _is_loopback_host(parsed.hostname):
            findings.append(
                _finding(
                    file_label,
                    url_line,
                    "mcp-insecure-transport",
                    "high",
                    "agent-config",
                    f"MCP server '{server_name}' uses unencrypted HTTP transport '{url_val}'",
                )
            )
        elif parsed.scheme.lower() in {
            "https",
            "ws",
            "wss",
        } and not _is_loopback_host(parsed.hostname):
            findings.append(
                _finding(
                    file_label,
                    url_line,
                    "mcp-remote-endpoint",
                    "medium",
                    "agent-config",
                    f"MCP server '{server_name}' connects to remote endpoint '{url_val}'",
                )
            )

    command = cfg.get("command")
    raw_args = cfg.get("args")
    args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
    if isinstance(command, str) and command.strip():
        cmd_line = _find_line_number(raw_text, command) or server_line
        cmd_base = Path(command.strip().replace("\\", "/")).name.lower()
        joined = " ".join([command, *args])
        if (
            cmd_base
            in {
                "sh",
                "bash",
                "zsh",
                "ksh",
                "cmd",
                "cmd.exe",
                "powershell",
                "powershell.exe",
                "pwsh",
                "pwsh.exe",
            }
            or _SHELL_DYNAMIC_EXEC_RE.search(joined)
            or _SHELL_NETWORK_RE.search(joined)
        ):
            findings.append(
                _finding(
                    file_label,
                    cmd_line,
                    "mcp-shell-execution",
                    "high",
                    "agent-config",
                    f"MCP server '{server_name}' executes shell or network command '{command}'",
                )
            )
        if cmd_base in {"npx", "npx.cmd"} and any(a in {"-y", "--yes"} for a in args):
            pkg_args = [a for a in args if not a.startswith("-")]
            if not pkg_args or any(
                not re.search(r"@\d", pkg.lstrip("@")) for pkg in pkg_args[:1]
            ):
                findings.append(
                    _finding(
                        file_label,
                        cmd_line,
                        "mcp-unpinned-package",
                        "medium",
                        "agent-config",
                        f"MCP server '{server_name}' runs 'npx -y' without an explicit pinned package version",
                    )
                )

    for map_field in ("env", "headers"):
        mapping = cfg.get(map_field)
        if not isinstance(mapping, Mapping):
            continue
        for key, val in mapping.items():
            if not isinstance(key, str) or not isinstance(val, str):
                continue
            stripped_val = val.strip()
            if not stripped_val or _ENV_PLACEHOLDER_RE.match(stripped_val):
                continue
            if _MCP_INLINE_SECRET_VALUE_RE.search(stripped_val) or (
                _MCP_SECRET_KEY_NAME_RE.search(key) and len(stripped_val) >= 8
            ):
                val_line = _find_line_number(raw_text, key) or server_line
                findings.append(
                    _finding(
                        file_label,
                        val_line,
                        "mcp-inline-secret",
                        "high",
                        "agent-config",
                        f"MCP server '{server_name}' stores a literal credential in {map_field}.{key}",
                    )
                )

    for text_field in ("description", "instructions", "prompt"):
        field_val = cfg.get(text_field)
        if isinstance(field_val, str) and field_val.strip():
            for pattern, desc in _PROMPT_OVERRIDE_PATTERNS:
                if pattern.search(field_val):
                    findings.append(
                        _finding(
                            file_label,
                            server_line,
                            "prompt-override",
                            "high",
                            "agent-config",
                            f"MCP server '{server_name}' {text_field}: {desc}",
                        )
                    )
                    break

    return findings


def vet_mcp_config(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Inspect an MCP configuration JSON file for unsafe commands, transports, or secrets."""
    file_label = _format_file_label(path, root)
    if path.is_symlink():
        return [
            _finding(
                file_label,
                1,
                "symlink-file",
                "high",
                "filesystem",
                "Symlinked MCP configuration file is not permitted",
            )
        ]
    if not path.is_file():
        raise ValueError(f"MCP configuration file not found: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            _finding(
                file_label,
                1,
                "invalid-encoding",
                "medium",
                "agent-config",
                "MCP configuration file is not valid UTF-8",
            )
        ]

    findings = _scan_hidden_unicode_text(raw_text, file_label, category="agent-config")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        findings.append(
            _finding(
                file_label,
                exc.lineno,
                "invalid-json",
                "medium",
                "agent-config",
                f"Malformed MCP JSON configuration: {exc.msg}",
            )
        )
        return findings

    if not isinstance(data, Mapping):
        findings.append(
            _finding(
                file_label,
                1,
                "invalid-mcp-config",
                "medium",
                "agent-config",
                "MCP configuration root must be a JSON object",
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


def vet_skill(
    package: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    trust_builtin_allowlists: bool = True,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Vet a standalone skill directory for risky scripts and hidden prompt payloads."""
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
                    "high",
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
                "high",
                "prompt",
                "Skill directory is missing SKILL.md",
            )
        )

    for entry in sorted(
        package.rglob("*"), key=lambda p: p.relative_to(package).as_posix()
    ):
        rel_parts = entry.relative_to(package).parts
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts):
            continue
        rel_posix = entry.relative_to(package).as_posix()
        if entry.is_symlink():
            findings.append(
                _finding(
                    rel_posix,
                    1,
                    "symlink-file",
                    "high",
                    "filesystem",
                    f"Symlink is not permitted inside skill package: {rel_posix}",
                )
            )
            continue
        if not entry.resolve().is_relative_to(package_resolved):
            findings.append(
                _finding(
                    rel_posix,
                    1,
                    "escaping-path",
                    "high",
                    "filesystem",
                    f"File escapes skill package boundary: {rel_posix}",
                )
            )
            continue
        if not entry.is_file():
            continue
        if (
            entry.suffix in _EXCLUDED_FILE_SUFFIXES
            or entry.name in _EXCLUDED_FILE_NAMES
        ):
            continue

        top_dir = rel_parts[0] if len(rel_parts) > 1 else ""
        suffix = entry.suffix.lower()
        if top_dir == "scripts" or suffix in _SCRIPT_SUFFIXES:
            scanned_files += 1
            findings.extend(vet_script(entry, effective_caps, root=package))
        elif suffix in {".md", ".mdc"}:
            scanned_files += 1
            findings.extend(vet_markdown(entry, root=package))

    return {
        "target": str(package),
        "kind": "skill",
        "skill": skill_name,
        "passed": len(findings) == 0,
        "scanned_files": scanned_files,
        "findings": findings,
    }


def _discover_host_skill_dirs(root: Path) -> list[str]:
    dirs: list[str] = list(_DEFAULT_HOST_SKILL_DIRS)
    hosts_path = root / "catalog/hosts.json"
    if hosts_path.is_file() and not hosts_path.is_symlink():
        try:
            doc = json.loads(hosts_path.read_text(encoding="utf-8"))
            hosts = doc.get("hosts", {})
            if isinstance(hosts, Mapping):
                for cfg in hosts.values():
                    if isinstance(cfg, Mapping):
                        proj = cfg.get("project_path")
                        if isinstance(proj, str) and proj.strip():
                            norm = proj.strip().strip("/")
                            if norm and norm not in dirs:
                                dirs.append(norm)
        except (OSError, json.JSONDecodeError):
            pass
    return dirs


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
                    "high",
                    "filesystem",
                    "Target path must not be a symlink",
                )
            ],
        }
    if not target.exists():
        raise FileNotFoundError(f"Target path not found: {target}")

    if target.is_file():
        name_lower = target.name.lower()
        suffix_lower = target.suffix.lower()
        if name_lower in {".mcp.json", "mcp.json", "mcp_config.json"}:
            file_findings = vet_mcp_config(target, root=target.parent)
        elif suffix_lower in _SCRIPT_SUFFIXES:
            file_findings = vet_script(target, allowed_capabilities, root=target.parent)
        else:
            file_findings = vet_markdown(target, root=target.parent)
        return {
            "target": str(target),
            "kind": "file",
            "passed": len(file_findings) == 0,
            "scanned_files": 1,
            "skills_scanned": [],
            "configs_scanned": [target.name],
            "findings": file_findings,
        }

    # If the directory itself is a standalone skill package (contains SKILL.md), vet it directly.
    skill_md = target / "SKILL.md"
    if skill_md.is_file() or skill_md.is_symlink():
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

    # Repository / project agent configuration scan.
    repo_root = target.resolve()
    findings: list[dict[str, Any]] = []
    scanned_files = 0
    skills_scanned: list[str] = []
    configs_scanned: list[str] = []

    for rel_skill_root in _discover_host_skill_dirs(root):
        host_skills_dir = repo_root / rel_skill_root
        if host_skills_dir.is_symlink():
            findings.append(
                _finding(
                    rel_skill_root,
                    1,
                    "symlink-directory",
                    "high",
                    "filesystem",
                    f"Host skill directory must not be a symlink: {rel_skill_root}",
                )
            )
            continue
        if not host_skills_dir.is_dir():
            continue
        for child in sorted(host_skills_dir.iterdir(), key=lambda p: p.name):
            rel_child = f"{rel_skill_root}/{child.name}"
            if child.is_symlink():
                findings.append(
                    _finding(
                        rel_child,
                        1,
                        "symlink-directory",
                        "high",
                        "filesystem",
                        f"Skill directory must not be a symlink: {rel_child}",
                    )
                )
                continue
            if not child.is_dir():
                continue
            child_skill_md = child / "SKILL.md"
            has_scripts = (child / "scripts").exists()
            if not (
                child_skill_md.exists() or child_skill_md.is_symlink() or has_scripts
            ):
                continue
            trusted = _is_trusted_toolkit_skill(child, root)
            sub_res = vet_skill(
                child,
                allowed_capabilities,
                trust_builtin_allowlists=trusted,
                root=root,
            )
            skills_scanned.append(rel_child)
            scanned_files += int(sub_res["scanned_files"])
            findings.extend(_prefix_skill_findings(sub_res["findings"], rel_child))

    seen_configs: set[str] = set()
    candidate_instruction_paths: list[Path] = [
        repo_root / rel for rel in _REPO_INSTRUCTION_FILES
    ]
    for pattern in _REPO_INSTRUCTION_GLOBS:
        candidate_instruction_paths.extend(sorted(repo_root.glob(pattern)))

    for inst_path in candidate_instruction_paths:
        if not inst_path.exists() and not inst_path.is_symlink():
            continue
        rel_label = _format_file_label(inst_path, repo_root)
        if rel_label in seen_configs:
            continue
        seen_configs.add(rel_label)
        configs_scanned.append(rel_label)
        scanned_files += 1
        findings.extend(vet_markdown(inst_path, root=repo_root))

    for rel_mcp in _REPO_MCP_FILES:
        mcp_path = repo_root / rel_mcp
        if not mcp_path.exists() and not mcp_path.is_symlink():
            continue
        rel_label = _format_file_label(mcp_path, repo_root)
        if rel_label in seen_configs:
            continue
        seen_configs.add(rel_label)
        configs_scanned.append(rel_label)
        scanned_files += 1
        findings.extend(vet_mcp_config(mcp_path, root=repo_root))

    return {
        "target": str(target),
        "kind": "repository",
        "passed": len(findings) == 0,
        "scanned_files": scanned_files,
        "skills_scanned": skills_scanned,
        "configs_scanned": configs_scanned,
        "findings": findings,
    }

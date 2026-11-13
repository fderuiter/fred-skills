from __future__ import annotations

import fnmatch
import hashlib
import platform
from pathlib import Path
import re
import shutil
import subprocess

from .common import digest, version

IGNORED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".fred-skills", "audit-output", "dist"}


def git(repo: Path, *args: str, required: bool = False) -> str | None:
    if not shutil.which("git"):
        if required:
            raise ValueError("Git is required for a revision/diff-based plan")
        return None
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args], capture_output=True, timeout=30)
    if result.returncode:
        if required:
            raise ValueError("Git could not resolve the requested repository or revision")
        return None
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\n")


def snapshot(repo: Path, exclude: list[str] | None = None, max_files: int = 10000, max_bytes: int = 5_000_000) -> dict:
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")
    exclude = list(exclude or [])
    listed = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed is not None:
        paths = [repo / p for p in listed.split("\0") if p]
    else:
        # Walk without descending through dependencies or symlinked directories.
        import os
        paths = []
        for base, dirs, files in os.walk(repo, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not (Path(base) / d).is_symlink())
            paths.extend(Path(base) / name for name in sorted(files))
    files, skipped = {}, {}
    for path in sorted(set(paths)):
        rel = path.relative_to(repo).as_posix()
        if any(p in IGNORED for p in Path(rel).parts) or any(fnmatch.fnmatchcase(rel, pattern) for pattern in exclude):
            continue
        if len(files) + len(skipped) >= max_files:
            skipped["<remaining>"] = "file-count-limit"
            break
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(repo):
                skipped[rel] = "symlink-or-outside-root"
            elif not path.is_file():
                skipped[rel] = "missing-or-not-regular"
            elif path.stat().st_size > max_bytes:
                skipped[rel] = "size-limit"
            else:
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            skipped[rel] = "unreadable-or-changed-during-inventory"
    return {"digest": digest({"files": files, "skipped": skipped, "exclude": exclude}),
            "files": files, "skipped": skipped, "exclude": exclude,
            "complete": not skipped, "max_files": max_files, "max_file_bytes": max_bytes}


def changed_files(repo: Path, base: str) -> tuple[list[str], str]:
    # Resolve once to an object ID before passing any user input to diff.
    sha = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}", required=True)
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha or ""):
        raise ValueError("Base did not resolve to a commit")
    head = git(repo, "rev-parse", "--verify", "HEAD", required=True)
    merge_base = git(repo, "merge-base", sha, head, required=True)
    committed = git(repo, "diff", "--no-ext-diff", "--name-only", "-z", merge_base, head, "--", required=True)
    dirty = git(repo, "diff", "--no-ext-diff", "--name-only", "-z", "HEAD", "--", required=True)
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", required=True)
    return sorted(set(p for output in [committed, dirty, untracked] for p in output.split("\0") if p)), merge_base


def preflight(repo: Path, exclude: list[str] | None = None) -> dict:
    repo = repo.resolve()
    snap = snapshot(repo, exclude)
    extensions = {Path(path).suffix.lower() for path in snap["files"]}
    languages = [name for ext, name in [(".py", "Python"), (".ts", "TypeScript"), (".tsx", "TypeScript/React"),
                 (".js", "JavaScript"), (".mjs", "JavaScript"), (".cs", "C#"), (".swift", "Swift"),
                 (".java", "Java"), (".rs", "Rust"), (".go", "Go")] if ext in extensions]
    return {"toolkit_version": version(), "python_version": platform.python_version(),
            "repository": {"path": str(repo), "revision": git(repo, "rev-parse", "HEAD") or "unversioned",
                           "dirty": bool(git(repo, "status", "--porcelain") or False)},
            "snapshot": snap, "languages": sorted(set(languages)),
            "capabilities": {"source_read": True, "git": shutil.which("git") is not None,
                "tool_binaries": {name: shutil.which(name) is not None for name in ["node", "dotnet", "swift", "semgrep", "codeql"]},
                "agent_binaries": {name: shutil.which(name) is not None for name in ["codex", "claude", "gemini"]},
                "agent_behavior_tested": False, "application_tests_executed": False,
                "github_access": "not-probed", "network_access": "not-probed"},
            "limitations": ["Binary detection is not a runtime, authentication, or behavior check.",
                            "No repository scripts, dependency installs, tests, or external requests were executed."]}

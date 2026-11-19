from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .common import FredCliError, digest, version

IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".fred-skills",
    "audit-output",
    "dist",
}


def git(repo: Path, *args: str, required: bool = False) -> str | None:
    if not shutil.which("git"):
        if required:
            raise FredCliError(
                "Git is required for a revision/diff-based plan",
                code="missing_git",
                hint="Install Git or run in a non-diff mode such as --mode investigate.",
            )
        return None
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        if required:
            raise FredCliError(
                "Git could not resolve the requested repository or revision",
                code="invalid_git_revision",
                hint="Verify the repository is a Git checkout and pass a valid commit, branch, or tag to --base.",
            )
        return None
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\n")


def snapshot(
    repo: Path,
    exclude: list[str] | None = None,
    max_files: int = 10000,
    max_bytes: int = 5_000_000,
) -> dict:
    repo = repo.resolve()
    if not repo.is_dir():
        raise FredCliError(
            f"Repository directory does not exist: {repo}",
            code="missing_repository",
            hint="Pass an existing target repository directory path.",
        )
    exclude = list(exclude or [])
    listed = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed is not None:
        paths = [repo / p for p in listed.split("\0") if p]
    else:
        # Walk without descending through dependencies or symlinked directories.
        paths = []
        for base, dirs, walk_files in os.walk(repo, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in IGNORED and not (Path(base) / d).is_symlink()
            )
            paths.extend(Path(base) / name for name in sorted(walk_files))
    files: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for path in sorted(set(paths)):
        rel = path.relative_to(repo).as_posix()
        if any(p in IGNORED for p in Path(rel).parts) or any(
            fnmatch.fnmatchcase(rel, pattern) for pattern in exclude
        ):
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
    return {
        "digest": digest({"files": files, "skipped": skipped, "exclude": exclude}),
        "files": files,
        "skipped": skipped,
        "exclude": exclude,
        "complete": not skipped,
        "max_files": max_files,
        "max_file_bytes": max_bytes,
    }


def changed_files(repo: Path, base: str) -> tuple[list[str], str]:
    # Resolve once to an object ID before passing any user input to diff.
    sha = git(
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        base + "^{commit}",
        required=True,
    )
    if sha is None or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("Base did not resolve to a commit")
    head = git(repo, "rev-parse", "--verify", "HEAD", required=True)
    if head is None:
        raise ValueError("HEAD did not resolve to a commit")
    merge_base = git(repo, "merge-base", sha, head, required=True)
    if merge_base is None:
        raise ValueError("Could not resolve merge-base")
    committed = git(
        repo,
        "diff",
        "--no-ext-diff",
        "--name-only",
        "-z",
        merge_base,
        head,
        "--",
        required=True,
    )
    dirty = git(
        repo, "diff", "--no-ext-diff", "--name-only", "-z", "HEAD", "--", required=True
    )
    untracked = git(
        repo, "ls-files", "--others", "--exclude-standard", "-z", required=True
    )
    return sorted(
        {
            p
            for output in (committed, dirty, untracked)
            if output is not None
            for p in output.split("\0")
            if p
        }
    ), merge_base


_MANIFEST_ECOSYSTEMS: dict[str, str] = {
    "package.json": "npm",
    "pyproject.toml": "python",
    "Cargo.toml": "cargo",
    "go.mod": "go",
}
_PEP508_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*(.*?)\s*$"
)
_EXACT_EQ_RE = re.compile(r"^==\s*([^,\s;]+)$")
_GO_DIRECTIVE_RE = re.compile(
    r"^(module|go|toolchain|require|replace|exclude|retract)\b"
)
_GO_REQ_ENTRY_RE = re.compile(r"^([^\s()]+)\s+(v[^\s()]+)$")


def _parse_package_json(raw_text: str) -> dict[str, str]:
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise TypeError("package.json root must be a JSON object")
    packages: dict[str, str] = {}
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        if section not in data or data[section] is None:
            continue
        section_map = data[section]
        if not isinstance(section_map, dict):
            raise TypeError(f"package.json '{section}' must be an object")
        for raw_name, raw_spec in section_map.items():
            if not isinstance(raw_name, str) or not isinstance(raw_spec, str):
                raise TypeError(
                    f"package.json '{section}' entries must map strings to strings"
                )
            name = raw_name.strip()
            spec = raw_spec.strip()
            if name and spec:
                packages.setdefault(name, spec)
    return dict(sorted(packages.items()))


def _parse_pep508_spec(entry: str) -> tuple[str, str] | None:
    head = entry.split(";", 1)[0].strip()
    if not head:
        return None
    match = _PEP508_REQ_RE.match(head)
    if not match:
        raise ValueError(f"Invalid PEP 508 dependency specifier: {entry!r}")
    name = match.group(1)
    rest = match.group(2).strip()
    if rest.startswith("@"):
        url_spec = rest[1:].strip()
        if not url_spec:
            raise ValueError(f"Incomplete PEP 508 URL specifier: {entry!r}")
        return name, url_spec
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    if not rest:
        return name, "*"
    exact = _EXACT_EQ_RE.match(rest)
    if exact:
        return name, exact.group(1)
    return name, rest


def _extract_poetry_dep_version(spec: object) -> str | None:
    if isinstance(spec, str):
        cleaned = spec.strip()
        return cleaned or None
    if isinstance(spec, dict):
        for key in ("version", "tag", "rev", "branch", "git", "path", "url"):
            val = spec.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip() if key == "version" else f"{key}:{val.strip()}"
        return "*"
    if isinstance(spec, list) and spec:
        for item in spec:
            resolved = _extract_poetry_dep_version(item)
            if resolved:
                return resolved
    return None


def _parse_pyproject_toml(raw_text: str) -> dict[str, str]:
    data = tomllib.loads(raw_text)
    packages: dict[str, str] = {}
    project = data.get("project")
    if project is not None:
        if not isinstance(project, dict):
            raise TypeError("pyproject.toml [project] must be a table")
        deps = project.get("dependencies", [])
        if not isinstance(deps, list) or any(
            not isinstance(item, str) for item in deps
        ):
            raise TypeError("pyproject.toml project.dependencies must be a string list")
        for item in deps:
            parsed = _parse_pep508_spec(item)
            if parsed is not None:
                packages.setdefault(parsed[0], parsed[1])
        opt_deps = project.get("optional-dependencies", {})
        if not isinstance(opt_deps, dict):
            raise TypeError(
                "pyproject.toml project.optional-dependencies must be a table"
            )
        for group_items in opt_deps.values():
            if not isinstance(group_items, list) or any(
                not isinstance(item, str) for item in group_items
            ):
                raise TypeError(
                    "pyproject.toml optional-dependencies groups must be string lists"
                )
            for item in group_items:
                parsed = _parse_pep508_spec(item)
                if parsed is not None:
                    packages.setdefault(parsed[0], parsed[1])

    dep_groups = data.get("dependency-groups")
    if dep_groups is not None:
        if not isinstance(dep_groups, dict):
            raise TypeError("pyproject.toml [dependency-groups] must be a table")
        for group_items in dep_groups.values():
            if not isinstance(group_items, list):
                raise TypeError("pyproject.toml dependency-groups values must be lists")
            for entry in group_items:
                if isinstance(entry, str):
                    parsed = _parse_pep508_spec(entry)
                    if parsed is not None:
                        packages.setdefault(parsed[0], parsed[1])
                elif not isinstance(entry, dict):
                    raise TypeError(
                        "pyproject.toml dependency-groups entries must be strings or tables"
                    )

    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        poetry_tables: list[dict[str, Any]] = []
        for sec in ("dependencies", "dev-dependencies"):
            sec_val = poetry.get(sec)
            if isinstance(sec_val, dict):
                poetry_tables.append(sec_val)
        groups = poetry.get("group")
        if isinstance(groups, dict):
            for grp in groups.values():
                if isinstance(grp, dict) and isinstance(grp.get("dependencies"), dict):
                    poetry_tables.append(grp["dependencies"])
        for tbl in poetry_tables:
            for raw_name, raw_spec in tbl.items():
                if raw_name == "python" or not isinstance(raw_name, str):
                    continue
                resolved = _extract_poetry_dep_version(raw_spec)
                if resolved:
                    packages.setdefault(raw_name.strip(), resolved)

    return dict(sorted(packages.items()))


def _extract_cargo_dep_entry(
    key: str, spec: object, workspace_versions: dict[str, str]
) -> tuple[str, str] | None:
    if isinstance(spec, str):
        cleaned = spec.strip()
        return (key, cleaned) if cleaned else None
    if isinstance(spec, dict):
        actual_name = (
            spec["package"].strip()
            if isinstance(spec.get("package"), str) and spec["package"].strip()
            else key
        )
        ver = spec.get("version")
        if isinstance(ver, str) and ver.strip():
            return actual_name, ver.strip()
        if spec.get("workspace") is True:
            return actual_name, workspace_versions.get(key, "workspace")
        for source_key in ("tag", "rev", "branch", "git", "path"):
            val = spec.get(source_key)
            if isinstance(val, str) and val.strip():
                return actual_name, f"{source_key}:{val.strip()}"
        return actual_name, "*"
    raise TypeError(f"Invalid Cargo dependency entry for {key!r}")


def _parse_cargo_toml(raw_text: str) -> dict[str, str]:
    data = tomllib.loads(raw_text)
    workspace_versions: dict[str, str] = {}
    packages: dict[str, str] = {}
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        ws_deps = workspace.get("dependencies")
        if ws_deps is not None:
            if not isinstance(ws_deps, dict):
                raise TypeError("Cargo.toml [workspace.dependencies] must be a table")
            for raw_name, raw_spec in ws_deps.items():
                entry = _extract_cargo_dep_entry(str(raw_name), raw_spec, {})
                if entry is not None:
                    workspace_versions[str(raw_name)] = entry[1]
                    packages.setdefault(entry[0], entry[1])

    sections: list[object] = [
        data.get("dependencies"),
        data.get("dev-dependencies"),
        data.get("build-dependencies"),
    ]
    targets = data.get("target")
    if isinstance(targets, dict):
        for target_cfg in targets.values():
            if isinstance(target_cfg, dict):
                sections.extend(
                    target_cfg.get(sec)
                    for sec in (
                        "dependencies",
                        "dev-dependencies",
                        "build-dependencies",
                    )
                )

    for sec_obj in sections:
        if sec_obj is None:
            continue
        if not isinstance(sec_obj, dict):
            raise TypeError("Cargo.toml dependency section must be a table")
        for raw_name, raw_spec in sec_obj.items():
            entry = _extract_cargo_dep_entry(
                str(raw_name), raw_spec, workspace_versions
            )
            if entry is not None:
                packages.setdefault(entry[0], entry[1])

    return dict(sorted(packages.items()))


def _parse_go_mod(raw_text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    in_block: str | None = None
    for lineno, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if in_block is not None:
            if line == ")":
                in_block = None
                continue
            if in_block == "require":
                match = _GO_REQ_ENTRY_RE.match(line)
                if not match:
                    raise ValueError(
                        f"Malformed go.mod require entry on line {lineno}: {line!r}"
                    )
                packages.setdefault(match.group(1), match.group(2))
            continue

        if not _GO_DIRECTIVE_RE.match(line):
            raise ValueError(
                f"Unrecognized go.mod directive on line {lineno}: {line!r}"
            )

        directive, _, rest = line.partition(" ")
        rest = rest.strip()
        if rest == "(" or line.endswith("(") and rest.rstrip("(").strip() == "":
            in_block = directive
            continue
        if directive == "require":
            match = _GO_REQ_ENTRY_RE.match(rest)
            if not match:
                raise ValueError(
                    f"Malformed go.mod require directive on line {lineno}: {line!r}"
                )
            packages.setdefault(match.group(1), match.group(2))

    if in_block is not None:
        raise ValueError(f"Unclosed {in_block} block in go.mod")
    return dict(sorted(packages.items()))


def _parse_manifests(repo: Path, snap_files: dict[str, str]) -> dict[str, Any]:
    manifest_paths = [
        rel_path
        for rel_path in snap_files
        if Path(rel_path).name in _MANIFEST_ECOSYSTEMS
    ]
    # Process shallower manifests first so root package pins win setdefault merges,
    # while keeping the output manifests list deterministically sorted by path.
    ordered_paths = sorted(manifest_paths, key=lambda p: (p.count("/"), p))
    parsed_by_path: dict[str, dict[str, Any]] = {}
    merged_packages: dict[str, str] = {}
    errors: dict[str, str] = {}

    parsers = {
        "package.json": _parse_package_json,
        "pyproject.toml": _parse_pyproject_toml,
        "Cargo.toml": _parse_cargo_toml,
        "go.mod": _parse_go_mod,
    }
    for rel_path in ordered_paths:
        filename = Path(rel_path).name
        ecosystem = _MANIFEST_ECOSYSTEMS[filename]
        full_path = repo / rel_path
        try:
            raw_text = full_path.read_text(encoding="utf-8")
            manifest_packages = parsers[filename](raw_text)
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            errors[rel_path] = str(exc)
            continue
        parsed_by_path[rel_path] = {
            "path": rel_path,
            "ecosystem": ecosystem,
            "packages": manifest_packages,
        }
        for pkg_name, pkg_ver in manifest_packages.items():
            merged_packages.setdefault(pkg_name, pkg_ver)

    return {
        "manifests": [parsed_by_path[p] for p in sorted(parsed_by_path)],
        "packages": dict(sorted(merged_packages.items())),
        "errors": dict(sorted(errors.items())),
    }


def preflight(repo: Path, exclude: list[str] | None = None) -> dict:
    repo = repo.resolve()
    snap = snapshot(repo, exclude)
    dependencies = _parse_manifests(repo, snap["files"])
    gh_available = shutil.which("gh") is not None
    extensions = {Path(path).suffix.lower() for path in snap["files"]}
    languages = [
        name
        for ext, name in [
            (".py", "Python"),
            (".ts", "TypeScript"),
            (".tsx", "TypeScript/React"),
            (".js", "JavaScript"),
            (".mjs", "JavaScript"),
            (".cs", "C#"),
            (".swift", "Swift"),
            (".java", "Java"),
            (".rs", "Rust"),
            (".go", "Go"),
        ]
        if ext in extensions
    ]
    limitations = [
        "Binary detection is not a runtime, authentication, or behavior check.",
        "No repository scripts, dependency installs, tests, or external requests were executed.",
    ]
    if dependencies["errors"]:
        failed = ", ".join(sorted(dependencies["errors"]))
        limitations.append(
            f"Skipped malformed dependency manifest(s) during offline preflight: {failed}."
        )
    return {
        "toolkit_version": version(),
        "python_version": platform.python_version(),
        "repository": {
            "path": str(repo),
            "revision": git(repo, "rev-parse", "HEAD") or "unversioned",
            "dirty": bool(git(repo, "status", "--porcelain") or False),
        },
        "snapshot": snap,
        "languages": sorted(set(languages)),
        "dependencies": dependencies,
        "capabilities": {
            "source_read": True,
            "git": shutil.which("git") is not None,
            "gh": gh_available,
            "tool_binaries": {
                name: shutil.which(name) is not None
                for name in ["gh", "node", "dotnet", "swift", "semgrep", "codeql"]
            },
            "agent_binaries": {
                name: shutil.which(name) is not None
                for name in ["codex", "claude", "gemini"]
            },
            "agent_behavior_tested": False,
            "application_tests_executed": False,
            "github_access": "not-probed",
            "network_access": "not-probed",
        },
        "limitations": limitations,
    }

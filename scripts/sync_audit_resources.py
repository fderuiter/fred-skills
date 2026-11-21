"""Materialize standalone skill resources, or detect drift with --check."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from skill_tools import ROOT, suite_members


def _resolve_script_source(shared: Path, root: Path, rel: Path) -> Path:
    source = shared / "resources" / rel
    if source.is_file():
        return source
    root_candidate = root / rel
    if root_candidate.is_file():
        return root_candidate
    if (ROOT / rel).is_file():
        return ROOT / rel
    return root_candidate


def _safe_relative_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip() or "\\" in raw_path:
        raise ValueError(f"Invalid shared resource path: {raw_path}")
    rel = Path(raw_path)
    if (
        rel.is_absolute()
        or raw_path.startswith("/")
        or PureWindowsPath(raw_path).is_absolute()
        or ".." in rel.parts
    ):
        raise ValueError(f"Invalid shared resource path: {raw_path}")
    return rel


def _excluded_for_skill(manifest: dict[str, object], skill_name: str) -> set[str]:
    raw_exclude = manifest.get("exclude", {})
    if not isinstance(raw_exclude, dict):
        raise TypeError(
            "Shared manifest 'exclude' must be an object mapping skill names to path lists"
        )
    skill_excludes = raw_exclude.get(skill_name, [])
    if not isinstance(skill_excludes, list):
        raise TypeError(f"Shared manifest exclusions for '{skill_name}' must be a list")
    normalized: set[str] = set()
    for item in skill_excludes:
        if not isinstance(item, str):
            raise TypeError(
                f"Invalid excluded resource entry for '{skill_name}': {item!r}"
            )
        normalized.add(_safe_relative_path(item).as_posix())
    return normalized


def _apply_exclusions(
    package: Path, skill_name: str, excluded: set[str], check: bool
) -> list[str]:
    problems: list[str] = []
    resolved_package = package.resolve()
    for excluded_rel in sorted(excluded):
        excluded_target = package / Path(excluded_rel)
        if not excluded_target.resolve().is_relative_to(resolved_package):
            raise ValueError(f"Shared exclusion escapes package: {excluded_target}")
        if not (excluded_target.is_file() or excluded_target.is_symlink()):
            continue
        if check:
            problems.append(
                f"Unexpected excluded resource in skill: {skill_name}/{excluded_rel}"
            )
        else:
            excluded_target.unlink()
    return problems


def _collect_file_targets(
    manifest: dict[str, object], shared: Path, excluded: set[str]
) -> list[tuple[str, Path]]:
    raw_files = manifest.get("files", [])
    if not isinstance(raw_files, list):
        return []
    targets: list[tuple[str, Path]] = []
    for relative in raw_files:
        if not isinstance(relative, str):
            continue
        rel = _safe_relative_path(relative)
        if rel.as_posix() not in excluded:
            targets.append((relative, shared / "resources" / rel))
    return targets


def _resolve_dict_scripts(
    scripts: Mapping[object, object],
    shared: Path,
    root: Path,
    skill_name: str,
    excluded: set[str],
) -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    for s_path, s_targets in scripts.items():
        if not (isinstance(s_path, str) and isinstance(s_targets, (list, tuple, set))):
            continue
        if skill_name not in s_targets:
            continue
        rel = _safe_relative_path(s_path)
        if rel.as_posix() not in excluded:
            targets.append((s_path, _resolve_script_source(shared, root, rel)))
    return targets


def _resolve_str_script(
    s_item: str,
    shared: Path,
    root: Path,
    package: Path,
    skill_name: str,
    excluded: set[str],
    check: bool,
    problems: list[str],
) -> tuple[str, Path] | None:
    rel = _safe_relative_path(s_item)
    if rel.as_posix() in excluded:
        return None
    if s_item != "scripts/audit_to_sarif.py":
        return (s_item, _resolve_script_source(shared, root, rel))
    if skill_name == "repo-audit-synthesis":
        return (s_item, _resolve_script_source(shared, root, rel))
    if not (package / s_item).is_file():
        return None
    if check:
        problems.append(
            f"Unexpected script in non-synthesis skill: {skill_name}/{s_item}"
        )
    else:
        (package / s_item).unlink()
    return None


def _resolve_mapping_script(
    s_item: Mapping[object, object],
    shared: Path,
    root: Path,
    skill_name: str,
    excluded: set[str],
) -> tuple[str, Path] | None:
    rel_path = s_item.get("path") or s_item.get("file")
    targets = s_item.get("targets") or s_item.get("skills", [])
    if not (
        isinstance(rel_path, str)
        and rel_path
        and isinstance(targets, (list, tuple, set))
    ):
        return None
    if skill_name not in targets:
        return None
    rel = _safe_relative_path(rel_path)
    if rel.as_posix() in excluded:
        return None
    return (rel_path, _resolve_script_source(shared, root, rel))


def _collect_script_targets(
    scripts: object,
    shared: Path,
    root: Path,
    package: Path,
    skill_name: str,
    excluded: set[str],
    check: bool,
    problems: list[str],
) -> list[tuple[str, Path]]:
    if isinstance(scripts, Mapping):
        return _resolve_dict_scripts(scripts, shared, root, skill_name, excluded)
    if not isinstance(scripts, list):
        return []
    targets: list[tuple[str, Path]] = []
    for s_item in scripts:
        resolved: tuple[str, Path] | None = None
        if isinstance(s_item, str):
            resolved = _resolve_str_script(
                s_item, shared, root, package, skill_name, excluded, check, problems
            )
        elif isinstance(s_item, Mapping):
            resolved = _resolve_mapping_script(
                s_item, shared, root, skill_name, excluded
            )
        if resolved is not None:
            targets.append(resolved)
    return targets


def _needs_exec_mode(rel: Path, expected: bytes) -> bool:
    is_script = bool(rel.parts and rel.parts[0] == "scripts")
    return is_script and expected.startswith(b"#!") and os.name != "nt"


def _sync_target_file(
    package: Path,
    skill_name: str,
    relative: str,
    source: Path,
    check: bool,
) -> str | None:
    rel = Path(relative)
    target = package / rel
    if not target.resolve().is_relative_to(package.resolve()):
        raise ValueError(f"Shared destination escapes package: {target}")
    expected = source.read_bytes()
    is_script = bool(rel.parts and rel.parts[0] == "scripts")
    if target.is_file() and target.read_bytes() == expected:
        if not (_needs_exec_mode(rel, expected) and not os.access(target, os.X_OK)):
            return None
        if check:
            return f"Shared script lacks executable mode: {skill_name}/{relative}"
        target.chmod(0o755)
        return None
    if check:
        return f"Shared resource drift: {skill_name}/{relative}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(expected)
    if is_script:
        target.chmod(0o755)
    return None


def _sync_skill_package(
    shared: Path,
    root: Path,
    package: Path,
    manifest: dict[str, object],
    skill_name: str,
    check: bool,
) -> list[str]:
    if not (package / "SKILL.md").is_file():
        return [f"Missing skill package: {skill_name}"]
    excluded = _excluded_for_skill(manifest, skill_name)
    problems = _apply_exclusions(package, skill_name, excluded, check)
    file_targets = _collect_file_targets(manifest, shared, excluded)
    file_targets.extend(
        _collect_script_targets(
            manifest.get("scripts", []),
            shared,
            root,
            package,
            skill_name,
            excluded,
            check,
            problems,
        )
    )
    for relative, source in file_targets:
        problem = _sync_target_file(package, skill_name, relative, source, check)
        if problem is not None:
            problems.append(problem)
    return problems


def _sync_manifest(
    root: Path,
    manifest_path: Path,
    members: Mapping[str, Mapping[str, object]],
    check: bool,
) -> list[str]:
    shared = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    suite = manifest.get("suite", "repository-audits")
    problems: list[str] = []
    for name, member in members.items():
        if member["suite"] != suite:
            continue
        problems.extend(
            _sync_skill_package(
                shared, root, root / "skills" / name, manifest, name, check
            )
        )
    return problems


def sync_skill_package(
    root: Path,
    package: Path,
    suite_name: str,
    skill_name: str,
    *,
    check: bool = False,
) -> list[str]:
    shared_root = root / "shared"
    if not shared_root.is_dir():
        return []
    problems: list[str] = []
    for manifest_path in sorted(shared_root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("suite", "repository-audits") == suite_name:
            problems.extend(
                _sync_skill_package(
                    manifest_path.parent, root, package, manifest, skill_name, check
                )
            )
    return problems


def sync(root: Path = ROOT, check: bool = False) -> list[str]:
    problems: list[str] = []
    members = suite_members(root)
    for manifest_path in sorted((root / "shared").glob("*/manifest.json")):
        problems.extend(_sync_manifest(root, manifest_path, members, check))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        problems = sync(check=args.check)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Resource sync failed: {exc}\n")
    for problem in problems:
        print(problem)
    if not problems:
        print("PASS: shared audit resources are consistent")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())

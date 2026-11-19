"""Materialize standalone skill resources, or detect drift with --check."""

from __future__ import annotations

import argparse
import json
import os
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


def sync(root: Path = ROOT, check: bool = False) -> list[str]:
    problems = []
    for manifest_path in sorted((root / "shared").glob("*/manifest.json")):
        shared = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        suite = manifest.get("suite", "repository-audits")
        for name, member in suite_members(root).items():
            if member["suite"] != suite:
                continue
            package = root / "skills" / name
            if not (package / "SKILL.md").is_file():
                problems.append(f"Missing skill package: {name}")
                continue
            excluded = _excluded_for_skill(manifest, name)
            for excluded_rel in sorted(excluded):
                excluded_target = package / Path(excluded_rel)
                if not excluded_target.resolve().is_relative_to(package.resolve()):
                    raise ValueError(
                        f"Shared exclusion escapes package: {excluded_target}"
                    )
                if excluded_target.is_file() or excluded_target.is_symlink():
                    if check:
                        problems.append(
                            f"Unexpected excluded resource in skill: {name}/{excluded_rel}"
                        )
                    else:
                        excluded_target.unlink()

            file_targets: list[tuple[str, Path]] = []
            for relative in manifest.get("files", []):
                rel = _safe_relative_path(relative)
                if rel.as_posix() in excluded:
                    continue
                source = shared / "resources" / rel
                file_targets.append((relative, source))

            scripts = manifest.get("scripts", [])
            if isinstance(scripts, dict):
                for s_path, s_targets in scripts.items():
                    if name in s_targets:
                        rel = _safe_relative_path(s_path)
                        if rel.as_posix() in excluded:
                            continue
                        source = _resolve_script_source(shared, root, rel)
                        file_targets.append((s_path, source))
            elif isinstance(scripts, list):
                for s_item in scripts:
                    if isinstance(s_item, str):
                        rel = _safe_relative_path(s_item)
                        if rel.as_posix() in excluded:
                            continue
                        if s_item == "scripts/audit_to_sarif.py":
                            if name == "repo-audit-synthesis":
                                source = _resolve_script_source(shared, root, rel)
                                file_targets.append((s_item, source))
                            elif (package / s_item).is_file():
                                if check:
                                    problems.append(
                                        f"Unexpected script in non-synthesis skill: {name}/{s_item}"
                                    )
                                else:
                                    (package / s_item).unlink()
                        else:
                            source = _resolve_script_source(shared, root, rel)
                            file_targets.append((s_item, source))
                    elif isinstance(s_item, dict):
                        rel_path = s_item.get("path") or s_item.get("file")
                        targets = s_item.get("targets") or s_item.get("skills", [])
                        if rel_path and name in targets:
                            rel = _safe_relative_path(rel_path)
                            if rel.as_posix() in excluded:
                                continue
                            source = _resolve_script_source(shared, root, rel)
                            file_targets.append((rel_path, source))

            for relative, source in file_targets:
                rel = Path(relative)
                target = package / rel
                if not target.resolve().is_relative_to(package.resolve()):
                    raise ValueError(f"Shared destination escapes package: {target}")
                expected = source.read_bytes()
                is_script = bool(rel.parts and rel.parts[0] == "scripts")
                needs_exec = (
                    is_script and expected.startswith(b"#!") and os.name != "nt"
                )
                if target.is_file() and target.read_bytes() == expected:
                    if needs_exec and not os.access(target, os.X_OK):
                        if check:
                            problems.append(
                                f"Shared script lacks executable mode: {name}/{relative}"
                            )
                        else:
                            target.chmod(0o755)
                    continue
                if check:
                    problems.append(f"Shared resource drift: {name}/{relative}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(expected)
                    if is_script:
                        target.chmod(0o755)
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

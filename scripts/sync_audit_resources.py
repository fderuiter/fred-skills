"""Materialize standalone skill resources, or detect drift with --check."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PureWindowsPath

from skill_tools import ROOT, suite_members

_SYNTHESIS_ONLY_SCRIPTS = frozenset(
    {
        "scripts/audit_to_sarif.py",
        "scripts/generate_issues.py",
    }
)


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


def _resolve_script_source(shared: Path, root: Path, rel: Path) -> Path:
    for candidate in (shared / "resources" / rel, root / rel, ROOT / rel):
        if candidate.is_file():
            return candidate
    return root / rel


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
            file_targets: list[tuple[str, Path]] = []
            for relative in manifest.get("files", []):
                rel = _safe_relative_path(relative)
                source = shared / "resources" / rel
                file_targets.append((relative, source))

            scripts = manifest.get("scripts", [])
            if isinstance(scripts, dict):
                for s_path, s_targets in scripts.items():
                    if name in s_targets:
                        rel = _safe_relative_path(s_path)
                        source = _resolve_script_source(shared, root, rel)
                        file_targets.append((s_path, source))
            elif isinstance(scripts, list):
                for s_item in scripts:
                    if isinstance(s_item, str):
                        rel = _safe_relative_path(s_item)
                        if s_item in _SYNTHESIS_ONLY_SCRIPTS:
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
                            source = _resolve_script_source(shared, root, rel)
                            file_targets.append((rel_path, source))

            for relative, source in file_targets:
                rel = Path(relative)
                target = package / rel
                if not target.resolve().is_relative_to(package.resolve()):
                    raise ValueError(f"Shared destination escapes package: {target}")
                expected = source.read_bytes()
                needs_exec = expected.startswith(b"#!")
                has_exec = os.name == "nt" or (
                    target.is_file() and bool(target.stat().st_mode & 0o111)
                )
                if (
                    target.is_file()
                    and target.read_bytes() == expected
                    and (not needs_exec or has_exec)
                ):
                    continue
                if check:
                    problems.append(f"Shared resource drift: {name}/{relative}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(expected)
                    if needs_exec and os.name != "nt":
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

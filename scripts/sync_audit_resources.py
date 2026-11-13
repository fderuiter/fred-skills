"""Materialize standalone skill resources, or detect drift with --check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from skill_tools import ROOT, suite_members


def sync(root: Path = ROOT, check: bool = False) -> list[str]:
    shared = root / "shared/repo-audit"
    manifest = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for name, member in suite_members(root).items():
        if member["suite"] != "repository-audits":
            continue
        package = root / "skills" / name
        if not (package / "SKILL.md").is_file():
            problems.append(f"Missing skill package: {name}")
            continue
        for relative in manifest["files"]:
            rel = Path(relative)
            if rel.is_absolute() or ".." in rel.parts or "\\" in relative:
                raise ValueError(f"Invalid shared resource path: {relative}")
            source = shared / "resources" / rel
            target = package / rel
            if not target.resolve().is_relative_to(package.resolve()):
                raise ValueError(f"Shared destination escapes package: {target}")
            expected = source.read_bytes()
            if target.is_file() and target.read_bytes() == expected:
                continue
            if check:
                problems.append(f"Shared resource drift: {name}/{relative}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected)
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

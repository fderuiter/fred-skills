#!/usr/bin/env python3
"""Package standalone skill archives and full suite with SHA-256 checksums."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from fredlib.releases import (
    _collect_skill_files,
    _get_suite_skills,
    _package_skills,
)


def get_suite_skills(root: Path = ROOT, suite_name: str | None = None) -> list[str]:
    """Retrieve the skills comprising catalog suites, or a single suite if specified."""
    return _get_suite_skills(root, suite_name=suite_name)


def collect_skill_files(package_dir: Path) -> list[tuple[str, bytes]]:
    """Return list of (relative_posix_path, file_bytes) sorted by relative path.

    Excludes authoring scripts, tests, root documents, and cache directories.
    """
    return _collect_skill_files(package_dir)


def package_skills(output_dir: Path, root: Path = ROOT) -> dict[str, str]:
    """Build individual skill archives, 1 consolidated suite archive, and checksums.sha256."""
    archive_skills = _package_skills(output_dir, root=root)
    checksums: dict[str, str] = {}

    for archive_name in sorted(archive_skills.keys()):
        archive_path = output_dir / archive_name
        checksums[archive_name] = hashlib.sha256(archive_path.read_bytes()).hexdigest()

    # Write checksums.sha256
    manifest_lines = [
        f"{checksums[name]}  {name}\n" for name in sorted(checksums.keys())
    ]
    (output_dir / "checksums.sha256").write_text(
        "".join(manifest_lines), encoding="utf-8"
    )

    return checksums


def verify_checksums(output_dir: Path) -> dict[str, bool]:
    """Verify all archives listed in checksums.sha256."""
    manifest_path = output_dir / "checksums.sha256"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing checksums file: {manifest_path}")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    results = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"Malformed checksum line: {line}")
        expected_hash, filename = parts[0], parts[1].strip()
        target_file = output_dir / filename
        if not target_file.is_file():
            raise FileNotFoundError(f"Missing archive listed in checksums: {filename}")
        actual_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(
                f"Checksum mismatch for {filename}: expected {expected_hash}, got {actual_hash}"
            )
        results[filename] = True
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        "--dist-dir",
        dest="output",
        type=Path,
        default=ROOT / "dist" / "release",
        help="Target directory for release archives and checksums (default: dist/release)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify existing archives against checksums.sha256 without re-packaging",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Package all documented packs and standalone skills into unified release",
    )
    args = parser.parse_args()

    try:
        if args.verify_only:
            results = verify_checksums(args.output)
            print(f"PASS: verified {len(results)} archives against checksums.sha256")
            return 0

        if args.all:
            from fredlib import releases

            manifest = releases.build(args.output, all_packs=True, root=args.root)
            releases.verify(args.output / "manifest.json")
            verify_checksums(args.output)
            print(
                f"PASS: packaged {len(manifest['archives'])} release archives into {args.output}"
            )
            return 0

        checksums = package_skills(args.output, root=args.root)
        verify_checksums(args.output)
        print(f"PASS: packaged {len(checksums)} archives into {args.output}")
        return 0
    except (ValueError, OSError, FileNotFoundError) as exc:
        print(f"Error packaging skills: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

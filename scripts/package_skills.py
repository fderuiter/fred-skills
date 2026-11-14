#!/usr/bin/env python3
"""Package standalone skill archives and full suite with SHA-256 checksums."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import gzip

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".github",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
EXCLUDED_FILE_NAMES = {".DS_Store", ".gitignore", ".gitattributes"}


def get_suite_skills(root: Path = ROOT) -> list[str]:
    """Retrieve the 35 skills comprising the repository-audits suite."""
    suites_path = root / "catalog" / "suites.json"
    if suites_path.is_file():
        try:
            suites_doc = json.loads(suites_path.read_text(encoding="utf-8"))
            for suite in suites_doc.get("suites", []):
                if suite.get("name") == "repository-audits":
                    skills = []
                    for phase in suite.get("phases", []):
                        skills.extend(phase.get("skills", []))
                    if skills:
                        return sorted(dict.fromkeys(skills))
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    packs_path = root / "catalog" / "packs.json"
    if packs_path.is_file():
        try:
            packs_doc = json.loads(packs_path.read_text(encoding="utf-8"))
            full_audit = packs_doc.get("packs", {}).get("full-audit", {})
            if "skills" in full_audit:
                return sorted(dict.fromkeys(full_audit["skills"]))
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    skills_dir = root / "skills"
    if skills_dir.is_dir():
        audit_dirs = sorted([
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and d.name.startswith("repo-audit-")
        ])
        if audit_dirs:
            return audit_dirs

    raise ValueError("Unable to determine audit suite skills from catalog")


def collect_skill_files(package_dir: Path) -> list[tuple[str, bytes]]:
    """Return list of (relative_posix_path, file_bytes) sorted by relative path.

    Excludes authoring scripts, tests, root documents, and cache directories.
    """
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise ValueError(f"Expected a real package directory: {package_dir}")
    files: list[tuple[str, bytes]] = []
    for path in sorted(package_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks not supported in skill packages: {path}")
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in EXCLUDED_FILE_SUFFIXES or path.name in EXCLUDED_FILE_NAMES:
            continue
        rel = path.relative_to(package_dir).as_posix()
        files.append((rel, path.read_bytes()))
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    return files


def create_tar_gz_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    """Create a deterministic, reproducible .tar.gz archive from entries: (archive_path, data)."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        with tarfile.open(mode="w", fileobj=gz, format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(entries, key=lambda x: x[0]):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def package_skills(output_dir: Path, root: Path = ROOT) -> dict[str, str]:
    """Build 35 individual skill archives, 1 consolidated suite archive, and checksums.sha256."""
    output_dir.mkdir(parents=True, exist_ok=True)
    skills = get_suite_skills(root)
    if len(skills) != 35:
        raise ValueError(f"Expected 35 skills in repository-audits suite, found {len(skills)}")

    all_entries: list[tuple[str, bytes]] = []
    checksums: dict[str, str] = {}

    for skill_name in skills:
        pkg_dir = root / "skills" / skill_name
        files = collect_skill_files(pkg_dir)
        entries = [(f"{skill_name}/{rel}", data) for rel, data in files]
        all_entries.extend(entries)

        archive_bytes = create_tar_gz_bytes(entries)
        archive_name = f"{skill_name}.tar.gz"
        archive_path = output_dir / archive_name
        archive_path.write_bytes(archive_bytes)

        digest = hashlib.sha256(archive_bytes).hexdigest()
        checksums[archive_name] = digest

    # Consolidated suite archive
    suite_bytes = create_tar_gz_bytes(all_entries)
    suite_name = "fred-skills-suite.tar.gz"
    (output_dir / suite_name).write_bytes(suite_bytes)
    checksums[suite_name] = hashlib.sha256(suite_bytes).hexdigest()

    # Write checksums.sha256
    manifest_lines = [
        f"{checksums[name]}  {name}\n"
        for name in sorted(checksums.keys())
    ]
    (output_dir / "checksums.sha256").write_text("".join(manifest_lines), encoding="utf-8")

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
            raise ValueError(f"Checksum mismatch for {filename}: expected {expected_hash}, got {actual_hash}")
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
    args = parser.parse_args()

    try:
        if args.verify_only:
            results = verify_checksums(args.output)
            print(f"PASS: verified {len(results)} archives against checksums.sha256")
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

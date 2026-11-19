from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import tarfile
import zipfile
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from .common import (
    _SKILL_SUITE_PREFIXES,
    ROOT,
    FredCliError,
    _infer_suite_name,
    _suite_skills_by_suite,
    atomic_json,
    choose_skills,
    read_json,
    read_json_object,
    relative_path,
    resolve_manifest_path,
    version,
)


def _load_documented_packs(root: Path = ROOT) -> tuple[str, ...]:
    packs_path = root / "catalog/packs.json"
    if not packs_path.is_file():
        return ()
    return tuple(read_json(packs_path).get("packs", {}).keys())


DOCUMENTED_PACKS: tuple[str, ...] = _load_documented_packs()

_EXCLUDED_DIR_NAMES = {
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
_EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
_EXCLUDED_FILE_NAMES = {".DS_Store", ".gitignore", ".gitattributes"}


def _is_executable_file(path: Path, data: bytes) -> bool:
    return (os.name != "nt" and bool(path.stat().st_mode & 0o111)) or data.startswith(
        b"#!"
    )


def _scan_skill_files(
    package_dir: Path, root: Path = ROOT
) -> tuple[list[tuple[str, bytes]], set[str]]:
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise ValueError(f"Expected a real package directory: {package_dir}")
    if not package_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Package directory escapes repository root: {package_dir}")
    files: list[tuple[str, bytes]] = []
    executable_paths: set[str] = set()
    for path in sorted(
        package_dir.rglob("*"),
        key=lambda p: p.relative_to(package_dir).as_posix(),
    ):
        if path.is_symlink():
            raise ValueError(f"Symlinks not supported in skill packages: {path}")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"File escapes repository root: {path}")
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in _EXCLUDED_FILE_SUFFIXES or path.name in _EXCLUDED_FILE_NAMES:
            continue
        rel = path.relative_to(package_dir).as_posix()
        data = path.read_bytes()
        files.append((rel, data))
        if _is_executable_file(path, data):
            executable_paths.add(rel)
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    return files, executable_paths


def _collect_skill_files(
    package_dir: Path, root: Path = ROOT
) -> list[tuple[str, bytes]]:
    files, _ = _scan_skill_files(package_dir, root=root)
    return files


def _get_suite_skills(root: Path = ROOT, suite_name: str | None = None) -> list[str]:
    by_suite = _suite_skills_by_suite(root)
    if by_suite is not None:
        if suite_name is None:
            skills = [s for members in by_suite.values() for s in members]
        else:
            skills = list(by_suite.get(suite_name, ()))
        return sorted(dict.fromkeys(skills))

    packs_path = root / "catalog" / "packs.json"
    if packs_path.is_file():
        try:
            packs_doc = read_json(packs_path)
            packs = packs_doc.get("packs", {})
            if suite_name == "repository-audits":
                pack = packs.get("full-audit", {})
            elif suite_name == "repository-workflows":
                pack = packs.get("verified-fixes", {})
            elif suite_name is not None:
                pack = packs.get(suite_name, {})
            else:
                pack = packs.get("complete", {})
            if "skills" in pack:
                return sorted(dict.fromkeys(pack["skills"]))
        except (OSError, ValueError, KeyError):
            pass

    skills_dir = root / "skills"
    if skills_dir.is_dir():
        dirs = sorted(
            [
                d.name
                for d in skills_dir.iterdir()
                if d.is_dir() and not d.is_symlink() and (d / "SKILL.md").is_file()
            ]
        )
        if suite_name is not None:
            known_suites = tuple(s for _, s in _SKILL_SUITE_PREFIXES)
            dirs = [d for d in dirs if _infer_suite_name(d, known_suites) == suite_name]
        if dirs:
            return dirs

    raise ValueError("Unable to determine suite skills from catalog")


def _write_zip_archive(
    archive_path: Path,
    entries: list[tuple[str, bytes]],
    executable_paths: Collection[str] | None = None,
) -> None:
    exec_set = set(executable_paths) if executable_paths else set()
    with zipfile.ZipFile(
        archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(entries, key=lambda x: x[0]):
            mode = 0o100755 if name in exec_set else 0o100644
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)


def _write_tar_entries(
    tar: tarfile.TarFile,
    entries: list[tuple[str, bytes]],
    executable_paths: Collection[str] | None = None,
) -> None:
    exec_set = set(executable_paths) if executable_paths else set()
    for name, data in sorted(entries, key=lambda x: x[0]):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o755 if name in exec_set else 0o644
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        tar.addfile(info, io.BytesIO(data))


def _write_tar_archive(
    archive_path: Path,
    entries: list[tuple[str, bytes]],
    executable_paths: Collection[str] | None = None,
) -> None:
    with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
        _write_tar_entries(archive, entries, executable_paths=executable_paths)


def _create_tar_gz_bytes(
    entries: list[tuple[str, bytes]],
    executable_paths: Collection[str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with (
        gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz,
        tarfile.open(mode="w", fileobj=gz, format=tarfile.PAX_FORMAT) as tar,
    ):
        _write_tar_entries(tar, entries, executable_paths=executable_paths)
    return buf.getvalue()


def _scan_skills_entries(
    skills: Iterable[str], root: Path = ROOT
) -> tuple[list[tuple[str, bytes]], set[str]]:
    entries: list[tuple[str, bytes]] = []
    executable_paths: set[str] = set()
    for s_name in sorted(skills):
        pkg = root / "skills" / s_name
        files, rel_execs = _scan_skill_files(pkg, root=root)
        for rel, data in files:
            arcname = f"{s_name}/{rel}"
            entries.append((arcname, data))
            if rel in rel_execs:
                executable_paths.add(arcname)
    return entries, executable_paths


def _collect_skills_entries(
    skills: Iterable[str], root: Path = ROOT
) -> list[tuple[str, bytes]]:
    entries, _ = _scan_skills_entries(skills, root=root)
    return entries


def _package_skills(output: Path, root: Path = ROOT) -> dict[str, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    suite_skills = _get_suite_skills(root)
    if not suite_skills:
        raise ValueError("Expected non-empty suite")

    archive_skills: dict[str, list[str]] = {}
    all_suite_entries: list[tuple[str, bytes]] = []
    all_suite_executables: set[str] = set()
    for s_name in sorted(suite_skills):
        s_entries, s_execs = _scan_skills_entries([s_name], root=root)
        all_suite_entries.extend(s_entries)
        all_suite_executables.update(s_execs)
        tar_gz_bytes = _create_tar_gz_bytes(s_entries, executable_paths=s_execs)
        archive_name = f"{s_name}.tar.gz"
        (output / archive_name).write_bytes(tar_gz_bytes)
        archive_skills[archive_name] = [s_name]

    suite_bytes = _create_tar_gz_bytes(
        all_suite_entries, executable_paths=all_suite_executables
    )
    suite_name = "fred-skills-suite.tar.gz"
    (output / suite_name).write_bytes(suite_bytes)
    archive_skills[suite_name] = suite_skills
    return archive_skills


def build(
    output: Path,
    pack: str | None = None,
    skills: list[str] | None = None,
    root: Path = ROOT,
    all_packs: bool = False,
) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)

    if all_packs or pack == "all":
        available_packs = list(_load_documented_packs(root))
        if not available_packs:
            raise ValueError("No documented packs found in catalog/packs.json")

        archive_skills: dict[str, list[str]] = {}
        packs_map: dict[str, list[str]] = {}

        for p in available_packs:
            p_skills = choose_skills(root, pack=p)
            packs_map[p] = p_skills
            p_stem = f"fred-skills-{version(root)}-{p}"
            p_entries, p_execs = _scan_skills_entries(p_skills, root=root)
            zip_name = f"{p_stem}.zip"
            tar_name = f"{p_stem}.tar"
            _write_zip_archive(output / zip_name, p_entries, executable_paths=p_execs)
            _write_tar_archive(output / tar_name, p_entries, executable_paths=p_execs)
            archive_skills[zip_name] = p_skills
            archive_skills[tar_name] = p_skills

        archive_skills.update(_package_skills(output, root=root))

        all_skills = sorted(set().union(*archive_skills.values()))
        all_archives: list[dict[str, Any]] = [
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
                "skills": archive_skills.get(path.name, []),
            }
            for path in sorted(output.iterdir())
            if path.is_file()
            and path.name not in {"manifest.json", "SHA256SUMS", "checksums.sha256"}
        ]
        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "version": version(root),
            "pack": "all",
            "packs": packs_map,
            "skills": all_skills,
            "licensing": "Owner license selection not yet declared"
            if not (root / "LICENSE").exists()
            else "See repository LICENSE",
            "archives": all_archives,
        }
        atomic_json(output / "manifest.json", manifest)
        checksum_lines = "".join(
            f"{item['sha256']}  {item['file']}\n" for item in all_archives
        )
        (output / "SHA256SUMS").write_text(
            checksum_lines, encoding="utf-8", newline="\n"
        )
        (output / "checksums.sha256").write_text(
            checksum_lines, encoding="utf-8", newline="\n"
        )
        return manifest

    selected = choose_skills(root, pack, skills)
    label = pack or "selected"
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("Invalid pack name")
    stem = f"fred-skills-{version(root)}-{label}"
    entries, execs = _scan_skills_entries(selected, root=root)
    _write_zip_archive(output / (stem + ".zip"), entries, executable_paths=execs)
    _write_tar_archive(output / (stem + ".tar"), entries, executable_paths=execs)
    pack_archives: list[dict[str, Any]] = [
        {
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "skills": list(selected),
        }
        for path in sorted(output.iterdir())
        if path.is_file()
        and path.name not in {"manifest.json", "SHA256SUMS", "checksums.sha256"}
    ]
    manifest = {
        "schema_version": "1.0",
        "version": version(root),
        "pack": label,
        "skills": selected,
        "licensing": "Owner license selection not yet declared"
        if not (root / "LICENSE").exists()
        else "See repository LICENSE",
        "archives": pack_archives,
    }
    atomic_json(output / "manifest.json", manifest)
    checksum_lines = "".join(
        f"{item['sha256']}  {item['file']}\n" for item in pack_archives
    )
    (output / "SHA256SUMS").write_text(checksum_lines, encoding="utf-8", newline="\n")
    (output / "checksums.sha256").write_text(
        checksum_lines, encoding="utf-8", newline="\n"
    )
    return manifest


def _verify_checksum_file(
    checksum_path: Path, manifest_dir: Path, expected_digests: dict[str, str]
) -> None:
    if checksum_path.is_symlink():
        raise ValueError(f"Symlink checksum file not supported: {checksum_path.name}")
    if not checksum_path.exists():
        return
    if not checksum_path.is_file() or not checksum_path.resolve().is_relative_to(
        manifest_dir
    ):
        raise ValueError(f"Unsafe checksum file: {checksum_path.name}")
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    if not lines and expected_digests:
        raise ValueError(f"Empty checksum file: {checksum_path.name}")
    seen: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(
                f"Malformed checksum line in {checksum_path.name}: {line!r}"
            )
        digest, raw_name = match.groups()
        norm_name = relative_path(raw_name).as_posix()
        if norm_name != raw_name:
            raise ValueError(
                f"Non-normalized archive path in {checksum_path.name}: {raw_name!r}"
            )
        if norm_name in seen:
            raise ValueError(
                f"Duplicate archive entry in {checksum_path.name}: {norm_name}"
            )
        if norm_name not in expected_digests:
            raise ValueError(
                f"Unexpected archive entry in {checksum_path.name}: {norm_name}"
            )
        if digest != expected_digests[norm_name]:
            raise ValueError(f"Checksum mismatch in {checksum_path.name}: {norm_name}")
        seen[norm_name] = digest
    if set(seen) != set(expected_digests):
        raise ValueError(f"Missing archive entries in {checksum_path.name}")


def verify(manifest: Path) -> dict:
    manifest_path = resolve_manifest_path(manifest)
    data = read_json_object(
        manifest_path,
        label="Release manifest",
        hint="Pass a valid 'manifest.json' or release directory generated by 'python scripts/fred.py release'.",
    )
    version_val = data.get("version")
    archives = data.get("archives")
    if not isinstance(version_val, str) or not version_val.strip():
        raise FredCliError(
            f"Malformed release manifest '{manifest_path}': missing nonempty string 'version'",
            code="invalid_manifest",
            hint="Rebuild the release bundle with 'python scripts/fred.py release --output <dir>'.",
        )
    if not isinstance(archives, list):
        raise FredCliError(
            f"Malformed release manifest '{manifest_path}': 'archives' must be a JSON array",
            code="invalid_manifest",
            hint="Ensure 'manifest.json' contains an 'archives' array of {file, sha256, bytes} entries.",
        )
    manifest_dir = manifest_path.parent.resolve()
    expected_digests: dict[str, str] = {}
    for entry in archives:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("file"), str)
            or not isinstance(entry.get("sha256"), str)
        ):
            raise FredCliError(
                f"Malformed archive entry in release manifest '{manifest_path}'",
                code="invalid_manifest",
                hint="Each entry in 'archives' must be an object with string 'file' and 'sha256' fields.",
            )
        rel = relative_path(entry["file"])
        rel_posix = rel.as_posix()
        if rel_posix in expected_digests:
            raise FredCliError(
                f"Duplicate archive entry in manifest: {entry['file']}",
                code="invalid_manifest",
                hint="Remove duplicate archive entries or rebuild 'manifest.json' with 'python scripts/fred.py release'.",
            )
        path = manifest_path.parent / rel
        if path.is_symlink() or not path.resolve().is_relative_to(manifest_dir):
            raise FredCliError(
                "Release archive escapes manifest directory",
                code="unsafe_archive_path",
                hint="Keep all release archives inside the manifest directory without symlinks.",
            )
        if not path.is_file():
            raise FredCliError(
                f"Missing release archive: {entry['file']}",
                code="missing_archive",
                hint=f"Ensure '{entry['file']}' exists alongside '{manifest_path}', or rebuild the release bundle.",
            )
        expected_bytes = entry.get("bytes")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
        ):
            raise FredCliError(
                f"Invalid byte count in manifest: {entry['file']}",
                code="invalid_manifest",
                hint="Rebuild the release bundle with 'python scripts/fred.py release --output <dir>'.",
            )
        if path.stat().st_size != expected_bytes:
            raise FredCliError(
                f"Byte size mismatch: {entry['file']}",
                code="checksum_mismatch",
                hint="Re-download or rebuild the release bundle; archive size does not match 'manifest.json'.",
            )
        raw = path.read_bytes()
        if len(raw) != expected_bytes:
            raise FredCliError(
                f"Byte size mismatch: {entry['file']}",
                code="checksum_mismatch",
                hint="Re-download or rebuild the release bundle; archive size does not match 'manifest.json'.",
            )
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise FredCliError(
                f"Checksum mismatch: {entry['file']}",
                code="checksum_mismatch",
                hint="Re-download or rebuild the release bundle; archive bytes do not match 'manifest.json'.",
            )
        expected_digests[rel_posix] = entry["sha256"]
    for checksum_name in ("SHA256SUMS", "checksums.sha256"):
        _verify_checksum_file(
            manifest_path.parent / checksum_name, manifest_dir, expected_digests
        )
    return {
        "verified_archives": len(archives),
        "version": version_val,
        "authenticity": "Checksums establish integrity against this manifest, not publisher authenticity.",
    }

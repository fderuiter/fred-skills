from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from .common import (
    ROOT,
    atomic_json,
    choose_skills,
    read_json,
    relative_path,
    version,
)

DOCUMENTED_PACKS = ("starter", "full-audit", "complete")

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


def _collect_skill_files(
    package_dir: Path, root: Path = ROOT
) -> list[tuple[str, bytes]]:
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise ValueError(f"Expected a real package directory: {package_dir}")
    if not package_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Package directory escapes repository root: {package_dir}")
    files: list[tuple[str, bytes]] = []
    for path in sorted(package_dir.rglob("*")):
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
        files.append((rel, path.read_bytes()))
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    return files


def _get_suite_skills(root: Path = ROOT, suite_name: str | None = None) -> list[str]:
    suites_path = root / "catalog" / "suites.json"
    if suites_path.is_file():
        try:
            suites_doc = read_json(suites_path)
            suites = suites_doc.get("suites", [])
            skills: list[str] = []
            matched = False
            for suite in suites:
                if suite_name is None or suite.get("name") == suite_name:
                    matched = True
                    for phase in suite.get("phases", []):
                        skills.extend(phase.get("skills", []))
            if matched or "suites" in suites_doc:
                return sorted(dict.fromkeys(skills))
        except (OSError, ValueError, KeyError):
            pass

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
        if suite_name == "repository-audits":
            dirs = [d for d in dirs if d.startswith("repo-audit-")]
        elif suite_name == "repository-workflows":
            dirs = [d for d in dirs if not d.startswith("repo-audit-")]
        if dirs:
            return dirs

    raise ValueError("Unable to determine suite skills from catalog")


def _write_zip_archive(archive_path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries, key=lambda x: x[0]):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def _write_tar_entries(tar: tarfile.TarFile, entries: list[tuple[str, bytes]]) -> None:
    for name, data in sorted(entries, key=lambda x: x[0]):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        tar.addfile(info, io.BytesIO(data))


def _write_tar_archive(archive_path: Path, entries: list[tuple[str, bytes]]) -> None:
    with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
        _write_tar_entries(archive, entries)


def _create_tar_gz_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with (
        gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz,
        tarfile.open(mode="w", fileobj=gz, format=tarfile.PAX_FORMAT) as tar,
    ):
        _write_tar_entries(tar, entries)
    return buf.getvalue()


def _collect_skills_entries(
    skills: Iterable[str], root: Path = ROOT
) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    for s_name in sorted(skills):
        pkg = root / "skills" / s_name
        for rel, data in _collect_skill_files(pkg, root=root):
            entries.append((f"{s_name}/{rel}", data))
    return entries


def _package_skills(output: Path, root: Path = ROOT) -> dict[str, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    suite_skills = _get_suite_skills(root)
    if not suite_skills:
        raise ValueError("Expected non-empty suite")

    archive_skills: dict[str, list[str]] = {}
    all_suite_entries: list[tuple[str, bytes]] = []
    for s_name in sorted(suite_skills):
        s_entries = _collect_skills_entries([s_name], root=root)
        all_suite_entries.extend(s_entries)
        tar_gz_bytes = _create_tar_gz_bytes(s_entries)
        archive_name = f"{s_name}.tar.gz"
        (output / archive_name).write_bytes(tar_gz_bytes)
        archive_skills[archive_name] = [s_name]

    suite_bytes = _create_tar_gz_bytes(all_suite_entries)
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
        packs_doc = read_json(root / "catalog/packs.json").get("packs", {})
        available_packs = [p for p in DOCUMENTED_PACKS if p in packs_doc]
        if not available_packs:
            raise ValueError("No documented packs found in catalog/packs.json")

        archive_skills: dict[str, list[str]] = {}
        packs_map: dict[str, list[str]] = {}

        for p in available_packs:
            p_skills = choose_skills(root, pack=p)
            packs_map[p] = p_skills
            p_stem = f"fred-skills-{version(root)}-{p}"
            p_entries = _collect_skills_entries(p_skills, root=root)
            zip_name = f"{p_stem}.zip"
            tar_name = f"{p_stem}.tar"
            _write_zip_archive(output / zip_name, p_entries)
            _write_tar_archive(output / tar_name, p_entries)
            archive_skills[zip_name] = p_skills
            archive_skills[tar_name] = p_skills

        archive_skills.update(_package_skills(output, root=root))

        all_skills = sorted(set().union(*archive_skills.values()))
        manifest = {
            "schema_version": "1.0",
            "version": version(root),
            "pack": "all",
            "packs": packs_map,
            "skills": all_skills,
            "licensing": "Owner license selection not yet declared"
            if not (root / "LICENSE").exists()
            else "See repository LICENSE",
            "archives": [
                {
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                    "skills": archive_skills.get(path.name, []),
                }
                for path in sorted(output.iterdir())
                if path.is_file()
                and path.name not in {"manifest.json", "SHA256SUMS", "checksums.sha256"}
            ],
        }
        atomic_json(output / "manifest.json", manifest)
        checksum_lines = "".join(
            f"{item['sha256']}  {item['file']}\n" for item in manifest["archives"]
        )
        (output / "SHA256SUMS").write_text(checksum_lines, encoding="utf-8")
        (output / "checksums.sha256").write_text(checksum_lines, encoding="utf-8")
        return manifest

    selected = choose_skills(root, pack, skills)
    label = pack or "selected"
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("Invalid pack name")
    stem = f"fred-skills-{version(root)}-{label}"
    entries = _collect_skills_entries(selected, root=root)
    _write_zip_archive(output / (stem + ".zip"), entries)
    _write_tar_archive(output / (stem + ".tar"), entries)
    manifest = {
        "schema_version": "1.0",
        "version": version(root),
        "pack": label,
        "skills": selected,
        "licensing": "Owner license selection not yet declared"
        if not (root / "LICENSE").exists()
        else "See repository LICENSE",
        "archives": [
            {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
            for path in sorted(output.iterdir())
            if path.is_file()
            and path.name not in {"manifest.json", "SHA256SUMS", "checksums.sha256"}
        ],
    }
    atomic_json(output / "manifest.json", manifest)
    checksum_lines = "".join(
        f"{item['sha256']}  {item['file']}\n" for item in manifest["archives"]
    )
    (output / "SHA256SUMS").write_text(checksum_lines, encoding="utf-8")
    (output / "checksums.sha256").write_text(checksum_lines, encoding="utf-8")
    return manifest


def verify(manifest: Path) -> dict:
    data = read_json(manifest)
    for entry in data["archives"]:
        path = manifest.parent / relative_path(entry["file"])
        if path.is_symlink() or not path.resolve().is_relative_to(
            manifest.parent.resolve()
        ):
            raise ValueError("Release archive escapes manifest directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Checksum mismatch: {entry['file']}")
    return {
        "verified_archives": len(data["archives"]),
        "version": data["version"],
        "authenticity": "Checksums establish integrity against this manifest, not publisher authenticity.",
    }

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import zipfile
from collections.abc import Callable, Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

from . import vetting
from .common import (
    _SKILL_SUITE_PREFIXES,
    ROOT,
    FredCliError,
    _infer_suite_name,
    _is_ignored_skill_entry,
    _is_skill_script_rel,
    _suite_skills_by_suite,
    _validate_allowed_skill_file,
    atomic_json,
    choose_skills,
    read_json_object,
    relative_path,
    resolve_manifest_path,
    version,
)


def _load_documented_packs(root: Path = ROOT) -> tuple[str, ...]:
    packs_path = root / "catalog/packs.json"
    try:
        packs_doc = read_json_object(
            packs_path, label="Packs catalog 'catalog/packs.json'"
        )
    except FredCliError as exc:
        if exc.code == "missing_file":
            return ()
        raise
    packs = packs_doc.get("packs")
    if not isinstance(packs, dict):
        raise FredCliError(
            f"Malformed packs catalog '{packs_path}': expected 'packs' object",
            code="invalid_catalog",
            hint=f"Ensure '{packs_path}' contains a JSON object under 'packs'.",
        )
    return tuple(packs.keys())


DOCUMENTED_PACKS: tuple[str, ...] = _load_documented_packs()


def _is_executable_file(path: Path, data: bytes) -> bool:
    return (os.name != "nt" and bool(path.stat().st_mode & 0o111)) or data.startswith(
        b"#!"
    )


def _validate_skill_package_dir(package_dir: Path, root: Path) -> None:
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise ValueError(f"Expected a real package directory: {package_dir}")
    if not package_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Package directory escapes repository root: {package_dir}")


def _inspect_skill_package_entry(
    path: Path, package_dir: Path, root: Path
) -> tuple[str, bytes, bool] | None:
    if path.is_symlink():
        raise ValueError(f"Symlinks not supported in skill packages: {path}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"File escapes repository root: {path}")
    if not path.is_file():
        return None
    rel_path = path.relative_to(package_dir)
    if _is_ignored_skill_entry(rel_path.parts, path.name, path.suffix):
        return None
    rel = rel_path.as_posix()
    data = path.read_bytes()
    _validate_allowed_skill_file(rel, data)
    is_exec = _is_skill_script_rel(rel) and _is_executable_file(path, data)
    return rel, data, is_exec


def _vet_scanned_skill(package_dir: Path, root: Path) -> None:
    vet_res = vetting.vet_skill(package_dir, trust_builtin_allowlists=True, root=root)
    if not vet_res["passed"]:
        first = vet_res["findings"][0]
        raise ValueError(
            f"Security vetting failed for skill '{package_dir.name}' "
            f"({first['file']}:{first['line']} [{first['rule']}]): {first['message']}"
        )


def _scan_skill_files(
    package_dir: Path, root: Path = ROOT
) -> tuple[list[tuple[str, bytes]], set[str]]:
    _validate_skill_package_dir(package_dir, root)
    files: list[tuple[str, bytes]] = []
    executable_paths: set[str] = set()
    for path in sorted(
        package_dir.rglob("*"),
        key=lambda p: p.relative_to(package_dir).as_posix(),
    ):
        inspected = _inspect_skill_package_entry(path, package_dir, root)
        if inspected is None:
            continue
        rel, data, is_exec = inspected
        files.append((rel, data))
        if is_exec:
            executable_paths.add(rel)
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    _vet_scanned_skill(package_dir, root)
    return files, executable_paths


def _collect_skill_files(
    package_dir: Path, root: Path = ROOT
) -> list[tuple[str, bytes]]:
    files, _ = _scan_skill_files(package_dir, root=root)
    return files


def _suite_skills_from_suites_catalog(
    root: Path, suite_name: str | None
) -> list[str] | None:
    by_suite = _suite_skills_by_suite(root)
    if by_suite is None:
        return None
    if suite_name is None:
        skills = [s for members in by_suite.values() for s in members]
    else:
        skills = list(by_suite.get(suite_name, ()))
    return sorted(dict.fromkeys(skills))


def _select_pack_for_suite(packs: dict[str, Any], suite_name: str | None) -> Any:
    if suite_name == "repository-audits":
        return packs.get("full-audit")
    if suite_name == "repository-workflows":
        return packs.get("verified-fixes")
    if suite_name is not None:
        return packs.get(suite_name)
    return packs.get("complete")


def _extract_pack_skill_names(pack: Any, packs_path: Path) -> list[str]:
    if not isinstance(pack, dict) or not isinstance(pack.get("skills"), list):
        raise FredCliError(
            f"Malformed pack entry in '{packs_path}': expected object with 'skills' array",
            code="invalid_catalog",
            hint=f"Ensure pack entries in '{packs_path}' define a 'skills' array.",
        )
    skills_list: list[str] = []
    for item in pack["skills"]:
        if not isinstance(item, str) or not item:
            raise FredCliError(
                f"Malformed pack entry in '{packs_path}': skill identifiers must be nonempty strings",
                code="invalid_catalog",
                hint=f"Ensure all entries in 'skills' inside '{packs_path}' are strings.",
            )
        skills_list.append(item)
    return sorted(dict.fromkeys(skills_list))


def _suite_skills_from_packs_catalog(
    root: Path, suite_name: str | None
) -> list[str] | None:
    packs_path = root / "catalog" / "packs.json"
    try:
        packs_doc = read_json_object(
            packs_path, label="Packs catalog 'catalog/packs.json'"
        )
    except FredCliError as exc:
        if exc.code == "missing_file":
            return None
        raise
    packs = packs_doc.get("packs")
    if not isinstance(packs, dict):
        raise FredCliError(
            f"Malformed packs catalog '{packs_path}': expected 'packs' object",
            code="invalid_catalog",
            hint=f"Ensure '{packs_path}' contains a JSON object under 'packs'.",
        )
    pack = _select_pack_for_suite(packs, suite_name)
    if pack is None:
        return None
    return _extract_pack_skill_names(pack, packs_path)


def _is_valid_skill_directory(entry: Path) -> bool:
    return entry.is_dir() and not entry.is_symlink() and (entry / "SKILL.md").is_file()


def _suite_skills_from_filesystem(
    root: Path, suite_name: str | None
) -> list[str] | None:
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return None
    dirs = sorted(d.name for d in skills_dir.iterdir() if _is_valid_skill_directory(d))
    if suite_name is not None:
        known_suites = tuple(s for _, s in _SKILL_SUITE_PREFIXES)
        dirs = [d for d in dirs if _infer_suite_name(d, known_suites) == suite_name]
    return dirs or None


def _get_suite_skills(root: Path = ROOT, suite_name: str | None = None) -> list[str]:
    for resolver in (
        _suite_skills_from_suites_catalog,
        _suite_skills_from_packs_catalog,
        _suite_skills_from_filesystem,
    ):
        resolved = resolver(root, suite_name)
        if resolved is not None:
            return resolved
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
            is_exec = name in exec_set and name.endswith(".py")
            mode = 0o100755 if is_exec else 0o100644
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
        is_exec = name in exec_set and name.endswith(".py")
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = 0o755 if is_exec else 0o644
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


def _root_license_entries(root: Path = ROOT) -> list[tuple[str, bytes]]:
    license_path = root / "LICENSE"
    if license_path.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {license_path}")
    if not license_path.exists():
        return []
    if not license_path.is_file() or not license_path.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError(f"Unsafe LICENSE file: {license_path}")
    return [("LICENSE", license_path.read_bytes())]


def _read_checked_repo_file(path: Path, base_root: Path) -> bytes:
    if path.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {path}")
    if not path.is_file() or not path.resolve().is_relative_to(base_root.resolve()):
        raise ValueError(f"Expected safe file inside repository root: {path}")
    return path.read_bytes()


def _gemini_manifest_bytes(
    suite_skills: Sequence[str],
    root: Path = ROOT,
) -> bytes:
    ext_path = root / "gemini-extension.json"
    if ext_path.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {ext_path}")
    if ext_path.exists():
        return _read_checked_repo_file(ext_path, root)
    payload = {
        "name": "fred-skills",
        "description": (
            f"Standalone agent skills ({len(suite_skills)} skills) for repository "
            "auditing, software architecture, code review, specification design, "
            "test engineering, and verified workflows."
        ),
        "version": version(root),
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _build_gemini_bundles(
    output: Path,
    suite_skills: list[str],
    license_entries: list[tuple[str, bytes]],
    root: Path = ROOT,
) -> dict[str, list[str]]:
    raw_entries, raw_execs = _scan_skills_entries(suite_skills, root=root)
    entries: list[tuple[str, bytes]] = [
        ("gemini-extension.json", _gemini_manifest_bytes(suite_skills, root=root)),
        *license_entries,
        *((f"skills/{arcname}", data) for arcname, data in raw_entries),
    ]
    execs = {f"skills/{arcname}" for arcname in raw_execs}
    tar_gz_bytes = _create_tar_gz_bytes(entries, executable_paths=execs)
    for gz_name in ("darwin.fred-skills.tar.gz", "linux.fred-skills.tar.gz"):
        (output / gz_name).write_bytes(tar_gz_bytes)
    _write_zip_archive(
        output / "win32.fred-skills.zip", entries, executable_paths=execs
    )
    return {
        "darwin.fred-skills.tar.gz": list(suite_skills),
        "linux.fred-skills.tar.gz": list(suite_skills),
        "win32.fred-skills.zip": list(suite_skills),
    }


def _collect_cli_catalog_entries(root: Path) -> list[tuple[str, bytes]]:
    catalog_root = root if (root / "catalog").is_dir() else ROOT
    catalog_dir = catalog_root / "catalog"
    if catalog_dir.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {catalog_dir}")
    if not catalog_dir.is_dir():
        return []
    return [
        (f"catalog/{cat_path.name}", _read_checked_repo_file(cat_path, catalog_root))
        for cat_path in sorted(catalog_dir.glob("*.json"), key=lambda p: p.name)
    ]


_CLI_EXECUTABLE_SCRIPTS: tuple[str, ...] = (
    "scripts/fred.py",
    "scripts/audit_to_sarif.py",
    "scripts/validate_architecture.py",
    "scripts/validate_audit.py",
    "scripts/validate_review.py",
    "scripts/validate_spec.py",
    "scripts/validate_testing.py",
    "scripts/validate_workflow.py",
)
_CLI_LIBRARY_SCRIPTS: tuple[str, ...] = (
    "scripts/skill_tools.py",
    "scripts/_markdown_utils.py",
)
_CLI_IGNORED_NAMES: frozenset[str] = frozenset({"__pycache__", ".DS_Store"})
_CLI_IGNORED_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo"})


def _collect_cli_script_entries(
    root: Path,
) -> tuple[list[tuple[str, bytes]], set[str]]:
    scripts_root = (
        root
        if (root / "scripts/fred.py").exists()
        or (root / "scripts/fred.py").is_symlink()
        else ROOT
    )
    entries = [
        (rel_script, _read_checked_repo_file(scripts_root / rel_script, scripts_root))
        for rel_script in (*_CLI_EXECUTABLE_SCRIPTS, *_CLI_LIBRARY_SCRIPTS)
    ]
    fredlib_dir = scripts_root / "scripts/fredlib"
    if fredlib_dir.is_symlink() or not fredlib_dir.is_dir():
        raise ValueError(f"Expected scripts/fredlib directory: {fredlib_dir}")
    for py_path in sorted(fredlib_dir.glob("*.py"), key=lambda p: p.name):
        entries.append(
            (
                f"scripts/fredlib/{py_path.name}",
                _read_checked_repo_file(py_path, scripts_root),
            )
        )
    return entries, set(_CLI_EXECUTABLE_SCRIPTS)


def _walk_cli_directory(
    cur_dir: Path,
    base_root: Path,
    entries: list[tuple[str, bytes]],
    execs: set[str],
) -> None:
    for child in sorted(cur_dir.iterdir(), key=lambda p: p.name):
        if child.is_symlink():
            raise ValueError(f"Symlinks not supported in release packages: {child}")
        if child.name in _CLI_IGNORED_NAMES or child.suffix in _CLI_IGNORED_SUFFIXES:
            continue
        if child.is_dir():
            _walk_cli_directory(child, base_root, entries, execs)
            continue
        data = _read_checked_repo_file(child, base_root)
        arcname = child.relative_to(base_root).as_posix()
        entries.append((arcname, data))
        if arcname.endswith(".py") and (
            data.startswith(b"#!") or "/scripts/" in arcname
        ):
            execs.add(arcname)


def _collect_cli_tree_entries(
    root: Path, rel_dir: str
) -> tuple[list[tuple[str, bytes]], set[str]]:
    candidate = root / rel_dir
    base_root = root if candidate.exists() or candidate.is_symlink() else ROOT
    target_dir = base_root / rel_dir
    if target_dir.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {target_dir}")
    if not target_dir.is_dir():
        raise ValueError(f"Expected {rel_dir} directory: {target_dir}")
    entries: list[tuple[str, bytes]] = []
    execs: set[str] = set()
    _walk_cli_directory(target_dir, base_root, entries, execs)
    return entries, execs


def _collect_cli_skills_entries(
    root: Path, suite_skills: Sequence[str] | None = None
) -> tuple[list[tuple[str, bytes]], set[str]]:
    candidate = root / "skills"
    skills_root = root if candidate.exists() or candidate.is_symlink() else ROOT
    skills_dir = skills_root / "skills"
    if skills_dir.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {skills_dir}")
    names = (
        list(suite_skills)
        if suite_skills is not None and skills_root == root
        else _get_suite_skills(skills_root)
    )
    raw_entries, raw_execs = _scan_skills_entries(names, root=skills_root)
    return (
        [(f"skills/{arcname}", data) for arcname, data in raw_entries],
        {f"skills/{arcname}" for arcname in raw_execs},
    )


def _build_cli_bundles(
    output: Path,
    license_entries: list[tuple[str, bytes]],
    root: Path = ROOT,
    suite_skills: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    ver = version(root)
    version_path = root / "VERSION"
    if version_path.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {version_path}")
    version_bytes = (
        _read_checked_repo_file(version_path, root)
        if version_path.exists()
        else f"{ver}\n".encode()
    )
    script_entries, cli_execs = _collect_cli_script_entries(root)
    entries: list[tuple[str, bytes]] = [
        *license_entries,
        ("VERSION", version_bytes),
        *_collect_cli_catalog_entries(root),
        *script_entries,
    ]
    for rel_dir in ("schemas", "shared", "evals"):
        dir_entries, dir_execs = _collect_cli_tree_entries(root, rel_dir)
        entries.extend(dir_entries)
        cli_execs.update(dir_execs)
    skill_entries, skill_execs = _collect_cli_skills_entries(
        root, suite_skills=suite_skills
    )
    entries.extend(skill_entries)
    cli_execs.update(skill_execs)
    stem = f"fred-skills-{ver}-cli"
    tar_gz_name = f"{stem}.tar.gz"
    zip_name = f"{stem}.zip"
    (output / tar_gz_name).write_bytes(
        _create_tar_gz_bytes(entries, executable_paths=cli_execs)
    )
    _write_zip_archive(output / zip_name, entries, executable_paths=cli_execs)
    return {tar_gz_name: [], zip_name: []}


def _package_skills(output: Path, root: Path = ROOT) -> dict[str, list[str]]:
    output.mkdir(parents=True, exist_ok=True)
    suite_skills = _get_suite_skills(root)
    if not suite_skills:
        raise ValueError("Expected non-empty suite")
    license_entries = _root_license_entries(root)

    archive_skills: dict[str, list[str]] = {}
    all_suite_entries: list[tuple[str, bytes]] = []
    all_suite_executables: set[str] = set()
    for s_name in sorted(suite_skills):
        s_entries, s_execs = _scan_skills_entries([s_name], root=root)
        all_suite_entries.extend(s_entries)
        all_suite_executables.update(s_execs)
        tar_gz_bytes = _create_tar_gz_bytes(
            [*license_entries, *s_entries], executable_paths=s_execs
        )
        archive_name = f"{s_name}.tar.gz"
        (output / archive_name).write_bytes(tar_gz_bytes)
        archive_skills[archive_name] = [s_name]

    suite_bytes = _create_tar_gz_bytes(
        [*license_entries, *all_suite_entries], executable_paths=all_suite_executables
    )
    suite_name = "fred-skills-suite.tar.gz"
    (output / suite_name).write_bytes(suite_bytes)
    archive_skills[suite_name] = suite_skills
    return archive_skills


_RELEASE_META_FILES: frozenset[str] = frozenset(
    {"manifest.json", "SHA256SUMS", "checksums.sha256"}
)


def _is_release_archive_file(path: Path) -> bool:
    return path.is_file() and path.name not in _RELEASE_META_FILES


def _collect_output_archives(
    output: Path, skills_for_file: Callable[[str], list[str]]
) -> list[dict[str, Any]]:
    return [
        {
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "skills": skills_for_file(path.name),
        }
        for path in sorted(output.iterdir())
        if _is_release_archive_file(path)
    ]


def _write_manifest_and_checksums(
    output: Path, manifest: dict[str, Any], archives: list[dict[str, Any]]
) -> dict[str, Any]:
    atomic_json(output / "manifest.json", manifest)
    checksum_lines = "".join(f"{item['sha256']}  {item['file']}\n" for item in archives)
    (output / "SHA256SUMS").write_text(checksum_lines, encoding="utf-8", newline="\n")
    (output / "checksums.sha256").write_text(
        checksum_lines, encoding="utf-8", newline="\n"
    )
    return manifest


def _licensing_statement(root: Path) -> str:
    if not (root / "LICENSE").exists():
        return "Owner license selection not yet declared"
    return "See repository LICENSE"


def _build_all_packs_release(
    output: Path,
    license_entries: list[tuple[str, bytes]],
    root: Path,
) -> dict[str, Any]:
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
        bundled_pack_entries = [*license_entries, *p_entries]
        zip_name = f"{p_stem}.zip"
        tar_name = f"{p_stem}.tar"
        _write_zip_archive(
            output / zip_name, bundled_pack_entries, executable_paths=p_execs
        )
        _write_tar_archive(
            output / tar_name, bundled_pack_entries, executable_paths=p_execs
        )
        archive_skills[zip_name] = p_skills
        archive_skills[tar_name] = p_skills

    packaged = _package_skills(output, root=root)
    archive_skills.update(packaged)
    suite_skills = packaged.get("fred-skills-suite.tar.gz") or _get_suite_skills(root)
    archive_skills.update(
        _build_gemini_bundles(output, suite_skills, license_entries, root=root)
    )
    archive_skills.update(
        _build_cli_bundles(
            output, license_entries, root=root, suite_skills=suite_skills
        )
    )

    all_skills = sorted(set().union(*archive_skills.values()))
    all_archives = _collect_output_archives(
        output, lambda fname: archive_skills.get(fname, [])
    )
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "version": version(root),
        "pack": "all",
        "packs": packs_map,
        "skills": all_skills,
        "licensing": _licensing_statement(root),
        "archives": all_archives,
    }
    return _write_manifest_and_checksums(output, manifest, all_archives)


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
    license_entries = _root_license_entries(root)

    if all_packs or pack == "all":
        return _build_all_packs_release(output, license_entries, root)

    selected = choose_skills(root, pack, skills)
    label = pack or "selected"
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("Invalid pack name")
    stem = f"fred-skills-{version(root)}-{label}"
    entries, execs = _scan_skills_entries(selected, root=root)
    bundled_entries = [*license_entries, *entries]
    _write_zip_archive(
        output / (stem + ".zip"), bundled_entries, executable_paths=execs
    )
    _write_tar_archive(
        output / (stem + ".tar"), bundled_entries, executable_paths=execs
    )
    pack_archives = _collect_output_archives(output, lambda _fname: list(selected))
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "version": version(root),
        "pack": label,
        "skills": selected,
        "licensing": _licensing_statement(root),
        "archives": pack_archives,
    }
    return _write_manifest_and_checksums(output, manifest, pack_archives)


def _read_safe_checksum_lines(
    checksum_path: Path, manifest_dir: Path, expected_digests: dict[str, str]
) -> list[str] | None:
    if checksum_path.is_symlink():
        raise FredCliError(
            f"Symlink checksum file not supported: {checksum_path.name}",
            code="unsafe_checksum_file",
            hint="Keep checksum files as regular files inside the release directory.",
        )
    if not checksum_path.exists():
        return None
    if not checksum_path.is_file() or not checksum_path.resolve().is_relative_to(
        manifest_dir
    ):
        raise FredCliError(
            f"Unsafe checksum file: {checksum_path.name}",
            code="unsafe_checksum_file",
            hint="Keep checksum files inside the release directory without symlinks.",
        )
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    if not lines and expected_digests:
        raise FredCliError(
            f"Empty checksum file: {checksum_path.name}",
            code="invalid_checksum_file",
            hint=f"Rebuild or re-download '{checksum_path.name}'.",
        )
    return lines


def _verify_checksum_line(
    line: str,
    checksum_name: str,
    expected_digests: dict[str, str],
    seen: dict[str, str],
) -> tuple[str, str]:
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
    if not match:
        raise FredCliError(
            f"Malformed checksum line in {checksum_name}: {line!r}",
            code="invalid_checksum_file",
            hint=f"Ensure '{checksum_name}' uses '<sha256>  <filename>' lines.",
        )
    digest, raw_name = match.groups()
    norm_name = relative_path(raw_name).as_posix()
    if norm_name != raw_name:
        raise FredCliError(
            f"Non-normalized archive path in {checksum_name}: {raw_name!r}",
            code="invalid_checksum_file",
            hint=f"Ensure archive filenames in '{checksum_name}' are normalized relative paths.",
        )
    if norm_name in seen:
        raise FredCliError(
            f"Duplicate archive entry in {checksum_name}: {norm_name}",
            code="invalid_checksum_file",
            hint=f"Remove duplicate entries from '{checksum_name}'.",
        )
    if norm_name not in expected_digests:
        raise FredCliError(
            f"Unexpected archive entry in {checksum_name}: {norm_name}",
            code="invalid_checksum_file",
            hint=f"Ensure '{checksum_name}' matches 'manifest.json'.",
        )
    if digest != expected_digests[norm_name]:
        raise FredCliError(
            f"Checksum mismatch in {checksum_name}: {norm_name}",
            code="checksum_mismatch",
            hint=f"Re-download '{checksum_name}' and 'manifest.json' from the same release.",
        )
    return norm_name, digest


def _verify_checksum_file(
    checksum_path: Path, manifest_dir: Path, expected_digests: dict[str, str]
) -> None:
    lines = _read_safe_checksum_lines(checksum_path, manifest_dir, expected_digests)
    if lines is None:
        return
    seen: dict[str, str] = {}
    for line in lines:
        norm_name, digest = _verify_checksum_line(
            line, checksum_path.name, expected_digests, seen
        )
        seen[norm_name] = digest
    if set(seen) != set(expected_digests):
        raise FredCliError(
            f"Missing archive entries in {checksum_path.name}",
            code="invalid_checksum_file",
            hint=f"Ensure '{checksum_path.name}' lists every archive in 'manifest.json'.",
        )


def _normalize_archive_selector(item: str | Path, manifest_dir: Path) -> str:
    raw_text = str(item).strip()
    if not raw_text:
        raise FredCliError(
            "Archive selector cannot be empty",
            code="invalid_archive_selector",
            hint="Pass a valid archive filename from 'manifest.json' to '--archive'.",
        )
    candidate = Path(raw_text)
    if candidate.is_absolute() or candidate.exists():
        resolved = candidate.resolve()
        if resolved.is_relative_to(manifest_dir) and resolved != manifest_dir:
            return resolved.relative_to(manifest_dir).as_posix()
    return relative_path(raw_text).as_posix()


def _validate_manifest_version_and_license(
    data: dict[str, Any],
    manifest_path: Path,
    expected_version: str | None,
    require_licensing: bool,
) -> str:
    version_val = data.get("version")
    if not isinstance(version_val, str) or not version_val.strip():
        raise FredCliError(
            f"Malformed release manifest '{manifest_path}': missing nonempty string 'version'",
            code="invalid_manifest",
            hint="Rebuild the release bundle with 'python scripts/fred.py release --output <dir>'.",
        )
    if expected_version is not None:
        norm_expected = expected_version.strip()
        if not norm_expected or version_val != norm_expected:
            raise FredCliError(
                f"Manifest version '{version_val}' does not match VERSION '{norm_expected}'",
                code="version_mismatch",
                hint="Ensure the release bundle matches the expected repository VERSION.",
            )
    if require_licensing and data.get("licensing") != "See repository LICENSE":
        raise FredCliError(
            f"Release manifest licensing '{data.get('licensing')}' does not report repository LICENSE",
            code="invalid_licensing",
            hint="Rebuild the release bundle from a repository checkout containing a root LICENSE file.",
        )
    return version_val


def _validate_manifest_header(
    data: dict[str, Any],
    manifest_path: Path,
    expected_version: str | None,
    require_licensing: bool,
) -> tuple[str, list[Any]]:
    if ("schema_version" in data or require_licensing) and (
        data.get("schema_version") != "1.0"
    ):
        raise FredCliError(
            f"Unsupported manifest schema_version in '{manifest_path}': {data.get('schema_version')!r}",
            code="invalid_manifest",
            hint="Ensure 'manifest.json' declares '\"schema_version\": \"1.0\"'.",
        )
    version_val = _validate_manifest_version_and_license(
        data, manifest_path, expected_version, require_licensing
    )
    archives = data.get("archives")
    if not isinstance(archives, list):
        raise FredCliError(
            f"Malformed release manifest '{manifest_path}': 'archives' must be a JSON array",
            code="invalid_manifest",
            hint="Ensure 'manifest.json' contains an 'archives' array of {file, sha256, bytes} entries.",
        )
    return version_val, archives


def _resolve_requested_archive_names(
    archive: str | Path | Sequence[str | Path] | None, manifest_dir: Path
) -> set[str] | None:
    if archive is None:
        return None
    raw_selectors = [archive] if isinstance(archive, str | Path) else list(archive)
    if not raw_selectors:
        raise FredCliError(
            "No archives matched '--archive' selector",
            code="no_matching_archives",
            hint="Pass at least one archive filename listed in 'manifest.json'.",
        )
    return {_normalize_archive_selector(item, manifest_dir) for item in raw_selectors}


def _is_valid_archive_byte_count(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _is_manifest_archive_dict(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return isinstance(entry.get("file"), str) and isinstance(entry.get("sha256"), str)


def _parse_manifest_archive_entry(
    entry: Any,
    manifest_path: Path,
    manifest_dir: Path,
    seen_digests: dict[str, str],
) -> tuple[str, str, dict[str, Any], Path]:
    if not _is_manifest_archive_dict(entry):
        raise FredCliError(
            f"Malformed archive entry in release manifest '{manifest_path}'",
            code="invalid_manifest",
            hint="Each entry in 'archives' must be an object with string 'file' and 'sha256' fields.",
        )
    rel = relative_path(entry["file"])
    rel_posix = rel.as_posix()
    if rel_posix in seen_digests:
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
    if not _is_valid_archive_byte_count(entry.get("bytes")):
        raise FredCliError(
            f"Invalid byte count in manifest: {entry['file']}",
            code="invalid_manifest",
            hint="Rebuild the release bundle with 'python scripts/fred.py release --output <dir>'.",
        )
    return rel_posix, entry["sha256"], entry, path


def _collect_manifest_entries(
    archives: Sequence[Any], manifest_path: Path, manifest_dir: Path
) -> tuple[dict[str, str], dict[str, tuple[dict[str, Any], Path]]]:
    expected_digests: dict[str, str] = {}
    manifest_entries: dict[str, tuple[dict[str, Any], Path]] = {}
    for entry in archives:
        rel_posix, digest, parsed_entry, path = _parse_manifest_archive_entry(
            entry, manifest_path, manifest_dir, expected_digests
        )
        expected_digests[rel_posix] = digest
        manifest_entries[rel_posix] = (parsed_entry, path)
    return expected_digests, manifest_entries


def _select_candidate_archives(
    manifest_entries: dict[str, tuple[dict[str, Any], Path]],
    requested_names: set[str] | None,
) -> list[str]:
    if requested_names is None:
        return list(manifest_entries.keys())
    unknown = sorted(requested_names - set(manifest_entries))
    if unknown:
        raise FredCliError(
            f"Archive not listed in release manifest: {', '.join(unknown)}",
            code="no_matching_archives",
            hint="Specify an archive filename listed in 'manifest.json'.",
        )
    return [name for name in manifest_entries if name in requested_names]


def _verify_single_archive_file(
    entry: dict[str, Any],
    path: Path,
    manifest_path: Path,
    ignore_missing: bool,
) -> bool:
    if not path.is_file():
        if ignore_missing:
            return False
        raise FredCliError(
            f"Missing release archive: {entry['file']}",
            code="missing_archive",
            hint=f"Ensure '{entry['file']}' exists alongside '{manifest_path}', or pass '--archive' / '--ignore-missing' for partial downloads.",
        )
    expected_bytes = entry["bytes"]
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
    return True


def verify(
    manifest: Path,
    *,
    archive: str | Path | Sequence[str | Path] | None = None,
    ignore_missing: bool = False,
    expected_version: str | None = None,
    require_licensing: bool = False,
) -> dict:
    manifest_path = resolve_manifest_path(manifest)
    data = read_json_object(
        manifest_path,
        label="Release manifest",
        hint="Pass a valid 'manifest.json' or release directory generated by 'python scripts/fred.py release'.",
    )
    version_val, archives = _validate_manifest_header(
        data, manifest_path, expected_version, require_licensing
    )
    manifest_dir = manifest_path.parent.resolve()
    requested_names = _resolve_requested_archive_names(archive, manifest_dir)
    expected_digests, manifest_entries = _collect_manifest_entries(
        archives, manifest_path, manifest_dir
    )
    candidate_names = _select_candidate_archives(manifest_entries, requested_names)

    verified_count = sum(
        1
        for name in candidate_names
        if _verify_single_archive_file(
            manifest_entries[name][0],
            manifest_entries[name][1],
            manifest_path,
            ignore_missing,
        )
    )

    for checksum_name in ("SHA256SUMS", "checksums.sha256"):
        _verify_checksum_file(
            manifest_path.parent / checksum_name, manifest_dir, expected_digests
        )

    if verified_count == 0:
        raise FredCliError(
            f"No release archives matched on disk for manifest '{manifest_path}'",
            code="no_matching_archives",
            hint="Download at least one release archive listed in 'manifest.json' before running verification.",
        )

    return {
        "verified_archives": verified_count,
        "version": version_val,
        "authenticity": "Checksums establish integrity against this manifest, not publisher authenticity.",
    }

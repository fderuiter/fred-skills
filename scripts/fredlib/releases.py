from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import zipfile
from collections.abc import Collection, Iterable, Sequence
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
        rel_path = path.relative_to(package_dir)
        if _is_ignored_skill_entry(rel_path.parts, path.name, path.suffix):
            continue
        rel = rel_path.as_posix()
        data = path.read_bytes()
        _validate_allowed_skill_file(rel, data)
        files.append((rel, data))
        if _is_skill_script_rel(rel) and _is_executable_file(path, data):
            executable_paths.add(rel)
    if not files:
        raise ValueError(f"No files found in skill package: {package_dir}")
    vet_res = vetting.vet_skill(package_dir, trust_builtin_allowlists=True, root=root)
    if not vet_res["passed"]:
        first = vet_res["findings"][0]
        raise ValueError(
            f"Security vetting failed for skill '{package_dir.name}' "
            f"({first['file']}:{first['line']} [{first['rule']}]): {first['message']}"
        )
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
    try:
        packs_doc = read_json_object(
            packs_path, label="Packs catalog 'catalog/packs.json'"
        )
    except FredCliError as exc:
        if exc.code != "missing_file":
            raise
    else:
        packs = packs_doc.get("packs")
        if not isinstance(packs, dict):
            raise FredCliError(
                f"Malformed packs catalog '{packs_path}': expected 'packs' object",
                code="invalid_catalog",
                hint=f"Ensure '{packs_path}' contains a JSON object under 'packs'.",
            )
        if suite_name == "repository-audits":
            pack = packs.get("full-audit")
        elif suite_name == "repository-workflows":
            pack = packs.get("verified-fixes")
        elif suite_name is not None:
            pack = packs.get(suite_name)
        else:
            pack = packs.get("complete")
        if pack is not None:
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


def _build_cli_bundles(
    output: Path,
    license_entries: list[tuple[str, bytes]],
    root: Path = ROOT,
) -> dict[str, list[str]]:
    ver = version(root)
    entries: list[tuple[str, bytes]] = list(license_entries)
    version_path = root / "VERSION"
    if version_path.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {version_path}")
    version_bytes = (
        _read_checked_repo_file(version_path, root)
        if version_path.exists()
        else f"{ver}\n".encode()
    )
    entries.append(("VERSION", version_bytes))

    catalog_root = root if (root / "catalog").is_dir() else ROOT
    catalog_dir = catalog_root / "catalog"
    if catalog_dir.is_symlink():
        raise ValueError(f"Symlinks not supported in release packages: {catalog_dir}")
    if catalog_dir.is_dir():
        for cat_path in sorted(catalog_dir.glob("*.json"), key=lambda p: p.name):
            entries.append(
                (
                    f"catalog/{cat_path.name}",
                    _read_checked_repo_file(cat_path, catalog_root),
                )
            )

    scripts_root = (
        root
        if (root / "scripts/fred.py").exists()
        or (root / "scripts/fred.py").is_symlink()
        else ROOT
    )
    for rel_script in (
        "scripts/fred.py",
        "scripts/skill_tools.py",
        "scripts/_markdown_utils.py",
        "scripts/audit_to_sarif.py",
    ):
        entries.append(
            (
                rel_script,
                _read_checked_repo_file(scripts_root / rel_script, scripts_root),
            )
        )

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

    cli_execs = {"scripts/fred.py", "scripts/audit_to_sarif.py"}
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
        suite_skills = packaged.get("fred-skills-suite.tar.gz") or _get_suite_skills(
            root
        )
        archive_skills.update(
            _build_gemini_bundles(output, suite_skills, license_entries, root=root)
        )
        archive_skills.update(_build_cli_bundles(output, license_entries, root=root))

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
    bundled_entries = [*license_entries, *entries]
    _write_zip_archive(
        output / (stem + ".zip"), bundled_entries, executable_paths=execs
    )
    _write_tar_archive(
        output / (stem + ".tar"), bundled_entries, executable_paths=execs
    )
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
        raise FredCliError(
            f"Symlink checksum file not supported: {checksum_path.name}",
            code="unsafe_checksum_file",
            hint="Keep checksum files as regular files inside the release directory.",
        )
    if not checksum_path.exists():
        return
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
    seen: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise FredCliError(
                f"Malformed checksum line in {checksum_path.name}: {line!r}",
                code="invalid_checksum_file",
                hint=f"Ensure '{checksum_path.name}' uses '<sha256>  <filename>' lines.",
            )
        digest, raw_name = match.groups()
        norm_name = relative_path(raw_name).as_posix()
        if norm_name != raw_name:
            raise FredCliError(
                f"Non-normalized archive path in {checksum_path.name}: {raw_name!r}",
                code="invalid_checksum_file",
                hint=f"Ensure archive filenames in '{checksum_path.name}' are normalized relative paths.",
            )
        if norm_name in seen:
            raise FredCliError(
                f"Duplicate archive entry in {checksum_path.name}: {norm_name}",
                code="invalid_checksum_file",
                hint=f"Remove duplicate entries from '{checksum_path.name}'.",
            )
        if norm_name not in expected_digests:
            raise FredCliError(
                f"Unexpected archive entry in {checksum_path.name}: {norm_name}",
                code="invalid_checksum_file",
                hint=f"Ensure '{checksum_path.name}' matches 'manifest.json'.",
            )
        if digest != expected_digests[norm_name]:
            raise FredCliError(
                f"Checksum mismatch in {checksum_path.name}: {norm_name}",
                code="checksum_mismatch",
                hint=f"Re-download '{checksum_path.name}' and 'manifest.json' from the same release.",
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
    if ("schema_version" in data or require_licensing) and (
        data.get("schema_version") != "1.0"
    ):
        raise FredCliError(
            f"Unsupported manifest schema_version in '{manifest_path}': {data.get('schema_version')!r}",
            code="invalid_manifest",
            hint="Ensure 'manifest.json' declares '\"schema_version\": \"1.0\"'.",
        )
    version_val = data.get("version")
    archives = data.get("archives")
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
    if not isinstance(archives, list):
        raise FredCliError(
            f"Malformed release manifest '{manifest_path}': 'archives' must be a JSON array",
            code="invalid_manifest",
            hint="Ensure 'manifest.json' contains an 'archives' array of {file, sha256, bytes} entries.",
        )

    manifest_dir = manifest_path.parent.resolve()
    requested_names: set[str] | None = None
    if archive is not None:
        raw_selectors = [archive] if isinstance(archive, str | Path) else list(archive)
        if not raw_selectors:
            raise FredCliError(
                "No archives matched '--archive' selector",
                code="no_matching_archives",
                hint="Pass at least one archive filename listed in 'manifest.json'.",
            )
        requested_names = {
            _normalize_archive_selector(item, manifest_dir) for item in raw_selectors
        }

    expected_digests: dict[str, str] = {}
    manifest_entries: dict[str, tuple[dict[str, Any], Path]] = {}
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
        expected_digests[rel_posix] = entry["sha256"]
        manifest_entries[rel_posix] = (entry, path)

    if requested_names is not None:
        unknown = sorted(requested_names - set(manifest_entries))
        if unknown:
            raise FredCliError(
                f"Archive not listed in release manifest: {', '.join(unknown)}",
                code="no_matching_archives",
                hint="Specify an archive filename listed in 'manifest.json'.",
            )
        candidate_names = [name for name in manifest_entries if name in requested_names]
    else:
        candidate_names = list(manifest_entries.keys())

    verified_count = 0
    for name in candidate_names:
        entry, path = manifest_entries[name]
        if not path.is_file():
            if ignore_missing:
                continue
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
        verified_count += 1

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

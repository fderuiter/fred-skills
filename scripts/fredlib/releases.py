from __future__ import annotations

import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

from .common import ROOT, atomic_json, choose_skills, package_files, read_json, relative_path, version


def build(output: Path, pack: str | None = None, skills: list[str] | None = None, root: Path = ROOT) -> dict:
    selected = choose_skills(root, pack, skills)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Release output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    label = pack or "selected"
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("Invalid pack name")
    stem = f"fred-skills-{version(root)}-{label}"
    entries = []
    for name in sorted(selected):
        package = root / "skills" / name
        for relative in package_files(package):
            entries.append((name + "/" + relative, (package / relative).read_bytes()))
    with zipfile.ZipFile(output / (stem + ".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    with tarfile.open(output / (stem + ".tar"), "w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    manifest = {"schema_version": "1.0", "version": version(root), "pack": label, "skills": selected,
                "licensing": "Owner license selection not yet declared" if not (root / "LICENSE").exists() else "See repository LICENSE",
                "archives": [{"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
                             for path in sorted(output.iterdir()) if path.is_file()]}
    atomic_json(output / "manifest.json", manifest)
    (output / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['file']}\n" for item in manifest["archives"]), encoding="utf-8")
    return manifest


def verify(manifest: Path) -> dict:
    data = read_json(manifest)
    for entry in data["archives"]:
        path = manifest.parent / relative_path(entry["file"])
        if path.is_symlink() or not path.resolve().is_relative_to(manifest.parent.resolve()):
            raise ValueError("Release archive escapes manifest directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Checksum mismatch: {entry['file']}")
    return {"verified_archives": len(data["archives"]), "version": data["version"], "authenticity": "Checksums establish integrity against this manifest, not publisher authenticity."}

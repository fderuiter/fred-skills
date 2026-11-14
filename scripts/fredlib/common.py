from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".fred-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(data) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def relative_path(value: str) -> Path:
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Expected a nonempty repository-relative POSIX path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"..", ".git"} for part in pure.parts)
    ):
        raise ValueError(f"Unsafe relative path: {value!r}")
    return Path(*pure.parts)


def package_files(package: Path) -> dict[str, str]:
    """Identity by bytes and relative names; timestamps are intentionally irrelevant."""
    if package.is_symlink() or not package.is_dir():
        raise ValueError(f"Expected a real package directory: {package}")
    result = {}
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported in packages: {path}")
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        result[path.relative_to(package).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result


def version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def choose_skills(
    root: Path = ROOT, pack: str | None = None, skills: list[str] | None = None
) -> list[str]:
    catalog = {
        item["name"] for item in read_json(root / "catalog/skills.json")["skills"]
    }
    selected = list(skills or [])
    if pack:
        packs = read_json(root / "catalog/packs.json")["packs"]
        if pack not in packs:
            raise ValueError(f"Unknown pack {pack!r}; use fred.py packs")
        selected.extend(packs[pack]["skills"])
    if not selected:
        raise ValueError("Select a pack or at least one --skill")
    selected = list(dict.fromkeys(selected))
    unknown = set(selected) - catalog
    if unknown:
        raise ValueError(f"Unknown skills: {sorted(unknown)}")
    return selected

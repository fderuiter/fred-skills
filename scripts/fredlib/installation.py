from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from .common import (
    ROOT,
    atomic_json,
    choose_skills,
    digest,
    package_files,
    read_json,
    relative_path,
    version,
)


def _receipt_identifies_host(
    destination: Path, host_name: str, scope: str | None = None
) -> bool:
    state_root = destination / ".fred-skills"
    if not state_root.is_dir():
        return False
    if scope and (state_root / f"{host_name}-{scope}" / "receipt.json").is_file():
        return True
    if any(state_root.glob(f"{host_name}*/receipt.json")):
        return True
    if (state_root / host_name / "receipt.json").is_file():
        return True
    for json_file in state_root.rglob("receipt*.json"):
        if json_file.is_file():
            try:
                data = read_json(json_file)
            except (OSError, ValueError, KeyError):
                continue
            if isinstance(data, dict):
                if data.get("host") == host_name:
                    return True
                for entry in data.get("history", []):
                    if isinstance(entry, dict) and entry.get("host") == host_name:
                        return True
    return False


def detect_host(destination: Path, scope: str = "project") -> str:
    dest = destination.resolve()
    marker_map = {
        ".claude": "claude-code",
        ".agents": "codex",
        ".gemini": "gemini-cli",
    }
    found = [m for m in marker_map if (dest / m).exists()]
    if not found:
        raise ValueError(
            f"No host markers (.claude, .agents, .gemini) found in destination '{destination}'; please specify --host"
        )
    if len(found) > 1:
        markers_str = ", ".join(sorted(found))
        raise ValueError(
            f"Multiple host markers found ({markers_str}) in destination '{destination}'; please specify --host"
        )

    marker = found[0]
    if marker == ".claude":
        return "claude-code"
    if marker == ".gemini":
        return "gemini-cli"

    has_antigravity = _receipt_identifies_host(dest, "antigravity", scope)
    has_codex = _receipt_identifies_host(dest, "codex", scope)
    if has_antigravity and has_codex:
        raise ValueError(
            f"Ambiguous host receipts for both codex and antigravity found in destination '{destination}'; please specify --host"
        )
    if has_antigravity:
        return "antigravity"
    return "codex"


def locations(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
):
    if host is None:
        host = detect_host(destination, scope)
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    if host not in hosts or scope not in {"project", "user"}:
        raise ValueError("Unknown host or installation scope")
    destination = destination.resolve()
    target = destination / relative_path(hosts[host][scope + "_path"])
    if not target.resolve().is_relative_to(destination):
        raise ValueError(
            "Host destination escapes the explicitly selected installation root"
        )
    state = destination / ".fred-skills" / (host + "-" + scope)
    if not state.resolve().is_relative_to(destination):
        raise ValueError("Installation state escapes the destination")
    return target, state


@contextmanager
def lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    path = state / "operation.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            "Another install operation or interrupted operation holds the lock; inspect operation.json before recovery"
        ) from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def receipt(state: Path) -> dict:
    path = state / "receipt.json"
    return (
        read_json(path)
        if path.exists()
        else {"schema_version": "1.0", "skills": {}, "history": []}
    )


def checked_target(target: Path, name: str) -> Path:
    candidate = target / relative_path(name)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(
        target.resolve()
    ):
        raise ValueError(f"Unsafe installed package path: {name}")
    return candidate


def require_unmodified(path: Path, record: dict | None):
    if path.exists():
        if not record:
            raise ValueError(f"Refusing to replace unmanaged skill: {path.name}")
        if digest(package_files(path)) != record["content_hash"]:
            raise ValueError(
                f"Locally modified skill: {path.name}; preserve or move it before updating/removing"
            )
    elif record:
        raise ValueError(
            f"Managed skill is missing: {path.name}; inspect the receipt before proceeding"
        )


def install(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    pack: str | None = None,
    skills: list[str] | None = None,
    update: bool = False,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict:
    if host is None:
        host = detect_host(destination, scope)
    selected = choose_skills(root, pack, skills)
    target, state = locations(destination, host, scope, root)
    current = receipt(state)
    for name in selected:
        source = root / "skills" / name
        package_files(source)  # reject source symlinks before any writes
        path = checked_target(target, name)
        require_unmodified(path, current["skills"].get(name))
        if path.exists() and not update:
            raise ValueError(
                f"Already installed: {name}; use update after reviewing the source changes"
            )
    preview = {
        "action": "update" if update else "install",
        "skills": selected,
        "host": host,
        "scope": scope,
        "target": str(target),
        "dry_run": dry_run,
        "source_version": version(root),
        "runtime_behavior_verified": False,
    }
    if dry_run:
        return preview
    with lock(state):
        # Recheck after taking the lock; preflight alone is not a concurrency guard.
        current = receipt(state)
        for name in selected:
            require_unmodified(
                checked_target(target, name), current["skills"].get(name)
            )
            if (target / name).exists() and not update:
                raise ValueError(f"Already installed: {name}; use update")
        operation = uuid.uuid4().hex
        staged = state / "staging" / operation
        backups = state / "backups" / operation
        staged.mkdir(parents=True)
        backups.mkdir(parents=True)
        before = dict(current["skills"])
        new_records = dict(before)
        moved, installed = [], []
        for name in selected:
            shutil.copytree(
                root / "skills" / name,
                staged / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        journal = {
            "id": operation,
            "action": preview["action"],
            "skills": selected,
            "before": before,
            "after_hashes": {
                name: digest(package_files(staged / name)) for name in selected
            },
            "backups": str(backups),
            "staging": str(staged),
            "phase": "prepared",
        }
        atomic_json(state / "operation.json", journal)
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in selected:
                path = checked_target(target, name)
                if path.exists():
                    os.replace(path, backups / name)
                    moved.append(name)
                os.replace(staged / name, path)
                installed.append(name)
                files = package_files(path)
                new_records[name] = {
                    "source_version": version(root),
                    "files": files,
                    "content_hash": digest(files),
                }
            current["host"] = host
            current["skills"] = new_records
            current["history"].append(
                {
                    "id": operation,
                    "action": preview["action"],
                    "skills": selected,
                    "before": before,
                    "after": new_records,
                    "backups": str(backups),
                }
            )
            atomic_json(state / "receipt.json", current)
        except BaseException:
            for name in reversed(installed):
                shutil.rmtree(target / name)
            for name in reversed(moved):
                os.replace(backups / name, target / name)
            raise
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        journal["phase"] = "committed"
        atomic_json(state / "operation.json", journal)
    return preview


def remove(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    skills: list[str] | None = None,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict:
    if host is None:
        host = detect_host(destination, scope)
    skills = list(dict.fromkeys(skills or []))
    target, state = locations(destination, host, scope, root)
    if dry_run:
        current = receipt(state)
        for name in skills:
            if name not in current["skills"]:
                raise ValueError(f"Not managed by this installation: {name}")
            require_unmodified(checked_target(target, name), current["skills"][name])
        return {"action": "remove", "skills": skills, "dry_run": True}
    with lock(state):
        current = receipt(state)
        for name in skills:
            if name not in current["skills"]:
                raise ValueError(f"Not managed by this installation: {name}")
            require_unmodified(checked_target(target, name), current["skills"][name])
        preview = {"action": "remove", "skills": skills, "dry_run": dry_run}
        before = dict(current["skills"])
        backups = state / "backups" / uuid.uuid4().hex
        backups.mkdir(parents=True)
        moved = []
        try:
            for name in skills:
                os.replace(target / name, backups / name)
                moved.append(name)
                del current["skills"][name]
            current["history"].append(
                {
                    "action": "remove",
                    "skills": skills,
                    "before": before,
                    "after": dict(current["skills"]),
                    "backups": str(backups),
                }
            )
            atomic_json(state / "receipt.json", current)
        except BaseException:
            for name in reversed(moved):
                os.replace(backups / name, target / name)
            raise
        return preview


def rollback(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> dict:
    if host is None:
        host = detect_host(destination, scope)
    target, state = locations(destination, host, scope, root)
    with lock(state):
        current = receipt(state)
        if not current["history"]:
            raise ValueError("No managed operation to roll back")
        action = current["history"][-1]
        if current["skills"] != action["after"]:
            raise ValueError(
                "Receipt changed since the operation; automatic rollback is unsafe"
            )
        backups = Path(action["backups"])
        if not backups.resolve().is_relative_to((state / "backups").resolve()):
            raise ValueError("Backup path escapes installation state")
        for name in action["skills"]:
            path = checked_target(target, name)
            require_unmodified(path, current["skills"].get(name))
            if name in action["before"]:
                saved = backups / name
                if (
                    not saved.is_dir()
                    or digest(package_files(saved))
                    != action["before"][name]["content_hash"]
                ):
                    raise ValueError(f"Missing or modified rollback backup: {name}")
        rollback_backup = state / "backups" / ("rollback-" + uuid.uuid4().hex)
        rollback_backup.mkdir(parents=True)
        moved, restored = [], []
        try:
            for name in action["skills"]:
                path = target / name
                if path.exists():
                    os.replace(path, rollback_backup / name)
                    moved.append(name)
                if name in action["before"]:
                    os.replace(backups / name, path)
                    restored.append(name)
            current["skills"] = action["before"]
            current["history"].pop()
            atomic_json(state / "receipt.json", current)
        except BaseException:
            for name in reversed(restored):
                os.replace(target / name, backups / name)
            for name in reversed(moved):
                os.replace(rollback_backup / name, target / name)
            raise
        return {
            "action": "rollback",
            "skills": action["skills"],
            "runtime_behavior_verified": False,
        }


def recover(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    confirm_interrupted: bool = False,
    root: Path = ROOT,
) -> dict:
    """Recover a prepared install after the user confirms its writer has stopped."""
    if not confirm_interrupted:
        raise ValueError(
            "Recovery requires --confirm-interrupted after verifying the previous writer has stopped"
        )
    if host is None:
        host = detect_host(destination, scope)
    target, state = locations(destination, host, scope, root)
    journal = read_json(state / "operation.json")
    current = receipt(state)
    if journal.get("phase") == "committed" or any(
        item.get("id") == journal["id"] for item in current["history"]
    ):
        for name in journal["skills"]:
            require_unmodified(
                checked_target(target, name), current["skills"].get(name)
            )
        (state / "operation.lock").unlink(missing_ok=True)
        return {"recovery": "already-committed", "skills": journal["skills"]}
    if journal.get("phase") != "prepared" or current["skills"] != journal["before"]:
        raise ValueError(
            "Journal/receipt disagreement needs manual inspection; no recovery changes made"
        )
    backup = Path(journal["backups"])
    if not backup.resolve().is_relative_to((state / "backups").resolve()):
        raise ValueError("Recovery backup escapes installation state")
    for name in journal["skills"]:
        path = checked_target(target, name)
        before = journal["before"].get(name)
        allowed = {journal["after_hashes"][name]}
        if before:
            allowed.add(before["content_hash"])
        if path.exists() and digest(package_files(path)) not in allowed:
            raise ValueError(
                f"Modified recovery destination: {name}; no recovery changes made"
            )
        if (
            before
            and (
                not path.exists()
                or digest(package_files(path)) != before["content_hash"]
            )
            and (
                not (backup / name).is_dir()
                or digest(package_files(backup / name)) != before["content_hash"]
            )
        ):
            raise ValueError(f"Required recovery backup is missing or modified: {name}")
    preserved = state / "backups" / ("recovered-" + uuid.uuid4().hex)
    preserved.mkdir(parents=True)
    target.mkdir(parents=True, exist_ok=True)
    for name in journal["skills"]:
        path = target / name
        before = journal["before"].get(name)
        if (
            before
            and path.exists()
            and digest(package_files(path)) == before["content_hash"]
        ):
            continue
        if path.exists():
            os.replace(path, preserved / name)
        if before:
            os.replace(backup / name, path)
    journal["phase"] = "recovered"
    atomic_json(state / "operation.json", journal)
    (state / "operation.lock").unlink(missing_ok=True)
    return {
        "recovery": "restored-before-state",
        "skills": journal["skills"],
        "preserved_interrupted_content": str(preserved),
    }

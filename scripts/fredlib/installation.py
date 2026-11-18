from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass
class TargetEnvironment:
    """Target workspace environment for skill lifecycle actions."""

    destination: Path | None = None
    host: str | None = None
    scope: str = "project"
    root: Path = ROOT

    def __post_init__(self) -> None:
        if self.destination is not None and not isinstance(self.destination, Path):
            self.destination = Path(self.destination)
        if not isinstance(self.root, Path):
            self.root = Path(self.root)


@dataclass
class InstallOptions:
    """Execution options for skill installation and updates."""

    pack: str | None = None
    skills: list[str] | None = None
    update: bool = False
    dry_run: bool = False
    target: TargetEnvironment | None = None


@dataclass
class RemoveOptions:
    """Execution options for skill removal."""

    skills: list[str] | None = None
    dry_run: bool = False
    target: TargetEnvironment | None = None


@dataclass
class RollbackOptions:
    """Execution options for rollback."""

    target: TargetEnvironment | None = None


@dataclass
class RecoverOptions:
    """Execution options for interrupted installation recovery."""

    confirm_interrupted: bool = False
    target: TargetEnvironment | None = None


# Backward-compatible aliases
TargetScope = TargetEnvironment
TargetOptions = TargetEnvironment
RecoveryOptions = RecoverOptions


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _arg_at(args: tuple[Any, ...], index: int, default: Any = None) -> Any:
    return args[index] if index < len(args) else default


def _ensure_path(val: Path | str | None) -> Path | None:
    if val is None or isinstance(val, Path):
        return val
    return Path(val)


def _merge_target_env(
    base: TargetEnvironment,
    dest: Any = None,
    host: Any = None,
    *,
    scope: Any = None,
    root: Any = None,
) -> TargetEnvironment:
    return TargetEnvironment(
        destination=_coalesce(_ensure_path(dest), base.destination),
        host=_coalesce(host, base.host),
        scope=_coalesce(scope, base.scope, "project"),
        root=_coalesce(root, base.root, ROOT),
    )


def _merge_install_options(
    base: InstallOptions,
    pack: Any = None,
    skills: Any = None,
    *,
    update: Any = None,
    dry_run: Any = None,
    target: TargetEnvironment | None = None,
) -> InstallOptions:
    return InstallOptions(
        pack=_coalesce(pack, base.pack),
        skills=_coalesce(skills, base.skills),
        update=_coalesce(update, base.update, False),
        dry_run=_coalesce(dry_run, base.dry_run, False),
        target=_coalesce(target, base.target),
    )


def _receipt_data_matches_host(data: Any, host_name: str) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("host") == host_name:
        return True
    return any(
        isinstance(entry, dict) and entry.get("host") == host_name
        for entry in data.get("history", [])
    )


def _receipt_file_matches_host(path: Path, host_name: str) -> bool:
    if not path.is_file():
        return False
    try:
        data = read_json(path)
    except (OSError, ValueError, KeyError):
        return False
    return _receipt_data_matches_host(data, host_name)


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
    return any(
        _receipt_file_matches_host(json_file, host_name)
        for json_file in state_root.rglob("receipt*.json")
    )


def _host_marker_entries(
    hosts: dict[str, Any], scope: str
) -> list[tuple[str, str, str]]:
    key = "user_markers" if scope == "user" else "project_markers"
    entries: list[tuple[str, str, str]] = []
    for host_name, host_cfg in hosts.items():
        for marker in host_cfg.get(key, []):
            entries.append((marker["path"], marker["tier"], host_name))
    return entries


def _matching_tier_markers(
    dest: Path, entries: list[tuple[str, str, str]], tier: str
) -> dict[str, list[str]]:
    matched: dict[str, list[str]] = {}
    for marker_path, marker_tier, host_name in entries:
        if marker_tier == tier and (dest / relative_path(marker_path)).exists():
            matched.setdefault(marker_path, []).append(host_name)
    return matched


def _conflicting_fallback_receipt(
    dest: Path, scope: str, hosts: dict[str, Any], allowed_hosts: set[str]
) -> bool:
    return any(
        host_name not in allowed_hosts
        and _receipt_identifies_host(dest, host_name, scope)
        for host_name in hosts
    )


def _select_active_markers(
    dest: Path,
    scope: str,
    hosts: dict[str, Any],
    entries: list[tuple[str, str, str]],
) -> dict[str, list[str]]:
    primary = _matching_tier_markers(dest, entries, "primary")
    fallback = _matching_tier_markers(dest, entries, "fallback")
    if primary:
        active = dict(primary)
        for marker_path, marker_hosts in fallback.items():
            if any(_receipt_identifies_host(dest, h, scope) for h in marker_hosts):
                active[marker_path] = marker_hosts
        return active
    if not fallback:
        return {}
    fallback_hosts = {h for hs in fallback.values() for h in hs}
    if _conflicting_fallback_receipt(dest, scope, hosts, fallback_hosts):
        raise ValueError(
            f"Conflicting host receipt found alongside fallback marker in destination '{dest}'; please specify --host"
        )
    return fallback


def _resolve_marker_host(dest: Path, scope: str, candidates: list[str]) -> str:
    if len(candidates) == 1:
        return candidates[0]
    receipt_hosts = [h for h in candidates if _receipt_identifies_host(dest, h, scope)]
    if len(receipt_hosts) > 1:
        hosts_label = " and ".join(receipt_hosts)
        raise ValueError(
            f"Ambiguous host receipts for both {hosts_label} found in destination '{dest}'; please specify --host"
        )
    return receipt_hosts[0] if receipt_hosts else candidates[0]


def _check_host_markers(
    dest: Path, scope: str = "project", root: Path = ROOT
) -> list[str]:
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    entries = _host_marker_entries(hosts, scope)
    active = _select_active_markers(dest, scope, hosts, entries)
    if not active:
        configured = list(dict.fromkeys(path for path, _, _ in entries))
        markers_str = ", ".join(configured)
        raise ValueError(
            f"No host markers ({markers_str}) found in destination '{dest}'; please specify --host"
        )
    host_groups = {tuple(hs) for hs in active.values()}
    if len(host_groups) > 1:
        markers_str = ", ".join(sorted(active))
        raise ValueError(
            f"Multiple host markers found ({markers_str}) in destination '{dest}'; please specify --host"
        )
    return sorted(active)


def _detect_host_with_root(
    destination: Path | None, scope: str, root: Path = ROOT
) -> str:
    if scope not in {"project", "user"}:
        raise ValueError("Unknown installation scope")
    dest = (
        _default_destination(scope).resolve()
        if destination is None
        else Path(destination).resolve()
    )
    found = _check_host_markers(dest, scope, root)
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    entries = _host_marker_entries(hosts, scope)
    candidates = [h for path, _, h in entries if path == found[0]]
    return _resolve_marker_host(dest, scope, candidates)


def _resolve_target_host(
    destination: Path | None, host: str | None, scope: str, root: Path
) -> str:
    if host is not None:
        return host
    if root == ROOT:
        return detect_host(destination, scope)
    return _detect_host_with_root(destination, scope, root)


def detect_host(destination: Path | None = None, scope: str = "project") -> str:
    return _detect_host_with_root(destination, scope, ROOT)


def _default_destination(scope: str) -> Path:
    return Path.home() if scope == "user" else Path.cwd()


def _resolve_locations_env(
    destination: TargetEnvironment | Path | None,
    host: str | None,
    scope: str,
    root: Path,
) -> tuple[Path, str | None, str, Path]:
    if isinstance(destination, TargetEnvironment):
        default_dest = _default_destination(destination.scope)
        d = _coalesce(destination.destination, default_dest)
        h = _coalesce(host, destination.host)
        s = destination.scope if scope == "project" else scope
        r = destination.root if root == ROOT else root
        return d, h, s, r
    d = destination if destination is not None else _default_destination(scope)
    return d, host, scope, root


def _validate_location_paths(destination: Path, target: Path, state: Path) -> None:
    if not target.resolve().is_relative_to(destination):
        raise ValueError(
            "Host destination escapes the explicitly selected installation root"
        )
    if not state.resolve().is_relative_to(destination):
        raise ValueError("Installation state escapes the destination")


def locations(
    destination: TargetEnvironment | Path | None = None,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
):
    dest, resolved_host, resolved_scope, resolved_root = _resolve_locations_env(
        destination, host, scope, root
    )
    resolved_host = _resolve_target_host(
        dest, resolved_host, resolved_scope, resolved_root
    )
    hosts = read_json(resolved_root / "catalog/hosts.json")["hosts"]
    if resolved_host not in hosts or resolved_scope not in {"project", "user"}:
        raise ValueError("Unknown host or installation scope")
    dest_resolved = dest.resolve()
    target = dest_resolved / relative_path(
        hosts[resolved_host][resolved_scope + "_path"]
    )
    state = dest_resolved / ".fred-skills" / (resolved_host + "-" + resolved_scope)
    _validate_location_paths(dest_resolved, target, state)
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


def _normalize_target_env(
    target: TargetEnvironment | Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    root: Path | None = None,
) -> TargetEnvironment:
    if isinstance(target, TargetEnvironment):
        return TargetEnvironment(
            destination=target.destination,
            host=_coalesce(host, target.host),
            scope=_coalesce(scope, target.scope),
            root=_coalesce(root, target.root),
        )
    return TargetEnvironment(
        destination=_ensure_path(target),
        host=host,
        scope=_coalesce(scope, "project"),
        root=_coalesce(root, ROOT),
    )


def _query_for_explicit_host(dest: Path, host: str, scope: str, root: Path) -> dict:
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    if host not in hosts:
        raise ValueError(f"Unknown host: {host}")
    target, state = locations(dest, host, scope, root)
    rec_path = state / "receipt.json"
    if not rec_path.is_file():
        return {
            "destination": str(dest),
            "target": str(target),
            "state": str(state),
            "host": host,
            "scope": scope,
            "skills": {},
            "message": f"No installation receipt found at destination for host '{host}'",
        }
    skills = receipt(state).get("skills", {})
    res = {
        "destination": str(dest),
        "target": str(target),
        "state": str(state),
        "host": host,
        "scope": scope,
        "skills": skills,
    }
    if not skills:
        res["message"] = f"No skills installed at destination for host '{host}'"
    return res


def _find_receipt_hosts(dest: Path, scope: str, hosts: dict) -> list[str]:
    state_root = dest / ".fred-skills"
    if not state_root.is_dir():
        return []
    return [
        h for h in hosts if (state_root / f"{h}-{scope}" / "receipt.json").is_file()
    ]


def _validate_receipt_hosts(existing_receipt_hosts: list[str], dest: Path) -> None:
    if len(existing_receipt_hosts) <= 1:
        return
    if "antigravity" in existing_receipt_hosts and "codex" in existing_receipt_hosts:
        raise ValueError(
            f"Ambiguous host receipts for both codex and antigravity found in destination '{dest}'; please specify --host"
        )
    hosts_str = ", ".join(sorted(existing_receipt_hosts))
    raise ValueError(
        f"Multiple host receipts found ({hosts_str}) in destination '{dest}'; please specify --host"
    )


def _query_detected_host(dest: Path, scope: str, root: Path) -> dict:
    try:
        detected_host = _resolve_target_host(dest, None, scope, root)
        target, state = locations(dest, detected_host, scope, root)
        return {
            "destination": str(dest),
            "target": str(target),
            "state": str(state),
            "host": detected_host,
            "scope": scope,
            "skills": {},
            "message": f"No installation receipt found at destination for host '{detected_host}'",
        }
    except ValueError as exc:
        if "No host markers" in str(exc):
            return {
                "destination": str(dest),
                "target": None,
                "state": None,
                "host": None,
                "scope": scope,
                "skills": {},
                "message": f"No installation receipt found at destination '{dest}'",
            }
        raise


def query_installed(
    target: TargetEnvironment | Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    root: Path | None = None,
    *,
    destination: Path | str | None = None,
) -> dict:
    """Read the installation receipt and harness paths for a target destination."""
    if destination is not None and target is None:
        target = destination
    env = _normalize_target_env(target, host=host, scope=scope, root=root)
    if env.scope not in {"project", "user"}:
        raise ValueError("Unknown installation scope")
    default_dest = _default_destination(env.scope)
    dest = _coalesce(env.destination, default_dest).resolve()
    hosts = read_json(env.root / "catalog/hosts.json")["hosts"]
    if env.host is not None:
        return _query_for_explicit_host(dest, env.host, env.scope, env.root)
    receipt_hosts = _find_receipt_hosts(dest, env.scope, hosts)
    _validate_receipt_hosts(receipt_hosts, dest)
    if len(receipt_hosts) == 1:
        return _query_for_explicit_host(dest, receipt_hosts[0], env.scope, env.root)
    return _query_detected_host(dest, env.scope, env.root)


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


def _check_install_candidate(
    target: Path, name: str, record: dict | None, root: Path
) -> None:
    source = root / "skills" / name
    package_files(source)  # reject source symlinks before any writes
    path = checked_target(target, name)
    require_unmodified(path, record)


def _validate_install_target_skills(
    target: Path, selected: list[str], current: dict, update: bool
) -> None:
    for name in selected:
        path = checked_target(target, name)
        if path.exists() and not update:
            raise ValueError(
                f"Already installed: {name}; use update after reviewing the source changes"
            )


def _validate_install_under_lock(
    target: Path, selected: list[str], current: dict, update: bool
) -> None:
    for name in selected:
        require_unmodified(checked_target(target, name), current["skills"].get(name))
        if (target / name).exists() and not update:
            raise ValueError(f"Already installed: {name}; use update")


def _stage_skills(
    state: Path, operation: str, selected: list[str], root: Path
) -> tuple[Path, Path]:
    staged = state / "staging" / operation
    backups = state / "backups" / operation
    staged.mkdir(parents=True)
    backups.mkdir(parents=True)
    for name in selected:
        shutil.copytree(
            root / "skills" / name,
            staged / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return staged, backups


def _revert_install_swap(
    target: Path, backups: Path, installed: list[str], moved: list[str]
) -> None:
    for name in reversed(installed):
        shutil.rmtree(target / name)
    for name in reversed(moved):
        os.replace(backups / name, target / name)


def _swap_skills(
    target: Path,
    staged: Path,
    backups: Path,
    params: tuple[list[str], Path],
) -> dict[str, dict]:
    selected, root = params
    target.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    installed: list[str] = []
    new_records: dict[str, dict] = {}
    try:
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
        return new_records
    except BaseException:
        _revert_install_swap(target, backups, installed, moved)
        raise


def _record_install_receipt(state: Path, current: dict, host: str, entry: dict) -> None:
    current["host"] = host
    current["skills"].update(entry["after"])
    current["history"].append(entry)
    atomic_json(state / "receipt.json", current)


def _execute_install(
    target: Path,
    state: Path,
    selected: list[str],
    context: tuple[bool, Path, str, dict],
) -> None:
    update, root, host, preview = context
    current = receipt(state)
    _validate_install_under_lock(target, selected, current, update)
    operation = uuid.uuid4().hex
    staged, backups = _stage_skills(state, operation, selected, root)
    before = dict(current["skills"])
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
        new_records = _swap_skills(target, staged, backups, (selected, root))
        entry = {
            "id": operation,
            "action": preview["action"],
            "skills": selected,
            "before": before,
            "after": new_records,
            "backups": str(backups),
        }
        _record_install_receipt(state, current, host, entry)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    journal["phase"] = "committed"
    atomic_json(state / "operation.json", journal)


def _resolve_install_targets(
    target: Any, options: Any
) -> tuple[TargetEnvironment, InstallOptions | None, bool]:
    if isinstance(target, InstallOptions):
        return target.target or TargetEnvironment(), target, True
    if isinstance(target, TargetEnvironment):
        opt = options if isinstance(options, InstallOptions) else None
        return target, opt, True
    if isinstance(options, InstallOptions):
        dest_val = _ensure_path(target)
        return options.target or TargetEnvironment(destination=dest_val), options, True
    return TargetEnvironment(), None, False


def _normalize_install_args(
    target: TargetEnvironment | Path | str | InstallOptions | None = None,
    options: InstallOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    pack: str | None = None,
    skills: list[str] | None = None,
    update: bool | None = None,
    dry_run: bool | None = None,
    root: Path | None = None,
    **kwargs: Any,
) -> tuple[TargetEnvironment, InstallOptions]:
    base_tgt, base_opt, is_structured = _resolve_install_targets(target, options)
    if is_structured:
        tgt = _merge_target_env(base_tgt, destination, host, scope=scope, root=root)
        opt = _merge_install_options(
            base_opt or InstallOptions(),
            pack,
            skills,
            update=update,
            dry_run=dry_run,
            target=tgt,
        )
        return tgt, opt

    pos_host = options if isinstance(options, str) or options is None else None
    dest_val = _coalesce(destination, target)
    tgt = TargetEnvironment(
        destination=_ensure_path(dest_val),
        host=_coalesce(host, pos_host),
        scope=_coalesce(scope, _arg_at(args, 0), "project"),
        root=_coalesce(root, _arg_at(args, 5), ROOT),
    )
    opt = InstallOptions(
        pack=_coalesce(pack, _arg_at(args, 1)),
        skills=_coalesce(skills, _arg_at(args, 2)),
        update=_coalesce(update, _arg_at(args, 3), False),
        dry_run=_coalesce(dry_run, _arg_at(args, 4), False),
        target=tgt,
    )
    return tgt, opt


def install(
    target: TargetEnvironment | Path | str | InstallOptions | None = None,
    options: InstallOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    pack: str | None = None,
    skills: list[str] | None = None,
    update: bool | None = None,
    dry_run: bool | None = None,
    root: Path | None = None,
) -> dict:
    env, opt = _normalize_install_args(
        target,
        options,
        *args,
        destination=destination,
        host=host,
        scope=scope,
        pack=pack,
        skills=skills,
        update=update,
        dry_run=dry_run,
        root=root,
    )
    resolved_host = _resolve_target_host(env.destination, env.host, env.scope, env.root)
    selected = choose_skills(env.root, opt.pack, opt.skills)
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    current = receipt(state_path)
    for name in selected:
        _check_install_candidate(
            target_path, name, current["skills"].get(name), env.root
        )
    _validate_install_target_skills(target_path, selected, current, opt.update)
    preview = {
        "action": "update" if opt.update else "install",
        "skills": selected,
        "host": resolved_host,
        "scope": env.scope,
        "target": str(target_path),
        "dry_run": opt.dry_run,
        "source_version": version(env.root),
        "runtime_behavior_verified": False,
    }
    if opt.dry_run:
        return preview
    with lock(state_path):
        _execute_install(
            target_path,
            state_path,
            selected,
            (opt.update, env.root, resolved_host, preview),
        )
    return preview


def _validate_remove_skills(target: Path, current: dict, skills: list[str]) -> None:
    for name in skills:
        if name not in current["skills"]:
            raise ValueError(f"Not managed by this installation: {name}")
        require_unmodified(checked_target(target, name), current["skills"][name])


def _execute_remove_operation(
    target: Path, state: Path, current: dict, skills: list[str]
) -> None:
    before = dict(current["skills"])
    backups = state / "backups" / uuid.uuid4().hex
    backups.mkdir(parents=True)
    moved: list[str] = []
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


def _resolve_remove_targets(
    target: Any, options: Any
) -> tuple[TargetEnvironment, RemoveOptions | None, bool]:
    if isinstance(target, RemoveOptions):
        return target.target or TargetEnvironment(), target, True
    if isinstance(target, TargetEnvironment):
        opt = options if isinstance(options, RemoveOptions) else None
        return target, opt, True
    if isinstance(options, RemoveOptions):
        dest_val = _ensure_path(target)
        return options.target or TargetEnvironment(destination=dest_val), options, True
    return TargetEnvironment(), None, False


def _merge_remove_options(
    base: RemoveOptions,
    skills: Any = None,
    dry_run: Any = None,
    target: TargetEnvironment | None = None,
) -> RemoveOptions:
    return RemoveOptions(
        skills=_coalesce(skills, base.skills),
        dry_run=_coalesce(dry_run, base.dry_run, False),
        target=_coalesce(target, base.target),
    )


def _normalize_remove_args(
    target: TargetEnvironment | Path | str | RemoveOptions | None = None,
    options: RemoveOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    skills: list[str] | None = None,
    dry_run: bool | None = None,
    root: Path | None = None,
    **kwargs: Any,
) -> tuple[TargetEnvironment, RemoveOptions]:
    base_tgt, base_opt, is_structured = _resolve_remove_targets(target, options)
    if is_structured:
        tgt = _merge_target_env(base_tgt, destination, host, scope=scope, root=root)
        opt = _merge_remove_options(
            base_opt or RemoveOptions(), skills, dry_run=dry_run, target=tgt
        )
        return tgt, opt

    pos_host = options if isinstance(options, str) or options is None else None
    dest_val = _coalesce(destination, target)
    tgt = TargetEnvironment(
        destination=_ensure_path(dest_val),
        host=_coalesce(host, pos_host),
        scope=_coalesce(scope, _arg_at(args, 0), "project"),
        root=_coalesce(root, _arg_at(args, 3), ROOT),
    )
    opt = RemoveOptions(
        skills=_coalesce(skills, _arg_at(args, 1)),
        dry_run=_coalesce(dry_run, _arg_at(args, 2), False),
        target=tgt,
    )
    return tgt, opt


def remove(
    target: TargetEnvironment | Path | str | RemoveOptions | None = None,
    options: RemoveOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    skills: list[str] | None = None,
    dry_run: bool | None = None,
    root: Path | None = None,
) -> dict:
    env, opt = _normalize_remove_args(
        target,
        options,
        *args,
        destination=destination,
        host=host,
        scope=scope,
        skills=skills,
        dry_run=dry_run,
        root=root,
    )
    resolved_host = _resolve_target_host(env.destination, env.host, env.scope, env.root)
    skills_list = list(dict.fromkeys(opt.skills or []))
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    if opt.dry_run:
        _validate_remove_skills(target_path, receipt(state_path), skills_list)
        return {"action": "remove", "skills": skills_list, "dry_run": True}
    with lock(state_path):
        current = receipt(state_path)
        _validate_remove_skills(target_path, current, skills_list)
        _execute_remove_operation(target_path, state_path, current, skills_list)
    return {"action": "remove", "skills": skills_list, "dry_run": opt.dry_run}


def _validate_rollback_backup_skill(
    target: Path,
    backups: Path,
    name: str,
    context: tuple[dict, dict],
) -> None:
    current, before = context
    path = checked_target(target, name)
    require_unmodified(path, current["skills"].get(name))
    if name in before:
        saved = backups / name
        if (
            not saved.is_dir()
            or digest(package_files(saved)) != before[name]["content_hash"]
        ):
            raise ValueError(f"Missing or modified rollback backup: {name}")


def _validate_rollback_state(
    state: Path, target: Path, current: dict
) -> tuple[dict, Path]:
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
        _validate_rollback_backup_skill(
            target, backups, name, (current, action["before"])
        )
    return action, backups


def _revert_rollback_swap(
    target: Path,
    backups: Path,
    rollback_backup: Path,
    params: tuple[list[str], list[str]],
) -> None:
    restored, moved = params
    for name in reversed(restored):
        os.replace(target / name, backups / name)
    for name in reversed(moved):
        os.replace(rollback_backup / name, target / name)


def _execute_rollback_operation(
    state: Path,
    target: Path,
    current: dict,
    context: tuple[dict, Path],
) -> None:
    action, backups = context
    rollback_backup = state / "backups" / ("rollback-" + uuid.uuid4().hex)
    rollback_backup.mkdir(parents=True)
    moved: list[str] = []
    restored: list[str] = []
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
        _revert_rollback_swap(target, backups, rollback_backup, (restored, moved))
        raise


def _resolve_rollback_targets(
    target: Any, options: Any
) -> tuple[TargetEnvironment, RollbackOptions | None, bool]:
    if isinstance(target, RollbackOptions):
        return target.target or TargetEnvironment(), target, True
    if isinstance(target, TargetEnvironment):
        opt = options if isinstance(options, RollbackOptions) else None
        return target, opt, True
    if isinstance(options, RollbackOptions):
        dest_val = _ensure_path(target)
        return options.target or TargetEnvironment(destination=dest_val), options, True
    return TargetEnvironment(), None, False


def _normalize_rollback_args(
    target: TargetEnvironment | Path | str | RollbackOptions | None = None,
    options: RollbackOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    root: Path | None = None,
    **kwargs: Any,
) -> tuple[TargetEnvironment, RollbackOptions]:
    base_tgt, base_opt, is_structured = _resolve_rollback_targets(target, options)
    if is_structured:
        tgt = _merge_target_env(base_tgt, destination, host, scope=scope, root=root)
        return tgt, base_opt or RollbackOptions(target=tgt)

    pos_host = options if isinstance(options, str) or options is None else None
    dest_val = _coalesce(destination, target)
    tgt = TargetEnvironment(
        destination=_ensure_path(dest_val),
        host=_coalesce(host, pos_host),
        scope=_coalesce(scope, _arg_at(args, 0), "project"),
        root=_coalesce(root, _arg_at(args, 1), ROOT),
    )
    return tgt, RollbackOptions(target=tgt)


def rollback(
    target: TargetEnvironment | Path | str | RollbackOptions | None = None,
    options: RollbackOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    root: Path | None = None,
) -> dict:
    env, _ = _normalize_rollback_args(
        target,
        options,
        *args,
        destination=destination,
        host=host,
        scope=scope,
        root=root,
    )
    resolved_host = _resolve_target_host(env.destination, env.host, env.scope, env.root)
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    with lock(state_path):
        current = receipt(state_path)
        action, backups = _validate_rollback_state(state_path, target_path, current)
        _execute_rollback_operation(state_path, target_path, current, (action, backups))
    return {
        "action": "rollback",
        "skills": action["skills"],
        "runtime_behavior_verified": False,
    }


def _check_already_committed_recovery(
    target: Path, state: Path, journal: dict, current: dict
) -> dict | None:
    if journal.get("phase") == "committed" or any(
        item.get("id") == journal["id"] for item in current["history"]
    ):
        for name in journal["skills"]:
            require_unmodified(
                checked_target(target, name), current["skills"].get(name)
            )
        (state / "operation.lock").unlink(missing_ok=True)
        return {"recovery": "already-committed", "skills": journal["skills"]}
    return None


def _path_has_hash(path: Path, expected_hash: str) -> bool:
    return path.exists() and digest(package_files(path)) == expected_hash


def _backup_has_hash(backup_dir: Path, expected_hash: str) -> bool:
    return backup_dir.is_dir() and digest(package_files(backup_dir)) == expected_hash


def _validate_recovery_skill(
    path: Path,
    backup: Path,
    name: str,
    context: tuple[dict | None, set[str]],
) -> None:
    before, allowed = context
    if path.exists() and digest(package_files(path)) not in allowed:
        raise ValueError(
            f"Modified recovery destination: {name}; no recovery changes made"
        )
    if before is not None:
        expected = before["content_hash"]
        if not _path_has_hash(path, expected) and not _backup_has_hash(
            backup / name, expected
        ):
            raise ValueError(f"Required recovery backup is missing or modified: {name}")


def _validate_recovery_journal(
    state: Path, target: Path, journal: dict, current: dict
) -> Path:
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
        _validate_recovery_skill(path, backup, name, (before, allowed))
    return backup


def _restore_single_skill(
    name: str,
    target: Path,
    backup: Path,
    context: tuple[Path, dict | None],
) -> None:
    preserved, before = context
    path = target / name
    if (
        before
        and path.exists()
        and digest(package_files(path)) == before["content_hash"]
    ):
        return
    if path.exists():
        os.replace(path, preserved / name)
    if before:
        os.replace(backup / name, path)


def _execute_recovery_restore(
    state: Path, target: Path, journal: dict, backup: Path
) -> Path:
    preserved = state / "backups" / ("recovered-" + uuid.uuid4().hex)
    preserved.mkdir(parents=True)
    target.mkdir(parents=True, exist_ok=True)
    for name in journal["skills"]:
        _restore_single_skill(
            name, target, backup, (preserved, journal["before"].get(name))
        )
    journal["phase"] = "recovered"
    atomic_json(state / "operation.json", journal)
    (state / "operation.lock").unlink(missing_ok=True)
    return preserved


def _resolve_recover_targets(
    target: Any, options: Any
) -> tuple[TargetEnvironment, RecoverOptions | None, bool]:
    if isinstance(target, RecoverOptions):
        return target.target or TargetEnvironment(), target, True
    if isinstance(target, TargetEnvironment):
        opt = options if isinstance(options, RecoverOptions) else None
        return target, opt, True
    if isinstance(options, RecoverOptions):
        dest_val = _ensure_path(target)
        return options.target or TargetEnvironment(destination=dest_val), options, True
    return TargetEnvironment(), None, False


def _normalize_recover_args(
    target: TargetEnvironment | Path | str | RecoverOptions | None = None,
    options: RecoverOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    confirm_interrupted: bool | None = None,
    root: Path | None = None,
    **kwargs: Any,
) -> tuple[TargetEnvironment, RecoverOptions]:
    base_tgt, base_opt, is_structured = _resolve_recover_targets(target, options)
    if is_structured:
        tgt = _merge_target_env(base_tgt, destination, host, scope=scope, root=root)
        confirm_val = _coalesce(
            confirm_interrupted,
            base_opt.confirm_interrupted if base_opt else None,
            False,
        )
        return tgt, RecoverOptions(confirm_interrupted=confirm_val, target=tgt)

    pos_host = options if isinstance(options, str) or options is None else None
    dest_val = _coalesce(destination, target)
    tgt = TargetEnvironment(
        destination=_ensure_path(dest_val),
        host=_coalesce(host, pos_host),
        scope=_coalesce(scope, _arg_at(args, 0), "project"),
        root=_coalesce(root, _arg_at(args, 2), ROOT),
    )
    opt = RecoverOptions(
        confirm_interrupted=_coalesce(confirm_interrupted, _arg_at(args, 1), False),
        target=tgt,
    )
    return tgt, opt


def recover(
    target: TargetEnvironment | Path | str | RecoverOptions | None = None,
    options: RecoverOptions | None = None,
    *args: Any,
    destination: Path | str | None = None,
    host: str | None = None,
    scope: str | None = None,
    confirm_interrupted: bool | None = None,
    root: Path | None = None,
) -> dict:
    """Recover a prepared install after the user confirms its writer has stopped."""
    env, opt = _normalize_recover_args(
        target,
        options,
        *args,
        destination=destination,
        host=host,
        scope=scope,
        confirm_interrupted=confirm_interrupted,
        root=root,
    )
    if not opt.confirm_interrupted:
        raise ValueError(
            "Recovery requires --confirm-interrupted after verifying the previous writer has stopped"
        )
    resolved_host = _resolve_target_host(env.destination, env.host, env.scope, env.root)
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    journal = read_json(state_path / "operation.json")
    current = receipt(state_path)
    committed_result = _check_already_committed_recovery(
        target_path, state_path, journal, current
    )
    if committed_result is not None:
        return committed_result
    backup = _validate_recovery_journal(state_path, target_path, journal, current)
    preserved = _execute_recovery_restore(state_path, target_path, journal, backup)
    return {
        "recovery": "restored-before-state",
        "skills": journal["skills"],
        "preserved_interrupted_content": str(preserved),
    }

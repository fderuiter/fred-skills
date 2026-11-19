from __future__ import annotations

import os
import shutil
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    ROOT,
    FredCliError,
    _copy_allowed_skill_package,
    _scan_validated_skill_files,
    atomic_json,
    choose_skills,
    digest,
    package_files,
    read_json,
    read_json_object,
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
    force: bool = False
    target: TargetEnvironment | None = None


@dataclass
class RemoveOptions:
    """Execution options for skill removal."""

    skills: list[str] | None = None
    dry_run: bool = False
    force: bool = False
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
    force: Any = None,
    target: TargetEnvironment | None = None,
) -> InstallOptions:
    return InstallOptions(
        pack=_coalesce(pack, base.pack),
        skills=_coalesce(skills, base.skills),
        update=_coalesce(update, base.update, False),
        dry_run=_coalesce(dry_run, base.dry_run, False),
        force=_coalesce(force, base.force, False),
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
    if not path.exists():
        return {"schema_version": "1.0", "skills": {}, "history": []}
    return read_json_object(
        path,
        label="Installation receipt 'receipt.json'",
        hint="Inspect '.fred-skills/<host>-<scope>/receipt.json' for corruption before retrying.",
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


def _skill_disk_status(target: Path, name: str, record: dict) -> str:
    path = checked_target(target, name)
    if not path.exists():
        return "missing"
    if not path.is_dir():
        return "modified"
    if digest(package_files(path)) != record.get("content_hash"):
        return "modified"
    return "tracked"


def _is_untracked_skill_dir(child: Path, managed: set[str]) -> bool:
    if child.name in managed or child.is_symlink() or not child.is_dir():
        return False
    skill_md = child / "SKILL.md"
    return not skill_md.is_symlink() and skill_md.is_file()


def _scan_untracked_skills(target: Path | None, managed: set[str]) -> list[str]:
    if target is None or target.is_symlink() or not target.is_dir():
        return []
    children = sorted(target.iterdir(), key=lambda p: p.name)
    return [c.name for c in children if _is_untracked_skill_dir(c, managed)]


def _is_state_locked(dest: Path, state: Path | None, scope: str) -> bool:
    if state is not None:
        return (state / "operation.lock").exists()
    state_root = dest / ".fred-skills"
    if not state_root.is_dir():
        return False
    return any(state_root.glob(f"*-{scope}/operation.lock"))


def _enrich_query_result(
    dest: Path, target: Path | None, state: Path | None, base: dict
) -> dict:
    raw_skills = base.get("skills", {})
    enriched_skills: dict[str, dict] = {}
    if target is not None:
        for name, rec in raw_skills.items():
            entry = dict(rec)
            entry["status"] = _skill_disk_status(target, name, rec)
            enriched_skills[name] = entry
    untracked = _scan_untracked_skills(target, set(raw_skills))
    locked = _is_state_locked(dest, state, base["scope"])
    healthy = not locked and all(
        item["status"] == "tracked" for item in enriched_skills.values()
    )
    res = dict(base)
    res["skills"] = enriched_skills
    res["untracked"] = untracked
    res["locked"] = locked
    res["healthy"] = healthy
    return res


def _query_for_explicit_host(dest: Path, host: str, scope: str, root: Path) -> dict:
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    if host not in hosts:
        raise ValueError(f"Unknown host: {host}")
    target, state = locations(dest, host, scope, root)
    rec_path = state / "receipt.json"
    if not rec_path.is_file():
        base = {
            "destination": str(dest),
            "target": str(target),
            "state": str(state),
            "host": host,
            "scope": scope,
            "skills": {},
            "message": f"No installation receipt found at destination for host '{host}'",
        }
        return _enrich_query_result(dest, target, state, base)
    skills = receipt(state).get("skills", {})
    base = {
        "destination": str(dest),
        "target": str(target),
        "state": str(state),
        "host": host,
        "scope": scope,
        "skills": skills,
    }
    if not skills:
        base["message"] = f"No skills installed at destination for host '{host}'"
    return _enrich_query_result(dest, target, state, base)


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


def _query_detected_host(dest: Path, scope: str, root: Path) -> dict[str, Any]:
    try:
        detected_host = _resolve_target_host(dest, None, scope, root)
        target, state = locations(dest, detected_host, scope, root)
        base: dict[str, Any] = {
            "destination": str(dest),
            "target": str(target),
            "state": str(state),
            "host": detected_host,
            "scope": scope,
            "skills": {},
            "message": f"No installation receipt found at destination for host '{detected_host}'",
        }
        return _enrich_query_result(dest, target, state, base)
    except ValueError as exc:
        if "No host markers" in str(exc):
            base = {
                "destination": str(dest),
                "target": None,
                "state": None,
                "host": None,
                "scope": scope,
                "skills": {},
                "message": f"No installation receipt found at destination '{dest}'",
            }
            return _enrich_query_result(dest, None, None, base)
        raise


def _dedupe_hosts_by_target(
    dest: Path, scope: str, hosts: dict[str, Any], candidates: list[str]
) -> list[str]:
    seen_targets: set[Path] = set()
    unique_hosts: list[str] = []
    for host_name in candidates:
        rel = relative_path(hosts[host_name][f"{scope}_path"])
        target = (dest / rel).resolve()
        if target not in seen_targets:
            seen_targets.add(target)
            unique_hosts.append(host_name)
    return unique_hosts


def _marker_preferred_hosts(dest: Path, scope: str, candidates: list[str]) -> list[str]:
    with_receipt = [h for h in candidates if _receipt_identifies_host(dest, h, scope)]
    return with_receipt if with_receipt else [candidates[0]]


def _collect_marker_candidates(
    dest: Path,
    scope: str,
    active: dict[str, list[str]],
    receipt_hosts: list[str],
) -> set[str]:
    chosen: set[str] = set(receipt_hosts)
    for candidates in active.values():
        chosen.update(_marker_preferred_hosts(dest, scope, candidates))
    return chosen


def _detect_workspace_hosts(dest: Path, scope: str, root: Path) -> list[str]:
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    entries = _host_marker_entries(hosts, scope)
    active = _select_active_markers(dest, scope, hosts, entries)
    receipt_hosts = _find_receipt_hosts(dest, scope, hosts)
    if not active and not receipt_hosts:
        return []
    chosen = _collect_marker_candidates(dest, scope, active, receipt_hosts)
    ordered = [h for h in hosts if h in chosen]
    return _dedupe_hosts_by_target(dest, scope, hosts, ordered)


def _check_shared_target_receipts(
    dest: Path, scope: str, hosts: dict[str, Any], receipt_hosts: list[str]
) -> None:
    by_target: dict[Path, list[str]] = {}
    for host_name in receipt_hosts:
        rel = relative_path(hosts[host_name][f"{scope}_path"])
        by_target.setdefault((dest / rel).resolve(), []).append(host_name)
    for shared in by_target.values():
        if len(shared) > 1:
            label = " and ".join(shared)
            raise ValueError(
                f"Ambiguous host receipts for both {label} found in destination '{dest}'; please specify --host"
            )


def _merge_multi_host_skills(
    per_host: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    merged_skills: dict[str, dict[str, Any]] = {}
    untracked_set: set[str] = set()
    for host_name, h_res in per_host.items():
        for s_name, s_info in h_res["skills"].items():
            agg = merged_skills.setdefault(s_name, {**s_info, "hosts": []})
            agg["hosts"].append(host_name)
            if s_info["status"] != "tracked":
                agg["status"] = s_info["status"]
        untracked_set.update(h_res["untracked"])
    return merged_skills, sorted(untracked_set)


def _aggregate_multi_host_queries(
    dest: Path, scope: str, per_host: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    merged_skills, untracked = _merge_multi_host_skills(per_host)
    locked = any(h_res["locked"] for h_res in per_host.values())
    healthy = not locked and all(h_res["healthy"] for h_res in per_host.values())
    res: dict[str, Any] = {
        "destination": str(dest),
        "target": None,
        "state": None,
        "host": None,
        "hosts": list(per_host.values()),
        "targets": {h: h_res["target"] for h, h_res in per_host.items()},
        "states": {h: h_res["state"] for h, h_res in per_host.items()},
        "scope": scope,
        "skills": merged_skills,
        "untracked": untracked,
        "locked": locked,
        "healthy": healthy,
    }
    if not merged_skills and not untracked:
        res["message"] = f"No installation receipt found at destination '{dest}'"
    return res


def _resolve_lifecycle_host(
    dest: Path | None, host: str | None, scope: str, root: Path
) -> str:
    if host is not None:
        return host
    resolved_dest = _coalesce(dest, _default_destination(scope)).resolve()
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    receipt_hosts = _find_receipt_hosts(resolved_dest, scope, hosts)
    _validate_receipt_hosts(receipt_hosts, resolved_dest)
    try:
        return _resolve_target_host(dest, None, scope, root)
    except ValueError as exc:
        if "No host markers" not in str(exc):
            raise
        if len(receipt_hosts) == 1:
            return receipt_hosts[0]
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
    _check_shared_target_receipts(dest, env.scope, hosts, receipt_hosts)
    detected = _detect_workspace_hosts(dest, env.scope, env.root)
    if not detected:
        return _query_detected_host(dest, env.scope, env.root)
    if len(detected) == 1:
        return _query_for_explicit_host(dest, detected[0], env.scope, env.root)
    per_host = {
        h: _query_for_explicit_host(dest, h, env.scope, env.root) for h in detected
    }
    return _aggregate_multi_host_queries(dest, env.scope, per_host)


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
        if not path.is_dir() or digest(package_files(path)) != record["content_hash"]:
            raise ValueError(
                f"Locally modified skill: {path.name}; pass --force for managed recovery or preserve/move it before updating/removing"
            )
    elif record:
        raise ValueError(
            f"Managed skill is missing: {path.name}; pass --force for managed recovery or inspect the receipt before proceeding"
        )


def _validate_managed_target(path: Path, record: dict | None, force: bool) -> None:
    if path.exists() and not record:
        raise ValueError(f"Refusing to replace unmanaged skill: {path.name}")
    if not force:
        require_unmodified(path, record)


def _check_install_candidate(
    target: Path, name: str, record: dict | None, context: tuple[Path, bool]
) -> None:
    root, force = context
    source = root / "skills" / name
    _scan_validated_skill_files(source, root=root)
    path = checked_target(target, name)
    _validate_managed_target(path, record, force)


def _validate_install_target_skills(
    target: Path, selected: list[str], current: dict, flags: tuple[bool, bool]
) -> None:
    update, force = flags
    for name in selected:
        path = checked_target(target, name)
        if path.exists() and not update and not force:
            raise ValueError(
                f"Already installed: {name}; use update after reviewing the source changes"
            )


def _validate_install_under_lock(
    target: Path, selected: list[str], current: dict, flags: tuple[bool, bool]
) -> None:
    update, force = flags
    for name in selected:
        path = checked_target(target, name)
        _validate_managed_target(path, current["skills"].get(name), force)
        if path.exists() and not update and not force:
            raise ValueError(f"Already installed: {name}; use update")


def _stage_skills(
    state: Path, operation: str, selected: list[str], root: Path
) -> tuple[Path, Path]:
    staged = state / "staging" / operation
    backups = state / "backups" / operation
    staged.mkdir(parents=True)
    backups.mkdir(parents=True)
    for name in selected:
        _copy_allowed_skill_package(root / "skills" / name, staged / name, root=root)
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
    context: tuple[tuple[bool, bool], Path, str, dict],
) -> None:
    flags, root, host, preview = context
    current = receipt(state)
    _validate_install_under_lock(target, selected, current, flags)
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
    force: bool | None = None,
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
            force=force,
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
        force=_coalesce(force, False),
        target=tgt,
    )
    return tgt, opt


def _resolve_install_hosts(env: TargetEnvironment, update: bool) -> list[str]:
    if env.scope not in {"project", "user"}:
        raise ValueError("Unknown installation scope")
    if env.host is not None:
        return [env.host]
    dest = _coalesce(env.destination, _default_destination(env.scope)).resolve()
    detected = _detect_workspace_hosts(dest, env.scope, env.root)
    if detected:
        return detected
    if not update:
        return ["codex"]
    _check_host_markers(dest, env.scope, env.root)
    return []


def _select_install_skills(
    env: TargetEnvironment, opt: InstallOptions, resolved_hosts: list[str]
) -> list[str]:
    if opt.pack or opt.skills:
        return choose_skills(env.root, opt.pack, opt.skills)
    if opt.update:
        managed: list[str] = []
        for host_name in resolved_hosts:
            _, state_path = locations(env.destination, host_name, env.scope, env.root)
            managed.extend(receipt(state_path)["skills"].keys())
        if managed:
            return choose_skills(env.root, None, list(dict.fromkeys(managed)))
    return choose_skills(env.root, "starter", None)


def _prepare_host_plan(
    env: TargetEnvironment,
    opt: InstallOptions,
    host_name: str,
    selected: list[str],
) -> tuple[Path, Path, dict[str, Any]]:
    target_path, state_path = locations(env.destination, host_name, env.scope, env.root)
    current = receipt(state_path)
    flags = (opt.update, opt.force)
    for name in selected:
        _check_install_candidate(
            target_path, name, current["skills"].get(name), (env.root, opt.force)
        )
    _validate_install_target_skills(target_path, selected, current, flags)
    preview = {
        "action": "update" if opt.update else "install",
        "skills": selected,
        "host": host_name,
        "scope": env.scope,
        "target": str(target_path),
        "dry_run": opt.dry_run,
        "source_version": version(env.root),
        "runtime_behavior_verified": False,
    }
    return target_path, state_path, preview


def _rollback_committed_hosts(committed: list[tuple[Path, Path]]) -> None:
    for target_path, state_path in reversed(committed):
        current = receipt(state_path)
        action, backups = _validate_rollback_state(state_path, target_path, current)
        _execute_rollback_operation(state_path, target_path, current, (action, backups))


def _commit_host_plans(
    plans: list[tuple[str, Path, Path, dict[str, Any]]],
    selected: list[str],
    flags: tuple[bool, bool],
    root: Path,
) -> None:
    committed: list[tuple[Path, Path]] = []
    with ExitStack() as stack:
        for _, _, state_path, _ in plans:
            stack.enter_context(lock(state_path))
        for _, target_path, state_path, _ in plans:
            _validate_install_under_lock(
                target_path, selected, receipt(state_path), flags
            )
        try:
            for host_name, target_path, state_path, preview in plans:
                _execute_install(
                    target_path,
                    state_path,
                    selected,
                    (flags, root, host_name, preview),
                )
                committed.append((target_path, state_path))
        except BaseException:
            _rollback_committed_hosts(committed)
            raise


def _format_install_result(
    plans: list[tuple[str, Path, Path, dict[str, Any]]],
    selected: list[str],
    env: TargetEnvironment,
    opt: InstallOptions,
) -> dict[str, Any]:
    if len(plans) == 1:
        return plans[0][3]
    return {
        "action": "update" if opt.update else "install",
        "skills": selected,
        "host": None,
        "target": None,
        "hosts": [p for _, _, _, p in plans],
        "targets": {h: str(t) for h, t, _, _ in plans},
        "scope": env.scope,
        "dry_run": opt.dry_run,
        "source_version": version(env.root),
        "runtime_behavior_verified": False,
    }


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
    force: bool | None = None,
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
        force=force,
        root=root,
    )
    resolved_hosts = _resolve_install_hosts(env, opt.update)
    selected = _select_install_skills(env, opt, resolved_hosts)
    plans = [(h, *_prepare_host_plan(env, opt, h, selected)) for h in resolved_hosts]
    result = _format_install_result(plans, selected, env, opt)
    if opt.dry_run:
        return result
    _commit_host_plans(plans, selected, (opt.update, opt.force), env.root)
    return result


def _validate_remove_skills(
    target: Path, current: dict, skills: list[str], force: bool = False
) -> None:
    for name in skills:
        if name not in current["skills"]:
            raise ValueError(f"Not managed by this installation: {name}")
        path = checked_target(target, name)
        _validate_managed_target(path, current["skills"][name], force)


def _execute_remove_operation(
    target: Path, state: Path, current: dict, skills: list[str]
) -> None:
    before = dict(current["skills"])
    backups = state / "backups" / uuid.uuid4().hex
    backups.mkdir(parents=True)
    moved: list[str] = []
    try:
        for name in skills:
            path = checked_target(target, name)
            if path.exists():
                os.replace(path, backups / name)
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
            target.mkdir(parents=True, exist_ok=True)
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
    *,
    force: Any = None,
    target: TargetEnvironment | None = None,
) -> RemoveOptions:
    return RemoveOptions(
        skills=_coalesce(skills, base.skills),
        dry_run=_coalesce(dry_run, base.dry_run, False),
        force=_coalesce(force, base.force, False),
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
    force: bool | None = None,
    root: Path | None = None,
    **kwargs: Any,
) -> tuple[TargetEnvironment, RemoveOptions]:
    base_tgt, base_opt, is_structured = _resolve_remove_targets(target, options)
    if is_structured:
        tgt = _merge_target_env(base_tgt, destination, host, scope=scope, root=root)
        opt = _merge_remove_options(
            base_opt or RemoveOptions(),
            skills,
            dry_run=dry_run,
            force=force,
            target=tgt,
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
        force=_coalesce(force, False),
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
    force: bool | None = None,
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
        force=force,
        root=root,
    )
    resolved_host = _resolve_lifecycle_host(
        env.destination, env.host, env.scope, env.root
    )
    skills_list = list(dict.fromkeys(opt.skills or []))
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    if opt.dry_run:
        _validate_remove_skills(
            target_path, receipt(state_path), skills_list, opt.force
        )
        return {"action": "remove", "skills": skills_list, "dry_run": True}
    with lock(state_path):
        current = receipt(state_path)
        _validate_remove_skills(target_path, current, skills_list, opt.force)
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
    resolved_host = _resolve_lifecycle_host(
        env.destination, env.host, env.scope, env.root
    )
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


def _load_recovery_journal(state: Path) -> dict:
    op_path = state / "operation.json"
    if not op_path.is_file():
        raise FredCliError(
            f"No interrupted installation journal 'operation.json' found in {state}",
            code="missing_operation_journal",
            hint="Check installation status with 'python scripts/fred.py installed' (or '--check'); 'recover-install' is only needed when an interrupted operation left 'operation.json'.",
        )
    journal = read_json_object(
        op_path,
        label="Installation journal 'operation.json'",
        hint="Inspect 'operation.json' and check 'python scripts/fred.py installed' status before retrying.",
    )
    required_keys = {"id", "skills", "before", "after_hashes", "backups"}
    if (
        not required_keys.issubset(journal)
        or not isinstance(journal["skills"], list)
        or not isinstance(journal["before"], dict)
        or not isinstance(journal["after_hashes"], dict)
        or not isinstance(journal["backups"], str)
    ):
        raise FredCliError(
            f"Malformed installation journal in {op_path}; no recovery changes made",
            code="invalid_operation_journal",
            hint="Inspect 'operation.json' manually and check 'python scripts/fred.py installed' status.",
        )
    return journal


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
        raise FredCliError(
            "Recovery requires --confirm-interrupted after verifying the previous writer has stopped",
            code="confirmation_required",
            hint="Check 'python scripts/fred.py installed', confirm the previous writer exited, and rerun with --confirm-interrupted.",
        )
    resolved_host = _resolve_lifecycle_host(
        env.destination, env.host, env.scope, env.root
    )
    target_path, state_path = locations(
        env.destination, resolved_host, env.scope, env.root
    )
    journal = _load_recovery_journal(state_path)
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

#!/usr/bin/env python3
"""Local discovery, installation, run management, and validation for fred-skills."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
from fredlib import (
    evaluations,
    installation,
    releases,
    reports,
    repository,
    runs,
    vetting,
)
from fredlib.common import (
    ROOT,
    WORKFLOW_DELIVERABLE_SCHEMAS,
    FredCliError,
    _detect_domain_kind,
    atomic_json,
    read_json,
    read_json_object,
    resolve_audit_path,
)

HOST_CHOICES = sorted(read_json(ROOT / "catalog/hosts.json")["hosts"].keys())
TASK_CHOICES = sorted(read_json(ROOT / "catalog/recipes.json")["recipes"].keys())
PACK_CHOICES = sorted(read_json(ROOT / "catalog/packs.json")["packs"].keys())
PROFILE_CHOICES = sorted(read_json(ROOT / "catalog/profiles.json")["profiles"].keys())

LIFECYCLE_GROUPS = {
    "Discovery": [
        "tasks",
        "packs",
        "hosts",
        "search",
    ],
    "Planning": [
        "preflight",
        "plan",
        "init",
    ],
    "Run Management": [
        "validate-run",
        "resume",
        "advance",
        "memory",
        "checkpoint",
        "report",
        "issues",
        "compare",
        "reconcile",
        "import-sarif",
        "export-sarif",
        "triage",
    ],
    "Installation": [
        "vet",
        "installed",
        "install",
        "update",
        "remove",
        "rollback",
        "recover-install",
    ],
    "Release": [
        "evaluate",
        "release",
        "verify-release",
    ],
}


def _parse_adapter_argv(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc}") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(arg, str) for arg in parsed)
    ):
        raise argparse.ArgumentTypeError(
            "Adapter argv must be a nonempty JSON array of strings"
        )
    return parsed


_IDENTIFIER_HEADERS = frozenset({"Skill", "Host", "Pack", "Task"})


def _fit_column_widths(
    headers: list[str], natural: list[int], term_width: int
) -> list[int]:
    sep_width = 2 * max(0, len(headers) - 1)
    if sum(natural) + sep_width <= term_width:
        return list(natural)
    clamped_term = max(term_width, 40)
    widths = list(natural)
    flex = [i for i, h in enumerate(headers) if h not in _IDENTIFIER_HEADERS]
    if not flex:
        return widths
    fixed_total = sum(widths[i] for i in range(len(headers)) if i not in flex)
    budget = clamped_term - sep_width - fixed_total
    floors = [min(natural[i], max(len(headers[i]), 6)) for i in flex]
    if budget <= sum(floors):
        min_floors = [max(4, min(natural[i], 6)) for i in flex]
        if budget <= sum(min_floors):
            for idx, col in enumerate(flex):
                widths[col] = min_floors[idx]
            return widths
        floors = min_floors
    remaining = set(flex)
    floor_map = dict(zip(flex, floors, strict=True))
    avail = budget
    while remaining:
        share = avail // len(remaining)
        small = [i for i in remaining if natural[i] <= share]
        if small:
            for col in small:
                widths[col] = natural[col]
                avail -= widths[col]
                remaining.remove(col)
            continue
        clamped = [i for i in remaining if share <= floor_map[i]]
        if clamped:
            for col in clamped:
                widths[col] = floor_map[col]
                avail -= widths[col]
                remaining.remove(col)
            continue
        sorted_rem = sorted(remaining)
        extra = avail - share * len(sorted_rem)
        for idx, col in enumerate(sorted_rem):
            widths[col] = share + (1 if idx < extra else 0)
        break
    return widths


_PATH_SEG_RE = re.compile(r"[/\\]*[^/\\]+[/\\]*")


def _tokenize_path_units(para: str, safe_width: int) -> list[str]:
    raw = _PATH_SEG_RE.findall(para)
    if not raw:
        return [para]
    units: list[str] = []
    idx = 0
    while idx < len(raw):
        seg = raw[idx]
        if seg.startswith(".") and idx + 1 < len(raw):
            combined = seg + raw[idx + 1]
            trimmed = combined.rstrip("/\\")
            if len(combined) <= safe_width:
                units.append(combined)
                idx += 2
                continue
            if len(trimmed) <= safe_width:
                trailing = combined[len(trimmed) :]
                units.append(trimmed)
                if trailing and idx + 2 < len(raw):
                    raw[idx + 2] = trailing + raw[idx + 2]
                elif trailing:
                    units.append(trailing)
                idx += 2
                continue
        units.append(seg)
        idx += 1
    return units


def _wrap_path_paragraph(para: str, safe_width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for unit in _tokenize_path_units(para, safe_width):
        if cur and len(cur) + len(unit) <= safe_width:
            cur += unit
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if len(unit) <= safe_width:
            cur = unit
        else:
            chunks = textwrap.wrap(unit, width=safe_width) or [""]
            lines.extend(chunks[:-1])
            cur = chunks[-1]
    if cur:
        lines.append(cur)
    return lines or [""]


def _wrap_table_cell(text: str, width: int, keep_intact: bool) -> list[str]:
    if keep_intact:
        return [text]
    safe_width = max(1, width)
    paragraphs = text.splitlines() or [""]
    wrapped: list[str] = []
    for para in paragraphs:
        if len(para) <= safe_width:
            wrapped.append(para)
        elif ("/" in para or "\\" in para) and " " not in para:
            wrapped.extend(_wrap_path_paragraph(para, safe_width))
        else:
            wrapped.extend(textwrap.wrap(para, width=safe_width) or [""])
    return wrapped or [""]


def _render_table_row(
    cells: list[str], widths: list[int], keep_mask: list[bool]
) -> list[str]:
    wrapped_cols = [
        _wrap_table_cell(cells[i], widths[i], keep_mask[i]) for i in range(len(widths))
    ]
    height = max(len(col) for col in wrapped_cols)
    lines: list[str] = []
    for line_idx in range(height):
        parts = [
            (
                wrapped_cols[i][line_idx] if line_idx < len(wrapped_cols[i]) else ""
            ).ljust(widths[i])
            for i in range(len(widths))
        ]
        lines.append("  ".join(parts).rstrip())
    return lines


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    if not headers or not rows:
        return "No entries found."
    norm_rows = [
        [str(row[i]) if i < len(row) else "" for i in range(len(headers))]
        for row in rows
    ]
    widths = [
        max(len(h), *(len(row[i]) for row in norm_rows)) for i, h in enumerate(headers)
    ]
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if is_tty:
        term_cols = shutil.get_terminal_size((80, 24)).columns
        widths = _fit_column_widths(headers, widths, term_cols)
    keep_mask = [(not is_tty) or (h in _IDENTIFIER_HEADERS) for h in headers]
    header_lines = _render_table_row(headers, widths, keep_mask)
    separator_line = "  ".join("-" * widths[i] for i in range(len(headers)))
    data_lines: list[str] = []
    for row in norm_rows:
        data_lines.extend(_render_table_row(row, widths, keep_mask))
    return "\n".join([*header_lines, separator_line, *data_lines])


def _installed_rows_for_host(host_label: str, entry: dict[str, Any]) -> list[list[str]]:
    skills_dict = entry.get("skills", {})
    untracked_list = entry.get("untracked", [])
    target = entry.get("target", "")
    rows = [
        [
            host_label,
            name,
            info.get("source_version", "unknown"),
            info.get("status", "tracked"),
            str(Path(target) / name) if target else "",
        ]
        for name, info in skills_dict.items()
    ]
    risky_set = set(entry.get("risky_untracked", []))
    for item in untracked_list:
        name = item if isinstance(item, str) else item.get("name", "")
        status_label = "untracked (risky)" if name in risky_set else "untracked"
        rows.append(
            [
                host_label,
                name,
                "-",
                status_label,
                str(Path(target) / name) if target else "",
            ]
        )
    return rows


def _render_installed_table(data: Any) -> str:
    headers = ["Host", "Skill", "Version", "Status", "Harness Path"]
    if not isinstance(data, dict):
        return "No entries found."
    hosts_val = data.get("hosts")
    rows: list[list[str]] = []
    if isinstance(hosts_val, list):
        for host_entry in hosts_val:
            if isinstance(host_entry, dict):
                rows.extend(
                    _installed_rows_for_host(
                        str(host_entry.get("host") or "-"), host_entry
                    )
                )
    elif isinstance(hosts_val, dict):
        for host_name, host_entry in hosts_val.items():
            if isinstance(host_entry, dict):
                rows.extend(_installed_rows_for_host(host_name, host_entry))
    else:
        host_label = str(data.get("host") or "-")
        rows.extend(_installed_rows_for_host(host_label, data))
    if not rows:
        if data.get("message"):
            return str(data["message"])
        return "No entries found."
    return format_table(headers, rows)


def render_discovery_table(command: str, data: Any) -> str:
    if command == "hosts":
        headers = ["Host", "Project Path", "Invocation"]
        hosts_dict = data.get("hosts", {}) if isinstance(data, dict) else {}
        rows = [
            [name, info.get("project_path", ""), info.get("invocation", "")]
            for name, info in hosts_dict.items()
        ]
        return format_table(headers, rows)
    if command == "packs":
        headers = ["Pack", "Skills", "Description"]
        packs_dict = data.get("packs", {}) if isinstance(data, dict) else {}
        rows = [
            [
                name,
                f"{len(info.get('skills', []))} skills",
                info.get("description", ""),
            ]
            for name, info in packs_dict.items()
        ]
        return format_table(headers, rows)
    if command == "tasks":
        headers = ["Task", "Outcome", "Skills"]
        tasks_dict = data.get("recipes", {}) if isinstance(data, dict) else {}
        rows = [
            [
                name,
                info.get("outcome", ""),
                f"{len(info.get('skills', []))} skills",
            ]
            for name, info in tasks_dict.items()
        ]
        return format_table(headers, rows)
    if command == "search":
        headers = ["Skill", "Title", "Description"]
        skills_list = data if isinstance(data, list) else []
        rows = [
            [
                item.get("name", ""),
                item.get("title", ""),
                item.get("description", ""),
            ]
            for item in skills_list
        ]
        return format_table(headers, rows)
    if command == "installed":
        return _render_installed_table(data)
    return ""


def _render_planning_tty(command: str, data: dict[str, Any]) -> str:
    if command == "preflight":
        repo = data.get("repository", {})
        snap = data.get("snapshot", {})
        file_count = snap.get("file_count", len(snap.get("files", {})))
        dirty_suffix = " (dirty)" if repo.get("dirty") else ""
        langs = ", ".join(data.get("languages", [])) or "none detected"
        deps = data.get("dependencies", {})
        dep_pkgs = deps.get("packages", {}) if isinstance(deps, dict) else {}
        dep_manifests = deps.get("manifests", []) if isinstance(deps, dict) else []
        lines = [
            f"Repository: {repo.get('path', '')}",
            f"Revision: {repo.get('revision', 'unversioned')}{dirty_suffix}",
            f"Files indexed: {file_count} (complete: {snap.get('complete', True)})",
            f"Languages: {langs}",
            f"Dependencies: {len(dep_pkgs)} packages across {len(dep_manifests)} manifests",
        ]
        return "\n".join(lines)
    if command == "plan":
        plan_info = data.get("plan", {})
        repo = data.get("preflight", {}).get("repository", {})
        skills = plan_info.get("skills", [])
        deferred = plan_info.get("deferred_skills", [])
        outputs = plan_info.get("expected_outputs", [])
        lines = [
            f"Plan: {plan_info.get('task', '')} (mode: {plan_info.get('mode', '')}, budget: {plan_info.get('budget', '')})",
            f"Repository: {repo.get('path', '')} ({repo.get('revision', 'unversioned')})",
            f"Selected skills ({len(skills)}): {', '.join(skills) or 'none'}",
            f"Deferred skills ({len(deferred)}): {', '.join(deferred) or 'none'}",
            f"Expected outputs: {', '.join(outputs) or 'none'}",
        ]
        if plan_info.get("base_revision"):
            changed = plan_info.get("changed_files", [])
            lines.append(
                f"Changed files ({len(changed)} vs {plan_info['base_revision'][:12]}): {', '.join(changed[:10]) or 'none'}"
            )
        return "\n".join(lines)
    run = data.get("run", {})
    plan_info = run.get("plan", {})
    output = data.get("output", "")
    skills = plan_info.get("skills", [])
    prompt_path = str(Path(output) / "prompt.md") if output else "prompt.md"
    return "\n".join(
        [
            f"Initialized run: {run.get('run_id', '')} ({plan_info.get('task', '')}, mode: {plan_info.get('mode', '')}, budget: {plan_info.get('budget', '')})",
            f"Workspace: {output}",
            f"Prompt: {prompt_path}",
            f"Selected skills ({len(skills)}): {', '.join(skills) or 'none'}",
        ]
    )


def _render_run_management_tty(command: str, data: dict[str, Any]) -> str:
    if command == "validate-run":
        status = "valid" if data.get("valid") else "invalid"
        return f"Run validation: {status} (semantic claims verified: {str(bool(data.get('semantic_claims_verified'))).lower()})"
    if command == "resume":
        changed = data.get("changed_files", [])
        stale = data.get("stale_finding_ids", [])
        old_snap = str(data.get("old_snapshot", ""))[:12]
        new_snap = str(data.get("new_snapshot", ""))[:12]
        return "\n".join(
            [
                f"Resume check: {len(changed)} changed files, {len(stale)} stale findings",
                f"Snapshot: {old_snap} -> {new_snap}",
                f"Next action: {data.get('next_action', '')}",
            ]
        )
    if command == "advance":
        changed = data.get("changed_files", [])
        old_snap = str(data.get("old_snapshot", ""))[:12]
        new_snap = str(data.get("new_snapshot", ""))[:12]
        active_skill = data.get("active_skill")
        next_step = (
            f"Next pass ({data.get('pass_index', 0) + 1}/{data.get('total_passes', 0)}): {active_skill}"
            if active_skill
            else f"Completed all {data.get('total_passes', 0)} passes"
        )
        return "\n".join(
            [
                f"Advanced run {data.get('run_id', '')}: completed {data.get('completed_skill', '')} ({len(changed)} changed files)",
                f"Snapshot: {old_snap} -> {new_snap}",
                next_step,
            ]
        )
    if command == "memory":
        if "contracts" in data:
            return f"Exported memory: {data.get('output', '')} ({data.get('contracts', 0)} contracts)"
        return f"Exported memory: {data.get('output', '')} ({data.get('records', 0)} records from {data.get('deliverable', 'domain')})"
    if command == "checkpoint":
        checkpoints = data.get("checkpoints", [])
        latest_note = checkpoints[-1].get("note", "") if checkpoints else ""
        return "\n".join(
            [
                f"Recorded checkpoint: {data.get('run_id', '')} -> {data.get('state', '')}",
                f"Note: {latest_note}",
            ]
        )
    if command == "report":
        return f"Rendered report: {data.get('output', '')}"
    if command == "issues":
        drafts = data.get("drafts", [])
        lines = [
            f"Generated issue drafts: {len(drafts)} (remote writes: {data.get('remote_writes', 0)})"
        ]
        lines.extend(f"  {path}" for path in drafts)
        return "\n".join(lines)
    if command == "compare":
        return (
            "Comparison summary: "
            f"{len(data.get('introduced', []))} introduced, "
            f"{len(data.get('persisting', []))} persisting, "
            f"{len(data.get('status_changed', []))} status changed, "
            f"{len(data.get('not_observed', []))} not observed, "
            f"{len(data.get('ambiguous', []))} ambiguous"
        )
    if command == "reconcile":
        return "\n".join(
            [
                f"Reconciled finding {data.get('finding_id', '')}: {data.get('state', '')}",
                f"Note: {data.get('note', '')}",
            ]
        )
    if command == "import-sarif":
        return f"Imported SARIF observations: {data.get('observations', 0)} -> {data.get('output', '')}"
    return (
        f"Exported SARIF results: {data.get('results', 0)} -> {data.get('output', '')}"
    )


def _render_installation_tty(command: str, data: dict[str, Any]) -> str:
    skills = data.get("skills", [])
    prefix = "Dry run: " if data.get("dry_run") else ""
    if command in {"install", "update"}:
        verb = (
            ("install" if command == "install" else "update")
            if prefix
            else ("Installed" if command == "install" else "Updated")
        )
        hosts_list = data.get("hosts")
        if isinstance(hosts_list, list) and hosts_list:
            host_names = ", ".join(str(h.get("host", "")) for h in hosts_list)
            lines = [
                f"{prefix}{verb} {len(skills)} skills across {len(hosts_list)} hosts ({host_names}, {data.get('scope', '')}):"
            ]
            lines.extend(
                f"  {h.get('host', '')} -> {h.get('target', '')}" for h in hosts_list
            )
            return "\n".join(lines)
        return f"{prefix}{verb} {len(skills)} skills for {data.get('host', '')} ({data.get('scope', '')}) -> {data.get('target', '')}"
    if command == "remove":
        verb = "remove" if prefix else "Removed"
        return f"{prefix}{verb} {len(skills)} skills: {', '.join(skills) or 'none'}"
    if command == "rollback":
        return f"Rolled back {len(skills)} skills: {', '.join(skills) or 'none'}"
    lines = [
        f"Install recovery ({data.get('recovery', '')}): {len(skills)} skills ({', '.join(skills) or 'none'})"
    ]
    if data.get("preserved_interrupted_content"):
        lines.append(f"Preserved content: {data['preserved_interrupted_content']}")
    return "\n".join(lines)


def _render_release_tty(command: str, data: dict[str, Any]) -> str:
    if command == "evaluate":
        runs_list = data.get("runs", [])
        summary = data.get("summary", {})
        lines = [f"Evaluation complete: {len(runs_list)} runs"]
        for label, key in [
            ("Without skill", "without_skill"),
            ("With skill", "with_skill"),
        ]:
            info = summary.get(key, {})
            rate = info.get("exact_match_rate")
            rate_str = f"{rate:.0%}" if isinstance(rate, int | float) else "n/a"
            lines.append(
                f"{label}: {info.get('runs', 0)} runs, exact match {rate_str}, {info.get('failures', 0)} failures"
            )
        return "\n".join(lines)
    if command == "release":
        archives = data.get("archives", [])
        skills = data.get("skills", [])
        return f"Built release v{data.get('version', '')} (pack: {data.get('pack', '')}): {len(archives)} archives ({len(skills)} skills)"
    return "\n".join(
        [
            f"Verified release v{data.get('version', '')}: {data.get('verified_archives', 0)} archives verified",
            str(data.get("authenticity", "")),
        ]
    ).rstrip()


def _render_triage_tty(
    data: dict[str, Any], args: argparse.Namespace | None = None
) -> str:
    fc = data.get("failure_classification", {})
    failure_type = fc.get("failure_type", "unknown")
    output_dir = (
        (getattr(args, "output", None) or getattr(args, "output_dir", None))
        if args is not None
        else None
    )
    if output_dir:
        out_path = Path(output_dir)
        return "\n".join(
            [
                f"Exported diagnostic bundle: {out_path / 'diagnostic_bundle.json'}",
                f"Exported diagnostic report: {out_path / 'diagnostic_report.md'}",
                f"Failure classification: {failure_type}",
            ]
        )
    line_num = fc.get("line_number")
    line_suffix = f" (line {line_num})" if line_num is not None else ""
    lines = [
        f"Session: {data.get('session_id', 'unknown')}",
        f"Failure classification: {failure_type}{line_suffix}",
    ]
    if fc.get("summary"):
        lines.append(f"Summary: {fc['summary']}")
    specialist = data.get("correlated_domain", {}).get("specialist_skill")
    if specialist:
        lines.append(f"Correlated domain: {specialist}")
    steps = data.get("recovery", {}).get("instructions", [])
    if steps:
        lines.append("Recovery steps:")
        lines.extend(f"  {step}" for step in steps)
    return "\n".join(lines)


def _render_vet_tty(data: dict[str, Any]) -> str:
    target = str(data.get("target", "."))
    kind = str(data.get("kind", "target"))
    scanned = data.get("scanned_files", 0)
    findings = data.get("findings", [])
    if data.get("passed", True) and not findings:
        return f"Vetted {kind} {target}: {scanned} files scanned, 0 findings (passed)"
    lines = [
        f"Vetting failed for {kind} {target}: {len(findings)} findings across {scanned} scanned files"
    ]
    for item in findings:
        if isinstance(item, dict):
            lines.append(
                f"  {item.get('file', '')}:{item.get('line', 1)} [{item.get('rule', 'finding')}] {item.get('message', '')}"
            )
    return "\n".join(lines)


def render_tty_output(
    command: str, data: Any, args: argparse.Namespace | None = None
) -> str:
    if command in {"tasks", "packs", "hosts", "search", "installed"}:
        return render_discovery_table(command, data)
    payload = data if isinstance(data, dict) else {}
    if command == "vet":
        return _render_vet_tty(payload)
    if command in {"preflight", "plan", "init"}:
        return _render_planning_tty(command, payload)
    if command == "triage":
        return _render_triage_tty(payload, args)
    if command in {
        "validate-run",
        "resume",
        "advance",
        "memory",
        "checkpoint",
        "report",
        "issues",
        "compare",
        "reconcile",
        "import-sarif",
        "export-sarif",
    }:
        return _render_run_management_tty(command, payload)
    if command in {"install", "update", "remove", "rollback", "recover-install"}:
        return _render_installation_tty(command, payload)
    if command in {"evaluate", "release", "verify-release"}:
        return _render_release_tty(command, payload)
    return json.dumps(data, indent=2, ensure_ascii=False)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=False,
        help="Output raw machine-readable JSON",
    )

    root = argparse.ArgumentParser(
        prog="fred.py",
        description=__doc__,
        parents=[common],
    )

    commands = root.add_subparsers(dest="command", required=True)

    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output raw machine-readable JSON",
    )

    # 1. Discovery commands
    commands.add_parser(
        "tasks",
        parents=[sub_common],
        help="List available audit recipe tasks and their target outcomes",
    )

    commands.add_parser(
        "packs",
        parents=[sub_common],
        help="List curated audit skill packs and included skills",
    )

    commands.add_parser(
        "hosts",
        parents=[sub_common],
        help="List supported agent hosts, configuration paths, and invocation formats",
    )

    sub_search = commands.add_parser(
        "search",
        parents=[sub_common],
        help="Search available audit skills by keyword, title, or topic",
    )
    sub_search.add_argument(
        "query",
        nargs="?",
        default="",
        help="Search query to match against skill metadata",
    )

    # 2. Planning commands
    pre = commands.add_parser(
        "preflight",
        parents=[sub_common],
        help="Inspect repository structure, size, and environment before planning",
    )
    pre.add_argument(
        "repo",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Path to target repository (default: current working directory)",
    )
    pre.add_argument(
        "--full",
        action="store_true",
        help="Include every recorded source-file hash",
    )

    for command in ["plan", "init"]:
        is_plan = command == "plan"
        cmd_help = (
            "Construct an audit plan without modifying repository files or executing fixes"
            if is_plan
            else "Initialize an audit run directory with an immutable baseline and task prompt"
        )
        sub = commands.add_parser(command, parents=[sub_common], help=cmd_help)
        sub.add_argument(
            "repo",
            type=Path,
            nargs="?",
            default=Path("."),
            help="Path to target repository (default: current working directory)",
        )
        sub.add_argument(
            "--task",
            required=True,
            choices=TASK_CHOICES,
            help=f"Audit recipe/task to execute ({', '.join(TASK_CHOICES)})",
        )
        sub.add_argument(
            "--mode",
            choices=["explore", "changes", "investigate", "comprehensive"],
            default="investigate",
            help="Audit mode: explore, changes, investigate, or comprehensive",
        )
        sub.add_argument(
            "--budget",
            choices=["brief", "balanced", "deep"],
            default="balanced",
            help="Execution budget level: brief, balanced, or deep",
        )
        sub.add_argument(
            "--base",
            help="Base git commit, tag, or branch for differential analysis",
        )
        sub.add_argument(
            "--profile",
            choices=PROFILE_CHOICES,
            help=f"Project archetype or technology profile name ({', '.join(PROFILE_CHOICES)})",
        )
        sub.add_argument(
            "--exclude",
            action="append",
            default=[],
            help="Path pattern to exclude from analysis (can be repeated)",
        )
        sub.add_argument(
            "--config",
            type=Path,
            help="Optional project YAML with profile, excludes, and critical workflows; never executable commands",
        )
        sub.add_argument(
            "--directory",
            default=None,
            help="Optional target subdirectory within the repository to focus tactical scope",
        )
        sub.add_argument(
            "--symbol",
            default=None,
            help="Optional symbol, function, or type name to trace blast radius and caller migration order",
        )
        sub.add_argument(
            "--allowed-paths",
            "--allowed-path",
            dest="allowed_paths",
            action="append",
            default=[],
            help="Relative path or glob allowed for edits when fixes are authorized (can be repeated)",
        )
        sub.add_argument(
            "--do-not-modify-paths",
            "--do-not-modify-path",
            dest="do_not_modify_paths",
            action="append",
            default=[],
            help="Relative path or glob that must never be modified (can be repeated)",
        )
        sub.add_argument(
            "--issue-number",
            type=int,
            default=None,
            help="Optional GitHub issue number for live read-only gh context queries",
        )
        sub.add_argument(
            "--full",
            action="store_true",
            help="Include the full source-file inventory in terminal output",
        )
        if command == "init":
            sub.add_argument(
                "--output",
                type=Path,
                required=True,
                help="Output directory path for the initialized run",
            )
            sub.add_argument(
                "--allow-fixes",
                action="store_true",
                help="Authorize proposing application fixes (default: false)",
            )
            sub.add_argument(
                "--allow-issue-publication",
                action="store_true",
                help="Authorize publishing findings to external issue trackers (default: false)",
            )
            sub.add_argument(
                "--single-task",
                action="store_true",
                help="Initialize a stateless single-task loop that emits one active skill per pass",
            )
            sub.add_argument(
                "--source-audit",
                type=Path,
                default=None,
                help="Optional existing audit.json file or run directory to seed findings/evidence into a new workflow run without mutating the source ledger",
            )

    # 3. Run Management commands
    sub_val = commands.add_parser(
        "validate-run",
        parents=[sub_common],
        help="Validate run directory structure, ledger schemas, and artifact integrity",
    )
    sub_val.add_argument("directory", type=Path, help="Path to audit run directory")

    sub_res = commands.add_parser(
        "resume",
        parents=[sub_common],
        help="Check for source drift and revalidate an in-progress audit run",
    )
    sub_res.add_argument("directory", type=Path, help="Path to audit run directory")

    sub_adv = commands.add_parser(
        "advance",
        parents=[sub_common],
        help="Record source freshness, advance run snapshots and loop pass state, and emit the next skill prompt",
    )
    sub_adv.add_argument("directory", type=Path, help="Path to run directory")
    sub_adv.add_argument(
        "--note",
        default=None,
        help="Optional single-line note recorded on the completed pass",
    )

    sub_mem = commands.add_parser(
        "memory",
        parents=[sub_common],
        help="Extract and serialize cross-run contracts and state transitions",
    )
    sub_mem.add_argument("directory", type=Path, help="Path to audit run directory")
    sub_mem.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output file path for serialized contracts",
    )

    sub_chk = commands.add_parser(
        "checkpoint",
        parents=[sub_common],
        help="Record a checkpoint state transition and operator note in the audit run",
    )
    sub_chk.add_argument("directory", type=Path, help="Path to audit run directory")
    sub_chk.add_argument(
        "--state",
        choices=["running", "paused", "completed"],
        required=True,
        help="Checkpoint execution state",
    )
    sub_chk.add_argument(
        "--note",
        required=True,
        help="Descriptive note explaining the checkpoint",
    )
    sub_chk.add_argument(
        "--completed-skill",
        "--completed-skills",
        dest="completed_skills",
        action="append",
        default=None,
        help="Planned skill completed in this session slice (can be repeated or comma-separated)",
    )

    sub_rep = commands.add_parser(
        "report",
        parents=[sub_common],
        help="Render human-readable HTML report from an audit",
    )
    sub_rep.add_argument(
        "audit",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Path to audit.json file or audit run directory (default: current working directory)",
    )
    sub_rep.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory or file path for rendered report",
    )
    sub_rep.add_argument(
        "--prior",
        type=Path,
        help="Optional prior audit.json to highlight newly introduced or resolved findings",
    )
    sub_rep.add_argument(
        "--remediation",
        type=Path,
        default=None,
        help="Optional remediation.json ledger with reconciled finding states",
    )

    sub_iss = commands.add_parser(
        "issues",
        parents=[sub_common],
        help="Generate local issue drafts for confirmed findings without remote writes",
    )
    sub_iss.add_argument(
        "audit",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Path to audit.json file or audit run directory (default: current working directory)",
    )
    sub_iss.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory path for markdown issue drafts",
    )
    sub_iss.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format: markdown drafts or github-compatible json payloads",
    )

    sub_cmp = commands.add_parser(
        "compare",
        parents=[sub_common],
        help="Compare two audit runs to detect newly introduced, resolved, or regressed findings",
    )
    sub_cmp.add_argument(
        "before",
        type=Path,
        help="Path to earlier audit.json file or audit run directory",
    )
    sub_cmp.add_argument(
        "after",
        type=Path,
        help="Path to later audit.json file or audit run directory",
    )

    sub_rec = commands.add_parser(
        "reconcile",
        parents=[sub_common],
        help="Update finding status and attach verification proof or acceptance rationale",
    )
    sub_rec.add_argument("directory", type=Path, help="Path to audit run directory")
    sub_rec.add_argument(
        "--finding", required=True, help="Finding ID to reconcile (e.g., F-1)"
    )
    sub_rec.add_argument(
        "--state",
        required=True,
        choices=[
            "open",
            "in-progress",
            "fixed-pending-verification",
            "verified-fixed",
            "regressed",
            "accepted-risk",
        ],
        help="Reconciled status for the finding",
    )
    sub_rec.add_argument(
        "--note",
        required=True,
        help="Explanation or justification for the status update",
    )
    sub_rec.add_argument(
        "--verification",
        type=Path,
        help="Path to structured verification evidence file",
    )

    sub_imp = commands.add_parser(
        "import-sarif",
        parents=[sub_common],
        help="Import static analyzer observations from a SARIF 2.1.0 file",
    )
    sub_imp.add_argument("source", type=Path, help="Path to input SARIF 2.1.0 file")
    sub_imp.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSON file path for imported observations",
    )
    sub_imp.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Optional repository root path for resolving file:// URIs",
    )

    sub_exp = commands.add_parser(
        "export-sarif",
        parents=[sub_common],
        help="Export audit findings to a standard SARIF 2.1.0 report",
    )
    sub_exp.add_argument(
        "audit",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Path to audit.json file or audit run directory (default: current working directory)",
    )
    sub_exp.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output file path for generated SARIF report",
    )
    sub_exp.add_argument(
        "--filter-refuted",
        action="store_true",
        help="Exclude refuted candidate findings from export",
    )
    sub_exp.add_argument(
        "--filter-info",
        action="store_true",
        help="Exclude informational findings from export",
    )
    sub_exp.add_argument(
        "--filter-refuted-or-info",
        action="store_true",
        help="Exclude both refuted and informational findings from export",
    )

    sub_tri = commands.add_parser(
        "triage",
        parents=[sub_common],
        help="Diagnose stalled or interrupted audit sessions from transcripts and workspace artifacts",
    )
    sub_tri.add_argument(
        "transcript",
        type=Path,
        nargs="?",
        default=None,
        help="Session transcript file (.json, .jsonl, .txt, .md)",
    )
    sub_tri.add_argument(
        "--transcript",
        type=Path,
        dest="transcript_opt",
        default=None,
        help=argparse.SUPPRESS,
    )
    sub_tri.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Optional audit run workspace directory with run.json/audit.json",
    )
    sub_tri.add_argument(
        "--workspace-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    sub_tri.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Directory to write scrubbed diagnostic_bundle.json and report",
    )
    sub_tri.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    sub_tri.add_argument(
        "--session-id", default=None, help="Explicit session or run ID"
    )
    sub_tri.add_argument(
        "--redaction-tier",
        choices=["skeleton", "evidence", "full"],
        default="full",
        help="Transcript redaction tier: skeleton, evidence, or full (default: full)",
    )
    sub_tri.add_argument(
        "--check-schema",
        action="store_true",
        help="Validate generated bundle against bundle.schema.json",
    )

    # 4. Installation commands
    sub_vet = commands.add_parser(
        "vet",
        parents=[sub_common],
        help="Statically vet a skill folder or repository agent configuration offline",
    )
    sub_vet.add_argument(
        "target",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Skill directory, configuration file, or repository root to vet (default: current working directory)",
    )
    sub_vet.add_argument(
        "--allow-capability",
        action="append",
        default=[],
        metavar="CAPABILITY",
        help="Explicit capability allowlist entry such as read:~/.claude/projects (can be repeated)",
    )

    install_helps = {
        "installed": "List installed skills, source versions, and harness paths for a target destination",
        "install": "Install audit skills or packs into an agent host environment",
        "update": "Update previously installed skills to the current version",
        "remove": "Uninstall specific skills from an agent host environment",
        "rollback": "Revert the most recent skill installation or removal operation",
        "recover-install": "Recover from an interrupted skill installation state",
    }
    for command in [
        "installed",
        "install",
        "update",
        "remove",
        "rollback",
        "recover-install",
    ]:
        sub = commands.add_parser(
            command, parents=[sub_common], help=install_helps[command]
        )
        sub.add_argument(
            "--destination",
            type=Path,
            required=False,
            default=None,
            help="Explicit project root, or home directory with --scope user (defaults to current working directory for project scope, user home directory for user scope)",
        )
        sub.add_argument(
            "--host",
            required=False,
            default=None,
            choices=HOST_CHOICES,
            help=f"Target agent host ({', '.join(HOST_CHOICES)})",
        )
        sub.add_argument(
            "--scope",
            choices=["project", "user"],
            default="project",
            help="Installation scope: project or user",
        )
        if command == "installed":
            sub.add_argument(
                "--check",
                action="store_true",
                help="Exit with code 1 when skill drift or an active operation lock exists",
            )
        if command not in {"installed", "rollback", "recover-install"}:
            skill_help = (
                "Specific skill name to install (can be repeated)"
                if command in {"install", "update"}
                else "Specific skill name to remove (can be repeated)"
            )
            sub.add_argument(
                "--skill",
                action="append",
                default=[],
                help=skill_help,
            )
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Simulate installation actions without modifying the filesystem",
            )
            sub.add_argument(
                "--force",
                action="store_true",
                help="Archive modified managed skills to backups and repair or remove drifted entries",
            )
        if command in {"install", "update"}:
            pack_help = (
                f"Skill pack to install ({', '.join(PACK_CHOICES)}; defaults to starter when --skill is omitted)"
                if command == "install"
                else f"Skill pack to install ({', '.join(PACK_CHOICES)})"
            )
            sub.add_argument(
                "--pack",
                default=None,
                choices=PACK_CHOICES,
                help=pack_help,
            )
        if command == "recover-install":
            sub.add_argument(
                "--confirm-interrupted",
                action="store_true",
                help="Confirm recovery from an interrupted installation",
            )

    # 5. Release commands
    sub_eval = commands.add_parser(
        "evaluate",
        parents=[sub_common],
        help="Evaluate skill performance and conformance against reference adapters",
    )
    sub_eval.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory path for evaluation records",
    )
    sub_eval.add_argument(
        "--adapter-argv",
        type=_parse_adapter_argv,
        required=True,
        help="JSON array of adapter command arguments (never shell interpreted)",
    )
    sub_eval.add_argument(
        "--case",
        action="append",
        help="Specific evaluation test case name (can be repeated)",
    )
    sub_eval.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of repetitions per test case",
    )
    sub_eval.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Execution timeout in seconds per evaluation run",
    )
    sub_eval.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Maximum allowed adapter token usage per evaluation run",
    )

    sub_rel = commands.add_parser(
        "release",
        parents=[sub_common],
        help="Bundle skill archives, catalogs, and manifests for distribution",
    )
    sub_rel.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory path for the generated release bundle",
    )
    sub_rel.add_argument(
        "--pack",
        default="full-audit",
        choices=PACK_CHOICES,
        help=f"Skill pack to package into release ({', '.join(PACK_CHOICES)})",
    )
    sub_rel.add_argument(
        "--all",
        action="store_true",
        help="Package all documented packs and standalone skills into unified release",
    )

    sub_vrel = commands.add_parser(
        "verify-release",
        parents=[sub_common],
        help="Verify cryptographic checksums and package integrity of a release manifest",
    )
    sub_vrel.add_argument(
        "manifest",
        type=Path,
        help="Path to manifest.json file or release directory to verify",
    )
    sub_vrel.add_argument(
        "--archive",
        action="append",
        default=None,
        help="Specific archive filename to verify against manifest.json (can be repeated)",
    )
    sub_vrel.add_argument(
        "--ignore-missing",
        action="store_true",
        help="Skip manifest archive entries that are not present on disk",
    )
    sub_vrel.add_argument(
        "--expected-version",
        default=None,
        help="Expected release version string to enforce against manifest.json",
    )
    sub_vrel.add_argument(
        "--require-licensing",
        action="store_true",
        help="Require manifest.json licensing field to declare 'See repository LICENSE'",
    )

    # Custom argument groups for root help output
    lifecycle_arg_groups = {
        name: root.add_argument_group(name) for name in LIFECYCLE_GROUPS
    }

    subcmd_to_group = {
        cmd: group_name for group_name, cmds in LIFECYCLE_GROUPS.items() for cmd in cmds
    }

    if commands in root._positionals._group_actions:
        root._positionals._group_actions.remove(commands)

    for action in commands._choices_actions:
        grp = subcmd_to_group.get(action.dest)
        if grp and grp in lifecycle_arg_groups:
            lifecycle_arg_groups[grp]._group_actions.append(action)
    return root


def concise_environment(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    snap = data["snapshot"]
    data["snapshot"] = {key: value for key, value in snap.items() if key != "files"}
    data["snapshot"]["file_count"] = len(snap["files"])
    return data


def _skill_search_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "name",
        "title",
        "description",
        "path",
        "suite",
        "phase_title",
        "maturity",
        "behavior_evaluation",
    ):
        val = row.get(key)
        if val:
            parts.append(str(val))
    for key in ("symptoms", "expected_inputs", "outcomes", "optional_tools"):
        items = row.get(key)
        if isinstance(items, list):
            parts.extend(str(item) for item in items if item)
    return " ".join(parts).casefold()


def _search_skills(query: str, root: Path = ROOT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = read_json(root / "catalog/skills.json")["skills"]
    tokens = [tok for tok in re.split(r"[\s-]+", query.casefold()) if tok]
    if not tokens:
        return rows
    recipes_path = root / "catalog/recipes.json"
    recipes = (
        read_json(recipes_path).get("recipes", {}) if recipes_path.is_file() else {}
    )
    matched_task_skills: set[str] = set()
    for task_name, recipe in recipes.items():
        task_key = task_name.casefold()
        if all(tok in task_key for tok in tokens):
            matched_task_skills.update(str(s) for s in recipe.get("skills", []))
    results: list[dict[str, Any]] = []
    for row in rows:
        if row.get("name") in matched_task_skills:
            results.append(row)
            continue
        haystack = _skill_search_text(row)
        if all(tok in haystack for tok in tokens):
            results.append(row)
    return results


def handle_catalog(a: argparse.Namespace) -> Any:
    if a.command in {"tasks", "packs", "hosts"}:
        filename = {"tasks": "recipes", "packs": "packs", "hosts": "hosts"}[a.command]
        return read_json(ROOT / "catalog" / (filename + ".json"))
    if a.command == "search":
        return _search_skills(a.query)
    raise ValueError(f"Unknown catalog command: {a.command}")


def handle_repository(a: argparse.Namespace) -> Any:
    if a.command == "preflight":
        data = repository.preflight(a.repo.resolve())
        return data if a.full else concise_environment(data)
    raise ValueError(f"Unknown repository command: {a.command}")


def _extract_run_options(a: argparse.Namespace) -> dict[str, Any]:
    return {
        key: getattr(a, key)
        for key in [
            "task",
            "mode",
            "budget",
            "base",
            "profile",
            "exclude",
            "config",
            "directory",
            "symbol",
            "allowed_paths",
            "do_not_modify_paths",
            "issue_number",
        ]
    }


def handle_runs(a: argparse.Namespace) -> Any:
    if a.command in {"plan", "init"}:
        repo = a.repo.resolve()
        options = _extract_run_options(a)
        if a.command == "plan":
            plan, preflight = runs.plan(repo, **options)
            return {
                "plan": plan,
                "preflight": preflight if a.full else concise_environment(preflight),
            }
        data = runs.initialize(
            repo,
            a.output,
            allow_fixes=a.allow_fixes,
            allow_publication=a.allow_issue_publication,
            single_task=getattr(a, "single_task", False),
            source_audit=getattr(a, "source_audit", None),
            **options,
        )
        return {
            "output": str(a.output),
            "run": data if a.full else concise_environment(data),
        }
    if a.command == "validate-run":
        errors = runs.validate_run(a.directory)
        if errors:
            raise FredCliError(
                "; ".join(errors),
                code="schema_validation_failed",
                hint="Fix the schema or identity errors in 'run.json' and the deliverable artifact, then rerun 'validate-run'.",
            )
        return {"valid": True, "semantic_claims_verified": False}
    if a.command == "resume":
        return runs.revalidate(a.directory)
    if a.command == "advance":
        return runs.advance(a.directory, note=getattr(a, "note", None))
    if a.command == "checkpoint":
        return runs.checkpoint(
            a.directory,
            a.state,
            a.note,
            completed_skills=getattr(a, "completed_skills", None),
        )
    if a.command == "memory":
        data = runs.memory(a.directory)
        atomic_json(a.output, data)
        if "contracts" in data:
            return {"output": str(a.output), "contracts": len(data["contracts"])}
        record_count = sum(
            len(data.get(key, []))
            for key in (
                "decisions",
                "boundaries",
                "components",
                "findings",
                "test_cases",
                "interview_decisions",
                "requirements",
            )
            if isinstance(data.get(key), list)
        )
        return {
            "output": str(a.output),
            "deliverable": data.get("deliverable", ""),
            "records": record_count,
        }
    if a.command == "compare":
        before_path = _resolve_audit_only_input(a.before, "compare")
        after_path = _resolve_audit_only_input(a.after, "compare")
        return runs.compare(
            read_json_object(
                before_path,
                label="Audit ledger",
                hint="Pass a valid 'audit.json' file or audit run directory.",
            ),
            read_json_object(
                after_path,
                label="Audit ledger",
                hint="Pass a valid 'audit.json' file or audit run directory.",
            ),
        )
    if a.command == "reconcile":
        return runs.reconcile(a.directory, a.finding, a.state, a.note, a.verification)
    if a.command == "import-sarif":
        data = runs.import_sarif(a.source, root=getattr(a, "root", None))
        atomic_json(a.output, data)
        return {"observations": len(data["observations"]), "output": str(a.output)}
    raise ValueError(f"Unknown runs command: {a.command}")


def handle_installation(a: argparse.Namespace) -> Any:
    if a.command == "vet":
        return vetting.vet_target(
            a.target,
            allowed_capabilities=getattr(a, "allow_capability", None),
        )
    target = installation.TargetEnvironment(a.destination, a.host, a.scope)
    if a.command == "installed":
        return installation.query_installed(target)
    if a.command in {"install", "update"}:
        selected_pack = (
            a.pack
            if (a.pack or a.skill)
            else ("starter" if a.command == "install" else None)
        )
        install_options = installation.InstallOptions(
            pack=selected_pack,
            skills=a.skill,
            update=a.command == "update",
            dry_run=a.dry_run,
            force=a.force,
        )
        return installation.install(target, install_options)
    if a.command == "remove":
        if not a.skill:
            raise ValueError(
                "Remove requires at least one --skill; there is no implicit remove-all"
            )
        remove_options = installation.RemoveOptions(
            skills=a.skill, dry_run=a.dry_run, force=a.force
        )
        return installation.remove(target, remove_options)
    if a.command == "rollback":
        rollback_options = installation.RollbackOptions(target=target)
        return installation.rollback(target, rollback_options)
    if a.command == "recover-install":
        recover_options = installation.RecoverOptions(
            confirm_interrupted=a.confirm_interrupted,
            target=target,
        )
        return installation.recover(target, recover_options)
    raise ValueError(f"Unknown installation command: {a.command}")


def _resolve_audit_only_input(target: Path, command_name: str) -> Path:
    if target.is_dir():
        runs._require_audit_directory(target, command_name)
    resolved = resolve_audit_path(target)
    if resolved.name == "run.json":
        raise FredCliError(
            f"Audit command '{command_name}' requires an 'audit.json' ledger; 'run.json' is run metadata and is not supported",
            code="unsupported_domain_artifact",
            hint="Pass an 'audit.json' ledger from a repository-audits run.",
        )
    if resolved.name != "audit.json" and (
        resolved.name in runs.DOMAIN_DELIVERABLE_SCHEMAS
        or resolved.name in WORKFLOW_DELIVERABLE_SCHEMAS
    ):
        raise FredCliError(
            f"Audit command '{command_name}' requires an 'audit.json' ledger; domain deliverable '{resolved.name}' is not supported",
            code="unsupported_domain_artifact",
            hint="Pass an 'audit.json' ledger from a repository-audits run.",
        )
    return resolved


def handle_reporting(a: argparse.Namespace) -> Any:
    if a.command == "report":
        prior_data = None
        explicit_remediation = getattr(a, "remediation", None)
        if a.prior:
            audit_path = _resolve_audit_only_input(a.audit, "report --prior")
            prior_path = _resolve_audit_only_input(a.prior, "report --prior")
            prior_data = runs._require_audit_ledger(
                read_json_object(prior_path, label="Prior audit ledger"),
                "report --prior",
            )
        elif explicit_remediation is not None:
            audit_path = _resolve_audit_only_input(a.audit, "report --remediation")
        else:
            audit_path = resolve_audit_path(a.audit)
            if (
                audit_path.name == "run.json"
                or audit_path.name in WORKFLOW_DELIVERABLE_SCHEMAS
            ):
                raise FredCliError(
                    f"Report rendering ('report') requires an 'audit.json' ledger; artifact '{audit_path.name}' is not supported",
                    code="unsupported_domain_artifact",
                    hint="Pass an 'audit.json' ledger or primary suite deliverable.",
                )
        audit_data = read_json_object(audit_path, label="Audit ledger")
        remediation_data = None
        if explicit_remediation is not None:
            remediation_data = read_json_object(
                explicit_remediation,
                label="Remediation ledger",
                hint="Pass a valid 'remediation.json' file containing a 'records' array.",
            )
        elif _detect_domain_kind(audit_data) is None and (
            a.audit.is_dir() or audit_path.name == "audit.json"
        ):
            for candidate_name in ("remediation.json", "reconciliation.json"):
                sibling_remediation = audit_path.parent / candidate_name
                if sibling_remediation.is_file():
                    remediation_data = read_json_object(
                        sibling_remediation,
                        label="Remediation ledger",
                        hint=f"Ensure '{candidate_name}' conforms to reconciliation.schema.json.",
                    )
                    break
        target = reports.render(
            audit_data,
            a.output,
            prior_data,
            remediation=remediation_data,
        )
        return {"output": str(target)}
    if a.command == "issues":
        audit_path = _resolve_audit_only_input(a.audit, "issues")
        return {
            "drafts": reports.issue_drafts(
                read_json_object(audit_path, label="Audit ledger"),
                a.output,
                format=a.format,
            ),
            "remote_writes": 0,
        }
    if a.command == "export-sarif":
        from audit_to_sarif import convert_file

        audit_path = _resolve_audit_only_input(a.audit, "export-sarif")
        data = convert_file(
            audit_path,
            a.output,
            filter_refuted=a.filter_refuted or a.filter_refuted_or_info,
            filter_info=a.filter_info or a.filter_refuted_or_info,
        )
        return {"results": len(data["runs"][0]["results"]), "output": str(a.output)}
    raise ValueError(f"Unknown reporting command: {a.command}")


def _validate_adapter_argv(adapter_argv: Any) -> list[str]:
    argv = adapter_argv if isinstance(adapter_argv, list) else json.loads(adapter_argv)
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(arg, str) for arg in argv)
    ):
        raise ValueError("Adapter argv must be a nonempty JSON array of strings")
    return argv


def handle_release(a: argparse.Namespace) -> Any:
    if a.command == "evaluate":
        argv = _validate_adapter_argv(a.adapter_argv)
        return evaluations.evaluate(
            a.output,
            argv,
            a.case,
            a.repeats,
            a.timeout,
            max_tokens=a.max_tokens,
        )
    if a.command == "release":
        return releases.build(a.output, a.pack, all_packs=a.all)
    if a.command == "verify-release":
        return releases.verify(
            a.manifest,
            archive=getattr(a, "archive", None),
            ignore_missing=bool(getattr(a, "ignore_missing", False)),
            expected_version=getattr(a, "expected_version", None),
            require_licensing=bool(getattr(a, "require_licensing", False)),
        )
    raise ValueError(f"Unknown release command: {a.command}")


def _load_triage_module() -> Any:
    import importlib.util

    script_path = ROOT / "skills/repo-audit-diagnostics/scripts/triage.py"
    if not script_path.is_file():
        raise ValueError(f"Triage script not found at {script_path}")
    spec = importlib.util.spec_from_file_location("diagnostics_triage", script_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to load triage module from {script_path}")
    triage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(triage)
    return triage


def handle_diagnostics(a: argparse.Namespace) -> Any:
    if a.command != "triage":
        raise ValueError(f"Unknown diagnostics command: {a.command}")

    workspace = a.workspace or a.workspace_dir
    output_dir = a.output or a.output_dir

    triage = _load_triage_module()

    transcript = a.transcript or a.transcript_opt
    discovery_meta = None
    if not transcript:
        if workspace and workspace.is_dir():
            discovery_meta = triage.discover_transcript_details(
                workspace, session_id=a.session_id
            )
            transcript = Path(discovery_meta["selected_transcript"])
            print(f"Discovered transcript: {transcript}", file=sys.stderr)
        elif workspace:
            raise ValueError(f"Workspace directory not found: {workspace}")
        elif a.session_id:
            discovery_meta = triage.discover_transcript_details(
                None, session_id=a.session_id
            )
            transcript = Path(discovery_meta["selected_transcript"])
            print(f"Discovered transcript: {transcript}", file=sys.stderr)
        else:
            raise ValueError("Transcript file path is required")
    if not transcript.is_file():
        raise ValueError(f"Transcript file not found: {transcript}")

    redaction_tier = getattr(a, "redaction_tier", "full") or "full"
    bundle = triage.generate_diagnostic_bundle(
        transcript,
        workspace,
        a.session_id,
        redaction_tier=redaction_tier,
        discovery_metadata=discovery_meta,
    )
    if a.check_schema:
        triage.validate_bundle(bundle)
    if output_dir:
        triage.export_bundle(bundle, output_dir)
    return bundle


COMMAND_REGISTRY: dict[str, Callable[[argparse.Namespace], Any]] = {
    # Discovery
    "tasks": handle_catalog,
    "packs": handle_catalog,
    "hosts": handle_catalog,
    "search": handle_catalog,
    # Planning
    "preflight": handle_repository,
    "plan": handle_runs,
    "init": handle_runs,
    # Run Management
    "validate-run": handle_runs,
    "resume": handle_runs,
    "advance": handle_runs,
    "checkpoint": handle_runs,
    "memory": handle_runs,
    "compare": handle_runs,
    "reconcile": handle_runs,
    "import-sarif": handle_runs,
    # Installation
    "vet": handle_installation,
    "installed": handle_installation,
    "install": handle_installation,
    "update": handle_installation,
    "remove": handle_installation,
    "rollback": handle_installation,
    "recover-install": handle_installation,
    # Reporting
    "report": handle_reporting,
    "issues": handle_reporting,
    "export-sarif": handle_reporting,
    # Release
    "evaluate": handle_release,
    "release": handle_release,
    "verify-release": handle_release,
    # Diagnostics
    "triage": handle_diagnostics,
}

DISPATCH_REGISTRY: dict[str, Callable[[argparse.Namespace], Any]] = COMMAND_REGISTRY


def dispatch(a: argparse.Namespace) -> Any:
    command = getattr(a, "command", None)
    handler = COMMAND_REGISTRY.get(command) if isinstance(command, str) else None
    if handler is None:
        raise ValueError("Unknown command")
    return handler(a)


def _to_cli_error(exc: BaseException) -> FredCliError:
    if isinstance(exc, FredCliError):
        return exc
    if isinstance(exc, subprocess.TimeoutExpired):
        cmd_display = (
            " ".join(str(part) for part in exc.cmd)
            if isinstance(exc.cmd, (list, tuple))
            else str(exc.cmd)
        )
        return FredCliError(
            f"Subprocess timed out after {exc.timeout:g}s: {cmd_display}",
            code="subprocess_timeout",
            hint="Retry once background filesystem locks or stalled child processes clear.",
        )
    if isinstance(exc, json.JSONDecodeError):
        return FredCliError(
            f"Malformed JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}",
            code="invalid_json",
            hint="Fix the JSON syntax and rerun the command.",
        )
    if isinstance(exc, FileNotFoundError):
        target = exc.filename or str(exc)
        return FredCliError(
            f"File or directory not found: {target}",
            code="missing_file",
            hint="Verify that the path exists and check its spelling.",
        )
    if isinstance(exc, IsADirectoryError):
        target = exc.filename or str(exc)
        return FredCliError(
            f"Expected a file, got directory: {target}",
            code="invalid_path_type",
            hint="Pass a file path or a subcommand-supported directory.",
        )
    if isinstance(exc, OSError):
        return FredCliError(
            str(exc),
            code="io_error",
            hint="Check filesystem paths and permissions, then retry.",
        )
    return FredCliError(
        str(exc),
        code="cli_error",
        hint="Run with '--help' to review expected arguments and artifact contracts.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
    except (
        FredCliError,
        subprocess.TimeoutExpired,
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
    ) as exc:
        cli_err = _to_cli_error(exc)
        if getattr(args, "as_json", False):
            print(
                json.dumps(cli_err.to_dict(), indent=2, ensure_ascii=False),
                file=sys.stderr,
            )
        else:
            print(f"fred-skills: {cli_err.message}", file=sys.stderr)
            if cli_err.hint:
                print(f"Hint: {cli_err.hint}", file=sys.stderr)
        return 1
    if sys.stdout.isatty() and not getattr(args, "as_json", False):
        print(render_tty_output(args.command, result, args))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    if (
        args.command == "installed"
        and getattr(args, "check", False)
        and isinstance(result, dict)
        and not result.get("healthy", True)
    ):
        return 1
    if (
        args.command == "vet"
        and isinstance(result, dict)
        and not result.get("passed", True)
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

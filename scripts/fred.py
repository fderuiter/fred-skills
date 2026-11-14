#!/usr/bin/env python3
"""Local discovery, installation, run management, and validation for fred-skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
from fredlib import evaluations, installation, releases, reports, repository, runs
from fredlib.common import ROOT, atomic_json, read_json

HOST_CHOICES = sorted(read_json(ROOT / "catalog/hosts.json")["hosts"].keys())
TASK_CHOICES = sorted(read_json(ROOT / "catalog/recipes.json")["recipes"].keys())
PACK_CHOICES = sorted(read_json(ROOT / "catalog/packs.json")["packs"].keys())

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
        "memory",
        "checkpoint",
        "report",
        "issues",
        "compare",
        "reconcile",
        "import-sarif",
        "export-sarif",
    ],
    "Installation": [
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


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "No entries found."
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator_line = "  ".join("-" * widths[i] for i in range(len(headers)))
    data_lines = [
        "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, separator_line, *data_lines])


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
    return ""


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
    pre.add_argument("repo", type=Path, help="Path to target repository")
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
        sub.add_argument("repo", type=Path, help="Path to target repository")
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
            help="Project archetype or technology profile name",
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

    sub_rep = commands.add_parser(
        "report",
        parents=[sub_common],
        help="Render human-readable HTML and Markdown summary reports from an audit",
    )
    sub_rep.add_argument("audit", type=Path, help="Path to audit.json file")
    sub_rep.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory path for rendered reports",
    )
    sub_rep.add_argument(
        "--prior",
        type=Path,
        help="Optional prior audit.json to highlight newly introduced or resolved findings",
    )

    sub_iss = commands.add_parser(
        "issues",
        parents=[sub_common],
        help="Generate local issue drafts for confirmed findings without remote writes",
    )
    sub_iss.add_argument("audit", type=Path, help="Path to audit.json file")
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
    sub_cmp.add_argument("before", type=Path, help="Path to earlier audit.json file")
    sub_cmp.add_argument("after", type=Path, help="Path to later audit.json file")

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

    sub_exp = commands.add_parser(
        "export-sarif",
        parents=[sub_common],
        help="Export audit findings to a standard SARIF 2.1.0 report",
    )
    sub_exp.add_argument("audit", type=Path, help="Path to audit.json file")
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

    # 4. Installation commands
    install_helps = {
        "install": "Install audit skills or packs into an agent host environment",
        "update": "Update previously installed skills to the current version",
        "remove": "Uninstall specific skills from an agent host environment",
        "rollback": "Revert the most recent skill installation or removal operation",
        "recover-install": "Recover from an interrupted skill installation state",
    }
    for command in ["install", "update", "remove", "rollback", "recover-install"]:
        sub = commands.add_parser(
            command, parents=[sub_common], help=install_helps[command]
        )
        sub.add_argument(
            "--destination",
            type=Path,
            required=True,
            help="Explicit project root, or home directory with --scope user",
        )
        sub.add_argument(
            "--host",
            required=True,
            choices=HOST_CHOICES,
            help=f"Target agent host ({', '.join(HOST_CHOICES)})",
        )
        sub.add_argument(
            "--scope",
            choices=["project", "user"],
            default="project",
            help="Installation scope: project or user",
        )
        if command not in {"rollback", "recover-install"}:
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
        if command in {"install", "update"}:
            sub.add_argument(
                "--pack",
                choices=PACK_CHOICES,
                help=f"Skill pack to install ({', '.join(PACK_CHOICES)})",
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

    sub_vrel = commands.add_parser(
        "verify-release",
        parents=[sub_common],
        help="Verify cryptographic checksums and package integrity of a release manifest",
    )
    sub_vrel.add_argument(
        "manifest",
        type=Path,
        help="Path to manifest.json file to verify",
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


def dispatch(a: argparse.Namespace) -> Any:
    if a.command in {"tasks", "packs", "hosts"}:
        filename = {"tasks": "recipes", "packs": "packs", "hosts": "hosts"}[a.command]
        return read_json(ROOT / "catalog" / (filename + ".json"))
    if a.command == "search":
        rows = read_json(ROOT / "catalog/skills.json")["skills"]
        query = a.query.casefold()
        return [
            row
            for row in rows
            if query in json.dumps(row, ensure_ascii=False).casefold()
        ]
    if a.command == "preflight":
        data = repository.preflight(a.repo)
        return data if a.full else concise_environment(data)
    if a.command in {"plan", "init"}:
        options = {
            key: getattr(a, key)
            for key in [
                "task",
                "mode",
                "budget",
                "base",
                "profile",
                "exclude",
                "config",
            ]
        }
        if a.command == "plan":
            plan, preflight = runs.plan(a.repo, **options)
            return {
                "plan": plan,
                "preflight": preflight if a.full else concise_environment(preflight),
            }
        data = runs.initialize(
            a.repo,
            a.output,
            allow_fixes=a.allow_fixes,
            allow_publication=a.allow_issue_publication,
            **options,
        )
        return {
            "output": str(a.output),
            "run": data if a.full else concise_environment(data),
        }
    if a.command == "validate-run":
        errors = runs.validate_run(a.directory)
        if errors:
            raise ValueError("; ".join(errors))
        return {"valid": True, "semantic_claims_verified": False}
    if a.command == "resume":
        return runs.revalidate(a.directory)
    if a.command == "checkpoint":
        return runs.checkpoint(a.directory, a.state, a.note)
    if a.command == "memory":
        data = runs.memory(a.directory)
        atomic_json(a.output, data)
        return {"output": str(a.output), "contracts": len(data["contracts"])}
    if a.command == "report":
        reports.render(
            read_json(a.audit), a.output, read_json(a.prior) if a.prior else None
        )
        return {"output": str(a.output)}
    if a.command == "issues":
        return {
            "drafts": reports.issue_drafts(
                read_json(a.audit), a.output, format=a.format
            ),
            "remote_writes": 0,
        }
    if a.command == "compare":
        return runs.compare(read_json(a.before), read_json(a.after))
    if a.command == "reconcile":
        return runs.reconcile(a.directory, a.finding, a.state, a.note, a.verification)
    if a.command == "import-sarif":
        data = runs.import_sarif(a.source)
        atomic_json(a.output, data)
        return {"observations": len(data["observations"]), "output": str(a.output)}
    if a.command == "export-sarif":
        from audit_to_sarif import convert_file

        data = convert_file(
            a.audit,
            a.output,
            filter_refuted=a.filter_refuted or a.filter_refuted_or_info,
            filter_info=a.filter_info or a.filter_refuted_or_info,
        )
        return {"results": len(data["runs"][0]["results"]), "output": str(a.output)}
    if a.command in {"install", "update"}:
        return installation.install(
            a.destination,
            a.host,
            a.scope,
            a.pack,
            a.skill,
            a.command == "update",
            a.dry_run,
        )
    if a.command == "remove":
        if not a.skill:
            raise ValueError(
                "Remove requires at least one --skill; there is no implicit remove-all"
            )
        return installation.remove(a.destination, a.host, a.scope, a.skill, a.dry_run)
    if a.command == "rollback":
        return installation.rollback(a.destination, a.host, a.scope)
    if a.command == "recover-install":
        return installation.recover(
            a.destination, a.host, a.scope, a.confirm_interrupted
        )
    if a.command == "evaluate":
        argv = (
            a.adapter_argv
            if isinstance(a.adapter_argv, list)
            else json.loads(a.adapter_argv)
        )
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) for arg in argv)
        ):
            raise ValueError("Adapter argv must be a nonempty JSON array of strings")
        return evaluations.evaluate(a.output, argv, a.case, a.repeats, a.timeout)
    if a.command == "release":
        return releases.build(a.output, a.pack)
    if a.command == "verify-release":
        return releases.verify(a.manifest)
    raise ValueError("Unknown command")


def main() -> int:
    args = parser().parse_args()
    try:
        result = dispatch(args)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"fred-skills: {exc}", file=sys.stderr)
        return 1
    if (
        args.command in {"tasks", "packs", "hosts", "search"}
        and sys.stdout.isatty()
        and not getattr(args, "as_json", False)
    ):
        print(render_discovery_table(args.command, result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

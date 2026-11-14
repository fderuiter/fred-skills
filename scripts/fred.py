#!/usr/bin/env python3
"""Local discovery, installation, run management, and validation for fred-skills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from fredlib import evaluations, installation, releases, reports, repository, runs
from fredlib.common import ROOT, atomic_json, read_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for command in ["tasks", "packs", "hosts", "search"]:
        sub = commands.add_parser(command)
        if command == "search":
            sub.add_argument("query", nargs="?", default="")
    pre = commands.add_parser("preflight")
    pre.add_argument("repo", type=Path)
    pre.add_argument(
        "--full", action="store_true", help="Include every recorded source-file hash"
    )
    for command in ["plan", "init"]:
        sub = commands.add_parser(command)
        sub.add_argument("repo", type=Path)
        sub.add_argument("--task", required=True)
        sub.add_argument(
            "--mode",
            choices=["explore", "changes", "investigate", "comprehensive"],
            default="investigate",
        )
        sub.add_argument(
            "--budget", choices=["brief", "balanced", "deep"], default="balanced"
        )
        sub.add_argument("--base")
        sub.add_argument("--profile")
        sub.add_argument("--exclude", action="append", default=[])
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
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--allow-fixes", action="store_true")
            sub.add_argument("--allow-issue-publication", action="store_true")
    for command in ["validate-run", "resume", "memory"]:
        sub = commands.add_parser(command)
        sub.add_argument("directory", type=Path)
        if command == "memory":
            sub.add_argument("--output", type=Path, required=True)
    sub = commands.add_parser("checkpoint")
    sub.add_argument("directory", type=Path)
    sub.add_argument(
        "--state", choices=["running", "paused", "completed"], required=True
    )
    sub.add_argument("--note", required=True)
    for command in ["report", "issues"]:
        sub = commands.add_parser(command)
        sub.add_argument("audit", type=Path)
        sub.add_argument("--output", type=Path, required=True)
        if command == "report":
            sub.add_argument("--prior", type=Path)
    sub = commands.add_parser("compare")
    sub.add_argument("before", type=Path)
    sub.add_argument("after", type=Path)
    sub = commands.add_parser("reconcile")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--finding", required=True)
    sub.add_argument(
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
    )
    sub.add_argument("--note", required=True)
    sub.add_argument("--verification", type=Path)
    sub = commands.add_parser("import-sarif")
    sub.add_argument("source", type=Path)
    sub.add_argument("--output", type=Path, required=True)
    sub = commands.add_parser("export-sarif")
    sub.add_argument("audit", type=Path)
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--filter-refuted", action="store_true")
    sub.add_argument("--filter-info", action="store_true")
    sub.add_argument("--filter-refuted-or-info", action="store_true")
    for command in ["install", "update", "remove", "rollback", "recover-install"]:
        sub = commands.add_parser(command)
        sub.add_argument(
            "--destination",
            type=Path,
            required=True,
            help="Explicit project root, or home directory with --scope user",
        )
        sub.add_argument("--host", required=False, default=None)
        sub.add_argument("--scope", choices=["project", "user"], default="project")
        if command not in {"rollback", "recover-install"}:
            sub.add_argument("--skill", action="append", default=[])
            sub.add_argument("--dry-run", action="store_true")
        if command in {"install", "update"}:
            sub.add_argument("--pack")
        if command == "recover-install":
            sub.add_argument("--confirm-interrupted", action="store_true")
    sub = commands.add_parser("evaluate")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument(
        "--adapter-argv",
        required=True,
        help="JSON array, with optional {prompt_file}, {output_file}, {case_dir} placeholders; never interpreted by a shell",
    )
    sub.add_argument("--case", action="append")
    sub.add_argument("--repeats", type=int, default=1)
    sub.add_argument("--timeout", type=int, default=120)
    sub = commands.add_parser("release")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--pack", default="full-audit")
    sub = commands.add_parser("verify-release")
    sub.add_argument("manifest", type=Path)
    return root


def concise_environment(data):
    data = dict(data)
    snap = data["snapshot"]
    data["snapshot"] = {key: value for key, value in snap.items() if key != "files"}
    data["snapshot"]["file_count"] = len(snap["files"])
    return data


def dispatch(a):
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
            "drafts": reports.issue_drafts(read_json(a.audit), a.output),
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
        argv = json.loads(a.adapter_argv)
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
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"fred-skills: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

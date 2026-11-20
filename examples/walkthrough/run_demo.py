"""Run the local synthetic before/after example; does not audit a real application."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import reports, runs
from fredlib.common import FredCliError, atomic_json, read_json

DEFAULT_DEMO_TIMEOUT = 30


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _run_walkthrough(output_dir: Path) -> None:
    source = Path(__file__).parent / "source"
    run = runs.initialize(source, output_dir, "investigate-bug", budget="brief")
    command = ["node", str(Path(__file__).parent / "check.mjs")]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=DEFAULT_DEMO_TIMEOUT,
    )
    proof = json.loads(result.stdout)
    proof.update(
        before_revision="sha256:" + run["snapshot"]["files"]["session.mjs"],
        after_revision="sha256:" + run["snapshot"]["files"]["corrected.mjs"],
        commands=["node examples/walkthrough/check.mjs"],
    )
    audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
    audit["run_id"], audit["repository"] = (
        run["run_id"],
        runs.empty_audit(run)["repository"],
    )
    audit["evidence"][0].update(
        line_start=5,
        line_end=11,
        context="Synthetic Session.start/cancel; exact snapshot hashes recorded in run.json",
    )
    finding = audit["findings"][0]
    finding.update(
        status="confirmed",
        validation={
            "state": "executed",
            "commands": proof["commands"],
            "result": proof["result"],
        },
    )
    audit["limitations"] = [
        "Published synthetic demonstration; not an agent audit of a user application. The original fixture intentionally retains the defect; corrected.mjs is a separate demonstrated correction."
    ]
    audit["coverage"] = [
        {
            "topic": "concurrency/cancellation",
            "scope": "synthetic publication contract",
            "status": "inspected",
            "evidence_ids": ["E-1"],
            "notes": "Both before/after variants and recorded safe controls executed",
            "next_action": "Use an agent on a real bounded workflow before generalizing.",
        }
    ]
    audit["checks"] = [
        {
            "name": "Synthetic original and corrected controls",
            "command": proof["commands"][0],
            "state": "executed",
            "result": proof["result"],
        }
    ]
    audit["issues"] = [
        {
            "id": "I-1",
            "title": "Fence publication by current operation ownership",
            "finding_ids": ["F-1"],
            "depends_on": [],
            "related_issues": [],
            "existing_url": None,
            "publication": "draft",
        }
    ]
    atomic_json(output_dir / "audit.json", audit)
    atomic_json(output_dir / "verification.json", proof)
    runs.reconcile(
        output_dir,
        "F-1",
        "verified-fixed",
        "Verified the separate corrected synthetic variant; no application code was changed.",
        output_dir / "verification.json",
    )
    remediation = read_json(output_dir / "remediation.json")
    reports.render(audit, output_dir / "report.html", remediation=remediation)
    reports.issue_drafts(audit, output_dir / "issues")
    errors = runs.validate_run(output_dir)
    if errors:
        raise SystemExit("; ".join(errors))
    runs.checkpoint(
        output_dir,
        "completed",
        "Synthetic walkthrough complete; real-world audit behavior remains to be evaluated.",
    )
    print(
        json.dumps(
            {
                "output": str(output_dir),
                "validation": "passed",
                "kind": "synthetic demonstration; no agent invoked",
            }
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _run_walkthrough(args.output)
    except FredCliError as exc:
        print(f"Error [{exc.code}]: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"Hint: {exc.hint}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        cmd_display = (
            " ".join(str(part) for part in exc.cmd)
            if isinstance(exc.cmd, (list, tuple))
            else str(exc.cmd)
        )
        print(
            f"Error [subprocess_timeout]: Command timed out after {exc.timeout:g}s: {cmd_display}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

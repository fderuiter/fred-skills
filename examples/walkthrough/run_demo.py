"""Run the local synthetic before/after example; does not audit a real application."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import reports, runs
from fredlib.common import atomic_json, read_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).parent / "source"
run = runs.initialize(source, args.output, "investigate-bug", budget="brief")
command = ["node", str(Path(__file__).parent / "check.mjs")]
result = subprocess.run(command, check=True, capture_output=True, text=True)
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
atomic_json(args.output / "audit.json", audit)
atomic_json(args.output / "verification.json", proof)
runs.reconcile(
    args.output,
    "F-1",
    "verified-fixed",
    "Verified the separate corrected synthetic variant; no application code was changed.",
    args.output / "verification.json",
)
reports.render(audit, args.output / "report.html")
reports.issue_drafts(audit, args.output / "issues")
errors = runs.validate_run(args.output)
if errors:
    raise SystemExit("; ".join(errors))
runs.checkpoint(
    args.output,
    "completed",
    "Synthetic walkthrough complete; real-world audit behavior remains to be evaluated.",
)
print(
    json.dumps(
        {
            "output": str(args.output),
            "validation": "passed",
            "kind": "synthetic demonstration; no agent invoked",
        }
    )
)

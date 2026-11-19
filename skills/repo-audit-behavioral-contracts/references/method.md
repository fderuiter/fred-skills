# Behavioral Contract Reconstruction: Specialist Method and Examples

## Reconstructing Actor-to-Storage Guarantees

1. Trace one user-triggered or webhook-triggered command from its ingress handler to durable commits and outbound notifications, listing every promise made in API types, docstrings, HTTP status codes, and test assertions.
2. Compare the independently documented promise (`intended`) against the actual guard in the authoritative write path (`observed`), flagging places where callers enforce preconditions that sibling entry points skip.
3. Separate intentional eventual-consistency windows (such as asynchronous search indexing with explicit version reconciliation) from silent contract gaps where superseded operations emit completion events.

## Failure Trace vs. Safe Control: Superseded Job Emitting Live Completion

```typescript
// Defect trace: startExport replaces activeJobId, yet the old worker callback still emits "export:ready"
class ExportCoordinator {
  private activeJobId = "";
  async startExport(jobId: string, runWorker: () => Promise<string>) {
    this.activeJobId = jobId;
    const downloadUrl = await runWorker();
    this.bus.emit("export:ready", { jobId, downloadUrl }); // unfenced event publication!
  }
}

// Safe control: check authoritative ownership immediately before external event publication
class FencedExportCoordinator {
  private activeJobId = "";
  async startExport(jobId: string, runWorker: () => Promise<string>) {
    this.activeJobId = jobId;
    const downloadUrl = await runWorker();
    if (this.activeJobId !== jobId) return;
    this.bus.emit("export:ready", { jobId, downloadUrl });
  }
}
```

## Behavioral-Contracts `audit.json` Deliverable Example

Validate this export-coordinator ledger locally using `python3 <this-skill>/scripts/validate_audit.py behavioral-contracts-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "behavioral-contracts-sample",
  "repository": {
    "identity": "acme/export-service",
    "revision": "a10bc42",
    "dirty": false,
    "snapshot": "git-tree:a10bc42"
  },
  "scope": [
    "src/exports/coordinator.ts"
  ],
  "coverage": [
    {
      "topic": "behavioral-contracts",
      "scope": "src/exports/coordinator.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-101"
      ],
      "notes": "Mapped startExport entry point, worker completion promise, and event bus subscribers.",
      "next_action": "Hand off overlapping worker cancellation details to repo-audit-cancellation if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-101",
      "dimension": "behavioral-contracts",
      "scope": "ExportCoordinator.startExport",
      "intended": "Only the active export job publishes export:ready to downstream UI listeners.",
      "observed": "Superseded startExport invocations emit export:ready unconditionally upon worker resolution.",
      "enforcement": "none on bus.emit in src/exports/coordinator.ts",
      "evidence_ids": [
        "E-101"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-101",
      "path": "src/exports/coordinator.ts",
      "line_start": 14,
      "line_end": 21,
      "symbol": "ExportCoordinator.startExport",
      "context": "Updates this.activeJobId before await runWorker() but omits activeJobId check before bus.emit."
    }
  ],
  "findings": [
    {
      "id": "F-101",
      "topic": "behavioral-contracts",
      "tags": [
        "concurrency"
      ],
      "title": "Superseded export job publishes export:ready into newer session workflow",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-101"
      ],
      "evidence_ids": [
        "E-101"
      ],
      "preconditions": "User triggers a second export with updated filters before the first export worker finishes.",
      "sequence": [
        "startExport('job-1') sets activeJobId = 'job-1' and suspends at await runWorker().",
        "startExport('job-2') overwrites activeJobId = 'job-2'.",
        "job-1 worker resolves and emits export:ready with stale job-1 URL."
      ],
      "expected": "Superseded job-1 completion is suppressed once activeJobId !== 'job-1'.",
      "actual": "Listeners receive export:ready for superseded job-1.",
      "impact": "Subscriber downloads stale unfiltered export archive.",
      "root_cause": "Event publication lacks post-await ownership check against activeJobId.",
      "counterevidence": "Single-export flows without replacement emit expected URL.",
      "remedy": "Check this.activeJobId === jobId immediately prior to bus.emit.",
      "acceptance": [
        "Overlapping startExport('job-1') and startExport('job-2') emit export:ready only for job-2."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace verified across ExportCoordinator and subscriber handlers."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

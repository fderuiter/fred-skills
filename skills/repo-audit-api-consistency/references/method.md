# API Design Consistency: Specialist Method and Examples

## Comparing Peer Operations Across Completion, Pagination, and Error Contracts

1. Group sibling endpoints, SDK methods, and CLI subcommands that expose parallel operations (`deleteProject` vs. `deleteDataset`, `listUsers` vs. `listTeams`, `updateWebhook` vs. `updateIntegration`) and compare their completion guarantees, HTTP status codes, nullability, and error shapes.
2. Trace callers that handle peer operations polymorphically—such as teardown scripts or generic retry middleware—where a hidden semantic mismatch causes premature resource cleanup or unhandled exceptions.
3. Focus on behavioral contract inconsistencies that mislead callers into bugs rather than purely cosmetic naming preferences.

## Failure Trace vs. Safe Control: Synchronous `200` Delete vs. Undocumented Async-Enqueue `200` Delete

```typescript
// Defect trace: deleteProject awaits durable S3 purge before 200 OK, whereas deleteDataset returns 200 OK on queue enqueue!
// Shared tenant offboarding caller immediately deletes KMS key after both return 200, stranding queued dataset purge with AccessDenied.
async function deleteDatasetBuggy(req: Request, res: Response) {
  await purgeQueue.enqueue({ datasetId: req.params.id });
  return res.status(200).json({ deleted: true }); // misleading synchronous-completion contract!
}

// Safe control: return 202 Accepted with pollable operation handle (or await durable purge to match peer 200 DELETE contract)
async function deleteDatasetSafe(req: Request, res: Response) {
  const op = await purgeQueue.enqueue({ datasetId: req.params.id });
  return res.status(202).location(`/v1/operations/${op.id}`).json({ status: "pending", operationId: op.id });
}
```

## API-Consistency `audit.json` Deliverable Example

Run `python3 <this-skill>/scripts/validate_audit.py api-consistency-audit.json` to check conformance with `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "api-consistency-sample",
  "repository": {
    "identity": "acme/data-platform-api",
    "revision": "51cd73f",
    "dirty": false,
    "snapshot": "git-tree:51cd73f"
  },
  "scope": [
    "src/routes/projects.ts",
    "src/routes/datasets.ts",
    "src/offboarding/decommission.ts"
  ],
  "coverage": [
    {
      "topic": "api-consistency",
      "scope": "src/routes/datasets.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-234",
        "E-235"
      ],
      "notes": "Compared DELETE /v1/projects/:id and DELETE /v1/datasets/:id completion semantics across decommission caller.",
      "next_action": "Hand off background purge retry behavior to repo-audit-lifecycle if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-234",
      "dimension": "api-consistency",
      "scope": "DELETE /v1/* resource deletion completion contract",
      "intended": "HTTP 200 { deleted: true } signifies storage artifacts were durably purged; async purges return 202 with operationId.",
      "observed": "DELETE /v1/projects/:id purges synchronously before 200, whereas DELETE /v1/datasets/:id enqueues background job and returns 200 { deleted: true }.",
      "enforcement": "contradictory status/body semantics in src/routes/datasets.ts",
      "evidence_ids": [
        "E-234",
        "E-235"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-234",
      "path": "src/routes/datasets.ts",
      "line_start": 61,
      "line_end": 67,
      "symbol": "deleteDataset",
      "context": "Enqueues background purge job and immediately returns HTTP 200 { deleted: true }."
    },
    {
      "id": "E-235",
      "path": "src/offboarding/decommission.ts",
      "line_start": 29,
      "line_end": 39,
      "symbol": "decommissionTenant",
      "context": "Awaits apiClient.deleteProject and apiClient.deleteDataset, then immediately schedules KMS key destruction."
    }
  ],
  "findings": [
    {
      "id": "F-234",
      "topic": "api-consistency",
      "tags": [
        "api-design",
        "temporal-coupling"
      ],
      "title": "DELETE /v1/datasets/:id returns synchronous 200 { deleted: true } before async storage purge runs",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-234"
      ],
      "evidence_ids": [
        "E-234",
        "E-235"
      ],
      "preconditions": "decommissionTenant deletes datasets and revokes tenant IAM/KMS credentials immediately upon 200 response.",
      "sequence": [
        "decommissionTenant calls DELETE /v1/datasets/ds-8 and receives 200 { deleted: true }.",
        "decommissionTenant revokes tenant storage role assuming purge finished like DELETE /v1/projects/:id.",
        "Background purge worker picks up ds-8 5 seconds later and fails permanently with AccessDenied."
      ],
      "expected": "Async deletion returns 202 Accepted with pollable operationId so decommissionTenant waits before revoking credentials.",
      "actual": "Caller treats 200 { deleted: true } as durable completion and orphans encrypted S3 objects.",
      "impact": "Offboarded tenant objects remain orphaned in storage buckets after credential revocation.",
      "root_cause": "Peer DELETE endpoints used identical 200 { deleted: true } responses for opposite sync vs. async completion semantics.",
      "counterevidence": "Interactive UI delete button ignores completion timing because KMS credentials stay active.",
      "remedy": "Return 202 Accepted with operation URL from deleteDataset and await operation completion in decommissionTenant.",
      "acceptance": [
        "decommissionTenant polls 202 operation until succeeded before revoking tenant storage credentials."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Traced status handling across projects.ts, datasets.ts, and decommission.ts."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

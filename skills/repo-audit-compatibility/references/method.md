# Compatibility and Migration Contracts: Specialist Method and Examples

## Auditing Mixed-Version Deployments, Rollbacks, and Stored State

1. Identify every persisted artifact that outlives a single process deploy: SQL tables, browser IndexedDB/localStorage blobs, queued background job payloads, session tokens, and rolling-deploy RPC peers.
2. Trace mixed-version coexistence (`v1` reader reading `v2`-written record during canary rollout or rollback) and check whether unknown versions fail closed or preserve unknown fields instead of silently dropping data.
3. Recognize safe expand-migrate-contract rollouts and explicit `schema_version` gates that reject unsupported newer formats cleanly without corrupting stored records.

## Failure Trace vs. Safe Control: Read-Modify-Write Stripping Newer Schema Fields During Canary

```typescript
// Defect trace: v1 worker deserializes v2 job record, drops unknown v2 fields, and overwrites row on retry update
interface JobRecordV1 {
  id: string;
  attempts: number;
}

async function incrementAttemptBuggy(store: JobStore, id: string) {
  const raw = await store.getJson(id);
  const parsed: JobRecordV1 = { id: raw.id, attempts: raw.attempts + 1 };
  await store.putJson(id, parsed); // erases v2 fields such as idempotencyToken and tenantShard!
}

// Safe control: enforce explicit schema_version guard and preserve unknown fields on update
async function incrementAttemptSafe(store: JobStore, id: string) {
  const raw = await store.getJson(id);
  if ((raw.schemaVersion ?? 1) > 1) {
    throw new UnsupportedSchemaVersionError(raw.schemaVersion);
  }
  await store.putJson(id, { ...raw, attempts: raw.attempts + 1 });
}
```

## Compatibility `audit.json` Output Example

Run `python3 <this-skill>/scripts/validate_audit.py compatibility-audit.json` to check this migration ledger against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "compatibility-sample",
  "repository": {
    "identity": "acme/job-runner",
    "revision": "90de62a",
    "dirty": false,
    "snapshot": "git-tree:90de62a"
  },
  "scope": [
    "src/jobs/retry_store.ts"
  ],
  "coverage": [
    {
      "topic": "compatibility",
      "scope": "src/jobs/retry_store.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-221"
      ],
      "notes": "Inspected rolling-deploy coexistence between v1 and v2 workers sharing Redis job JSON blobs.",
      "next_action": "Hand off wire payload framing checks to repo-audit-protocol-drift if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-221",
      "dimension": "compatibility",
      "scope": "incrementAttempt on shared Redis job records",
      "intended": "During rolling deploy or rollback, older worker pods either defer newer-version jobs or preserve v2 fields intact.",
      "observed": "incrementAttempt reconstructs { id, attempts } without schemaVersion check, wiping v2 fields on writeback.",
      "enforcement": "none; destructive projection in src/jobs/retry_store.ts",
      "evidence_ids": [
        "E-221"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-221",
      "path": "src/jobs/retry_store.ts",
      "line_start": 19,
      "line_end": 26,
      "symbol": "incrementAttempt",
      "context": "Reads raw JSON blob from Redis and writes back only { id: raw.id, attempts: raw.attempts + 1 }."
    }
  ],
  "findings": [
    {
      "id": "F-221",
      "topic": "compatibility",
      "tags": [
        "migration",
        "data-loss"
      ],
      "title": "Rolling deploy v1 worker strips v2 tenantShard and idempotencyToken fields on job retry",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-221"
      ],
      "evidence_ids": [
        "E-221"
      ],
      "preconditions": "Canary v2 API enqueues job JSON with tenantShard and idempotencyToken while v1 workers still poll shared queue.",
      "sequence": [
        "v2 API writes { schemaVersion: 2, id: 'j-1', attempts: 0, tenantShard: 'eu-1', idempotencyToken: 'tok-9' }.",
        "v1 worker picks up j-1, hits transient error, and runs incrementAttempt('j-1').",
        "v1 worker overwrites Redis key with { id: 'j-1', attempts: 1 }, permanently erasing tenantShard and idempotencyToken."
      ],
      "expected": "v1 worker requeues unsupported schemaVersion > 1 untouched or preserves unknown properties.",
      "actual": "Shared job record loses v2 routing and deduplication metadata mid-rollout.",
      "impact": "Subsequent v2 retry executes against wrong default shard without deduplication token.",
      "root_cause": "Read-modify-write serializer projects only known v1 fields and lacks schemaVersion gating.",
      "counterevidence": "Single-version clusters where all producers and consumers run v2 preserve fields.",
      "remedy": "Gate queue consumption by supported schemaVersion and use atomic field increment (HINCRBY) or unknown-field preservation.",
      "acceptance": [
        "Mixed v2-write -> v1-touch -> v2-read fixture retains tenantShard and idempotencyToken."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace verified across v2 producer serializer and retry_store.ts."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

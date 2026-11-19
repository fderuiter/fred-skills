# Trust and Authorization Boundaries: Specialist Method and Examples

## Tracing Caller Identity Binding to Resource Ownership and Sinks

1. Enumerate every external ingress surface (HTTP routes, GraphQL resolvers, message consumers, webhook endpoints, archive/file importers) alongside its deployment trust model, distinguishing multi-tenant server APIs from purely local CLI utilities.
2. Verify object-level and tenant-level binding on every read/mutation query: confirm that `org_id` / `owner_id` comes from the verified server principal and constrains the lookup (`WHERE id = $1 AND tenant_id = $2`), not merely route-level authentication.
3. Check mass-assignment DTOs, admin role checks, webhook HMAC verification, and outbound SSRF/file sinks so untrusted fields cannot escalate role flags or pivot to internal network hosts.

## Failure Trace vs. Safe Control: Authenticated Cross-Tenant Object Access (IDOR)

```typescript
// Vulnerable handler: verifies JWT login, but fetches document by id without binding req.auth.tenantId
router.patch("/v1/documents/:docId", requireAuth, async (req, res) => {
  const doc = await db.documents.update({
    where: { id: req.params.docId }, // Tenant A can mutate Tenant B's docId!
    data: { title: req.body.title },
  });
  res.json(doc);
});

// Safe control: scope lookup and mutation atomically by authenticated principal's tenantId
router.patch("/v1/documents/:docId", requireAuth, async (req, res) => {
  const updated = await db.documents.updateMany({
    where: { id: req.params.docId, tenantId: req.auth.tenantId },
    data: { title: String(req.body.title) },
  });
  if (updated.count === 0) return res.status(404).json({ error: "not_found" });
  return res.status(204).end();
});
```

## Trust-Boundary `audit.json` Deliverable Example

Validate this tenant-invoice ledger locally using `python3 <this-skill>/scripts/validate_audit.py trust-boundaries-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "trust-boundaries-sample",
  "repository": {
    "identity": "acme/docs-api",
    "revision": "3e77a19",
    "dirty": false,
    "snapshot": "git-tree:3e77a19"
  },
  "scope": [
    "src/routes/documents.ts"
  ],
  "coverage": [
    {
      "topic": "trust-boundaries",
      "scope": "src/routes/documents.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-901",
        "E-902"
      ],
      "notes": "Compared GET, PATCH, and DELETE handlers in documents.ts for tenant scoping.",
      "next_action": "Inspect URL preview importer with repo-audit-canonicalization."
    }
  ],
  "contracts": [
    {
      "id": "C-901",
      "dimension": "trust-boundaries",
      "scope": "PATCH /v1/documents/:docId",
      "intended": "Authenticated principals may modify only documents belonging to req.auth.tenantId.",
      "observed": "GET binds tenantId, whereas PATCH updates by primary key id alone.",
      "enforcement": "missing tenant predicate in PATCH handler",
      "evidence_ids": [
        "E-901",
        "E-902"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-901",
      "path": "src/routes/documents.ts",
      "line_start": 18,
      "line_end": 26,
      "symbol": "GET /v1/documents/:docId",
      "context": "Safe control: filters findFirst by { id: req.params.docId, tenantId: req.auth.tenantId }."
    },
    {
      "id": "E-902",
      "path": "src/routes/documents.ts",
      "line_start": 31,
      "line_end": 39,
      "symbol": "PATCH /v1/documents/:docId",
      "context": "Calls db.documents.update({ where: { id: req.params.docId } }) without tenantId."
    }
  ],
  "findings": [
    {
      "id": "F-901",
      "topic": "trust-boundaries",
      "tags": [
        "api-consistency"
      ],
      "title": "PATCH /v1/documents/:docId omits tenantId filter, allowing cross-tenant document overwrite",
      "status": "supported",
      "severity": "critical",
      "confidence": "high",
      "contract_ids": [
        "C-901"
      ],
      "evidence_ids": [
        "E-901",
        "E-902"
      ],
      "preconditions": "Attacker holds a valid low-privilege session in Tenant A and knows or guesses a document UUID in Tenant B.",
      "sequence": [
        "Caller authenticates as Tenant A user, passing requireAuth.",
        "Caller sends PATCH /v1/documents/<tenant-b-doc-uuid> with {\"title\": \"overwritten\"}.",
        "Handler updates row by primary key without checking tenantId and returns full updated record."
      ],
      "expected": "Request fails with HTTP 404 when docId does not belong to req.auth.tenantId.",
      "actual": "Tenant B document is modified and returned in the JSON response body.",
      "impact": "Cross-tenant unauthorized write and full document disclosure.",
      "root_cause": "Object lookup relies on global primary key instead of composite (id, tenantId) ownership guard.",
      "counterevidence": "GET /v1/documents/:docId enforces tenantId properly (E-901).",
      "remedy": "Scope PATCH and DELETE queries by { id: req.params.docId, tenantId: req.auth.tenantId } and avoid returning unverified rows.",
      "acceptance": [
        "Authenticated Tenant A caller receives HTTP 404 on PATCH /v1/documents/<tenant-b-id> and target row stays unchanged."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified directly in route handler and Prisma query call."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

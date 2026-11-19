# Boundary Specification: Specialist Method and Examples

## Auditing Ingress Crossings, Encapsulation Seams, and Authority Guards

1. Enumerate every crossing where external payloads, cross-service RPCs, or cross-package calls enter privileged domain or persistence layers (`rg -n "req\\.(body|query|headers)|request\\.|from .*models import"`).
2. Classify each seam accurately as `trust`, `module`, `service`, or `storage`, recording the exact input canonicalization, principal binding, and error-translation contract enforced at the edge.
3. Check whether raw transport maps, ORM entities, or caller-supplied tenant identifiers leak past the boundary, distinguishing genuine type-enforced gateways from pass-through wrappers.

## Failure Shape vs. Safe Control: Raw Query Pass-Through vs. Typed Trust Gateway

```typescript
// Leaky trust and storage boundary: forwards untrusted query keys and leaks ORM columns
app.get("/v1/invoices", async (req, res) => {
  const rows = await db.invoiceModel.findMany({ where: req.query });
  res.json(rows); // accepts caller-injected tenant_id and exposes internal cost_basis_cents
});

// Enforced trust + module boundary: parses strict DTO, binds verified principal, projects view
app.get("/v1/invoices", requireAuth, async (req, res) => {
  const filter = InvoiceQuerySchema.parse(req.query);
  const scope = TenantScope.fromPrincipal(req.principal);
  const invoices = await billingModule.listInvoices(scope, filter);
  res.json(invoices.map(toInvoicePublicDto));
});
```

## Boundary Contract `architecture.json` Deliverable Example

Validate this boundary specification locally using `python3 <this-skill>/scripts/validate_architecture.py boundaries.json` (`assets/architecture.schema.json`):

```json
{
  "schema_version": "1.0",
  "title": "Invoice API Trust and Ledger Storage Boundary Specification",
  "status": "accepted",
  "boundaries": [
    {
      "name": "public-invoice-ingress",
      "type": "trust",
      "description": "HTTP ingress gateway in src/api/invoices.ts converting external requests into authenticated tenant-scoped commands.",
      "invariants": [
        "Tenant identity derives solely from verified auth middleware principals, never from req.query or req.body.",
        "Unrecognized filter parameters fail strict schema validation with HTTP 400 before reaching domain services.",
        "Outbound responses serialize InvoicePublicDto projections and strip internal cost columns."
      ]
    },
    {
      "name": "billing-ledger-module",
      "type": "module",
      "description": "Domain package in src/domain/billing/ encapsulating invoice lifecycle rules behind narrow interfaces.",
      "invariants": [
        "Zero runtime imports from src/api/ or HTTP framework packages exist inside src/domain/billing/.",
        "Every repository lookup requires a constructed TenantScope value object."
      ]
    },
    {
      "name": "billing-postgres-store",
      "type": "storage",
      "description": "Relational schema owning invoices and invoice_line_items tables.",
      "invariants": [
        "Only src/adapters/postgres_invoice_repo.ts issues SQL statements against invoice tables."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-004",
      "title": "Enforce TenantScope value objects and public DTO projection at HTTP ingress",
      "context": "Passing req.query directly into ORM findMany in src/api/invoices.ts permits cross-tenant filter injection and internal column exposure.",
      "decision": "Mandate strict ingress schema parsing, principal-derived TenantScope binding, and explicit public DTO serialization at public-invoice-ingress.",
      "consequences": [
        "Positive: Eliminates cross-tenant query operator injection and ORM schema leakage by construction.",
        "Negative: Requires updating explicit DTO mappers whenever public invoice fields change."
      ]
    }
  ]
}
```

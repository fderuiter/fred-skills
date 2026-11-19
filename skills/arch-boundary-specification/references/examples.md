# Worked Examples: Boundary Specification

Load these examples on demand when specifying trust, module, service, and storage boundaries
and validating output with `scripts/validate_architecture.py`.

## 1. Leaky Boundary Input vs. Encapsulated Ingress Guard

Inspecting `src/api/invoices.ts` reveals raw HTTP query dictionaries crossing directly into
the internal ledger query builder without tenant binding or ORM isolation:

```typescript
// Leaky boundary: controller forwards untrusted query filters and returns raw ORM rows
app.get("/v1/invoices", async (req, res) => {
  const rows = await db.invoiceModel.findMany({ where: req.query });
  res.json(rows); // exposes internal cost_basis_cents and accepts arbitrary where keys
});
```

Safe boundary design separates:
- `public-invoice-ingress` (`trust` boundary): parses an explicit filter DTO, binds
  `tenantId` from the verified session principal, and maps outputs to a public view DTO.
- `ledger-domain-module` (`module` boundary): accepts typed `TenantInvoiceFilter` objects
  and hides ORM models behind repository interfaces.

## 2. Valid Boundary Specification (`assets/architecture.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_architecture.py architecture.json`:

```json
{
  "schema_version": "1.0",
  "title": "Invoice API Trust and Domain Module Boundary Specification",
  "status": "accepted",
  "boundaries": [
    {
      "name": "public-invoice-ingress",
      "type": "trust",
      "description": "HTTP ingress boundary in src/api/invoices.ts translating untrusted requests into authenticated tenant commands.",
      "invariants": [
        "Caller tenantId is derived exclusively from verified auth middleware, never from req.query or req.body.",
        "Unknown query keys are rejected by strict schema parsing before reaching domain services.",
        "Responses serialize InvoicePublicView DTOs and never expose ORM entities or internal cost columns."
      ]
    },
    {
      "name": "billing-ledger-core",
      "type": "module",
      "description": "In-process domain module in src/domain/billing/ owning invoice state transitions and balance rules.",
      "invariants": [
        "No runtime imports from src/api/ or HTTP transport packages are permitted inside src/domain/billing/.",
        "All repository queries require a non-empty TenantScope value object."
      ]
    },
    {
      "name": "billing-postgres-store",
      "type": "storage",
      "description": "Primary relational schema for invoices and line_items.",
      "invariants": [
        "Only src/adapters/postgres_invoice_repo.ts executes SQL against invoices tables."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-004",
      "title": "Enforce typed TenantScope and public DTO projection at HTTP ingress",
      "context": "Direct pass-through of req.query to ORM findMany in src/api/invoices.ts risks cross-tenant filter injection and internal column leakage.",
      "decision": "Require strict ingress DTO validation and mandatory TenantScope construction at the public-invoice-ingress boundary.",
      "consequences": [
        "Positive: Blocks operator/query injection and internal column exposure by construction.",
        "Negative: Adds explicit DTO mapping maintenance when public invoice fields evolve."
      ]
    }
  ]
}
```


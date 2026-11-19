# Component Decomposition: Specialist Method and Examples

## Partitioning Tangled Monoliths by State Ownership and Change Cadence

1. Map runtime import graphs, shared table writers, and side-effecting I/O calls across target packages (`rg -n "^(import|from) |smtp|requests|cursor\\.execute"`), separating pure domain policy from transport and persistence adapters.
2. Group capabilities that mutate the same transactional invariant into one cohesive `module`, and split responsibilities that vary independently (ingestion parsing, domain pricing/tax calculation, outbound notification) behind narrow typed ports.
3. Verify the proposed dependency graph is a strict DAG—checking runtime imports, factory wiring, and event callbacks—and plan incremental strangler seams rather than flag-day rewrites.

## Failure Shape vs. Safe Control: Entangled Billing Routine vs. Pure Core + Port Adapters

```python
# Entangled component: mixes CSV parsing, tax policy arithmetic, SQL writes, and SMTP I/O
def run_monthly_billing_monolith(csv_blob: str, db, smtp_client) -> int:
    billed = 0
    for line in csv_blob.splitlines()[1:]:
        acct_id, subtotal_str = line.split(",")
        total_cents = round(int(subtotal_str) * 1.0825)
        db.execute("INSERT INTO invoices VALUES (?, ?)", (acct_id, total_cents))
        smtp_client.send_mail(acct_id, f"Charged {total_cents} cents")
        billed += 1
    return billed


# Decomposed components: pure domain calculator isolated from CSV parser and I/O ports
def compute_invoice_total(line_item: UsageRecord, tax_rate_bps: int) -> InvoiceDraft:
    tax_cents = (line_item.subtotal_cents * tax_rate_bps + 5_000) // 10_000
    return InvoiceDraft(
        account_id=line_item.account_id,
        total_cents=line_item.subtotal_cents + tax_cents,
    )
```

## Component Decomposition `architecture.json` Deliverable Example

Validate this decomposition plan locally using `python3 <this-skill>/scripts/validate_architecture.py decomposition.json` (`assets/architecture.schema.json`):

```json
{
  "schema_version": "1.0",
  "title": "Monthly Billing Pipeline Component Decomposition",
  "status": "accepted",
  "boundaries": [
    {
      "name": "usage-csv-ingestor",
      "type": "trust",
      "description": "Adapter in src/billing/ingest.py validating raw CSV rows into immutable UsageRecord values.",
      "invariants": [
        "Malformed rows or negative subtotals fail validation before any invoice calculation or persistence occurs."
      ]
    },
    {
      "name": "invoice-pricing-core",
      "type": "module",
      "description": "Pure domain package in src/billing/pricing.py owning integer basis-point tax and discount rules.",
      "invariants": [
        "Performs zero filesystem, SQL, clock, or network side effects.",
        "Depends only on domain value objects and never imports src/billing/ingest.py or notification adapters."
      ]
    },
    {
      "name": "invoice-outbox-dispatch",
      "type": "storage",
      "description": "Persistence adapter in src/billing/repository.py committing invoices and queued email notifications atomically.",
      "invariants": [
        "SMTP delivery runs asynchronously from committed outbox records rather than inside the SQL write loop."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-DECOMP-01",
      "title": "Extract pure invoice-pricing-core and decouple SMTP delivery from SQL persistence",
      "context": "src/billing/pipeline.py couples CSV parsing, floating-point tax math, row-by-row SQL inserts, and synchronous SMTP calls in one loop, causing partial billing runs when SMTP times out.",
      "decision": "Decompose into usage-csv-ingestor, side-effect-free invoice-pricing-core, and atomic invoice-outbox-dispatch while keeping all three in-process within the billing service.",
      "consequences": [
        "Positive: Enables deterministic unit testing of tax arithmetic without database or SMTP mocks and prevents mid-loop SMTP partial commits.",
        "Negative: Adds explicit UsageRecord and InvoiceDraft adapter mapping code across module seams."
      ]
    }
  ]
}
```

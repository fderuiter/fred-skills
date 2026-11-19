# Cohesion and Responsibility: Specialist Method and Examples

## Auditing Entangled Change Axes in God Modules

1. Inspect modules and classes with high fan-out across unrelated subsystems (for example, mixing PDF layout formatting, tax jurisdiction math, S3 credential rotation, and Slack notifications inside one method).
2. Trace concrete maintenance hazards caused by low cohesion: where editing presentation copy or retry logic forces touching billing or authorization state without isolated unit seams.
3. Do not recommend premature decomposition of cohesive single-authority modules (such as a self-contained RFC parser whose internal helpers change for one specification reason).

## Failure Trace vs. Safe Control: Report Renderer Mutating Billing Ledger State vs. Separated Authorities

```python
# Defect trace: rendering a customer PDF invoice also recalculates tax rates, writes SQL ledger entries, and sends email
class InvoiceControllerGodObject:
    def render_pdf_buggy(self, db: Session, invoice_id: str) -> bytes:
        inv = db.get_invoice(invoice_id)
        inv.tax_cents = round(inv.subtotal_cents * self.fetch_live_tax_rate(inv.region))
        db.commit()  # previewing PDF overwrites locked historical tax rate in database!
        self.mailer.send_receipt(inv.customer_email)
        return build_pdf_bytes(inv)


# Safe control: pure PDF renderer accepts immutable InvoiceSnapshot; tax calculation and delivery live in explicit services
def render_invoice_pdf_pure(snapshot: InvoiceSnapshot) -> bytes:
    return build_pdf_bytes(snapshot)
```

## Cohesion `audit.json` Deliverable Example

Run `python3 <this-skill>/scripts/validate_audit.py cohesion-audit.json` to check this cohesion report against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "cohesion-sample",
  "repository": {
    "identity": "acme/billing-portal",
    "revision": "17ef39b",
    "dirty": false,
    "snapshot": "git-tree:17ef39b"
  },
  "scope": [
    "src/invoices/controller.py"
  ],
  "coverage": [
    {
      "topic": "cohesion",
      "scope": "src/invoices/controller.py",
      "status": "inspected",
      "evidence_ids": [
        "E-230"
      ],
      "notes": "Mapped responsibilities entangled inside InvoiceController.render_pdf.",
      "next_action": "Hand off read-endpoint mutation semantics to repo-audit-side-effects if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-230",
      "dimension": "cohesion",
      "scope": "InvoiceController.render_pdf",
      "intended": "Document rendering formats finalized invoice snapshots independently of tax-rate recalculation and email dispatch.",
      "observed": "render_pdf couples PDF layout generation, live tax API lookup, SQL ledger commit, and receipt emailing in one method.",
      "enforcement": "entangled multi-authority method in src/invoices/controller.py",
      "evidence_ids": [
        "E-230"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-230",
      "path": "src/invoices/controller.py",
      "line_start": 44,
      "line_end": 58,
      "symbol": "InvoiceController.render_pdf",
      "context": "Fetches live tax rate, mutates inv.tax_cents with db.commit(), dispatches email, and builds PDF bytes."
    }
  ],
  "findings": [
    {
      "id": "F-230",
      "topic": "cohesion",
      "tags": [
        "maintainability",
        "side-effects"
      ],
      "title": "Invoice PDF renderer entangles live tax mutation, DB commit, and customer email dispatch",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-230"
      ],
      "evidence_ids": [
        "E-230"
      ],
      "preconditions": "Support engineer re-downloads last year's finalized invoice PDF after regional tax rate changed.",
      "sequence": [
        "Admin endpoint calls InvoiceController.render_pdf(db, 'inv-2025-09').",
        "render_pdf queries current regional tax rate, overwrites historical inv.tax_cents in SQL, and emails customer.",
        "PDF renders altered total that no longer matches original payment charge."
      ],
      "expected": "PDF rendering is a pure transformation over an immutable finalized InvoiceSnapshot.",
      "actual": "Re-rendering historical PDF mutates accounting records and spams customer receipts.",
      "impact": "Layout troubleshooting or historical downloads alter closed accounting periods and trigger unwanted emails.",
      "root_cause": "Tax calculation, persistence, notification, and PDF formatting responsibilities were collapsed into render_pdf.",
      "counterevidence": "Initial invoice creation happens to want all four actions sequentially.",
      "remedy": "Extract pure render_invoice_pdf(snapshot) and invoke tax calculation + email only in finalize_invoice().",
      "acceptance": [
        "render_invoice_pdf runs in unit tests with zero DB or SMTP dependencies and performs no external writes."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified call graph of InvoiceController.render_pdf across admin and checkout routes."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

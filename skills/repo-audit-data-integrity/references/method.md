# Data Transformation Integrity: Specialist Method and Examples

## Auditing Precision, Units, and Lossless Round-Trips

1. Follow monetary amounts, physical measurements, timestamps, and Unicode identifiers across JSON/CSV/Protobuf parsers, in-memory domain structs, SQL column types, and outbound exports.
2. Flag lossy intermediate representations—such as binary IEEE-754 `float` arithmetic on decimal currency, naive timezone-stripped `datetime` conversions, or silent `int32` narrowing—that corrupt stored or exported values.
3. Distinguish display-only presentation formatting (for example, rendering `"$19.99"` in a UI template while keeping `1999` integer minor units in state and API payloads) from storage-path precision loss.

## Failure Trace vs. Safe Control: Binary Float Invoice Conversion vs. Integer Minor Units

```python
# Defect trace: parsing decimal CSV prices through float introduces IEEE-754 representation drift
def parse_line_item_cents_buggy(unit_price_str: str, quantity: int) -> int:
    unit_dollars = float(unit_price_str)  # float("19.99") * 100 -> 1998.9999999999998
    return int(unit_dollars * 100) * quantity  # truncates 19.99 to 1998 cents!


# Safe control: parse exact Decimal and quantize to integer minor units with explicit rounding mode
from decimal import Decimal, ROUND_HALF_UP


def parse_line_item_cents_safe(unit_price_str: str, quantity: int) -> int:
    cents = (Decimal(unit_price_str) * Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(cents) * quantity
```

## Data-Integrity `audit.json` Deliverable Example

Validate this transformation-integrity ledger using `python3 <this-skill>/scripts/validate_audit.py data-integrity-audit.json` against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "data-integrity-sample",
  "repository": {
    "identity": "acme/invoice-importer",
    "revision": "56fa28c",
    "dirty": false,
    "snapshot": "git-tree:56fa28c"
  },
  "scope": [
    "src/importer/csv_prices.py"
  ],
  "coverage": [
    {
      "topic": "data-integrity",
      "scope": "src/importer/csv_prices.py",
      "status": "inspected",
      "evidence_ids": [
        "E-216"
      ],
      "notes": "Inspected CSV unit_price parser, line-item subtotal math, and Postgres integer cents writer.",
      "next_action": "Hand off negative price guard inspection to repo-audit-domain-models if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-216",
      "dimension": "data-integrity",
      "scope": "parse_line_item_cents",
      "intended": "Decimal dollar strings in imported CSVs convert losslessly into exact USD integer cents.",
      "observed": "int(float(unit_price_str) * 100) truncates binary float imprecision, losing 1 cent per unit on values like '19.99'.",
      "enforcement": "none; binary float conversion in src/importer/csv_prices.py",
      "evidence_ids": [
        "E-216"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-216",
      "path": "src/importer/csv_prices.py",
      "line_start": 18,
      "line_end": 22,
      "symbol": "parse_line_item_cents",
      "context": "Computes int(float(unit_price_str) * 100) * quantity when importing vendor invoice rows."
    }
  ],
  "findings": [
    {
      "id": "F-216",
      "topic": "data-integrity",
      "tags": [
        "numeric-precision"
      ],
      "title": "Float-to-int truncation undercharges imported CSV unit prices such as 19.99",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-216"
      ],
      "evidence_ids": [
        "E-216"
      ],
      "preconditions": "CSV row contains decimal unit price without exact binary-float representation (e.g. '19.99', quantity 100).",
      "sequence": [
        "Importer reads unit_price_str = '19.99' and quantity = 100.",
        "float('19.99') * 100 evaluates to 1998.9999999999998.",
        "int(...) truncates to 1998 cents/unit, persisting 199800 cents ($1,998.00) instead of 199900 cents ($1,999.00)."
      ],
      "expected": "parse_line_item_cents('19.99', 100) returns 199900 cents.",
      "actual": "Function returns 199800 cents, silently dropping $1.00 across 100 units.",
      "impact": "Imported invoices systematically underbill decimal unit prices.",
      "root_cause": "Decimal currency string converted via IEEE-754 binary float followed by floor truncation.",
      "counterevidence": "Power-of-two fractions like '19.50' evaluate exactly in binary float and mask the defect in unit tests.",
      "remedy": "Parse currency strings with decimal.Decimal and quantize to integer minor units before multiplying.",
      "acceptance": [
        "parse_line_item_cents('19.99', 100) == 199900 and rejects >2 fractional digits unless configured."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified IEEE-754 evaluation path in src/importer/csv_prices.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

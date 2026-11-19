# Diff Analysis: Specialist Method and Examples

## Tracing Hunk Blast Radius Across Unchanged Callers and Wire Contracts

1. Parse every modified hunk (`git diff --unified=5`) alongside all untouched callers and interface implementations (`rg -n "symbol_name\\("`), paying close attention to deleted `-` lines that previously enforced normalization, exception contracts, or auth predicates.
2. Check signature, return-type, and exception-surface shifts—such as replacing a raised `KeyError` with `None`, dropping a default parameter, or adding a non-nullable payload field—against every caller's error handler and rolling-deploy compatibility window.
3. Confirm whether upstream adapters or type checkers already block the suspected regression before filing, setting `verdict` to `"request_changes"` whenever `critical` or `high` findings remain reachable.

## Failure Shape vs. Safe Control: Silent `None` Return Shift vs. Explicit Optional Helper

```python
# Breaking diff in src/catalog/pricing.py: drops .strip() and returns None instead of KeyError
def lookup_unit_price(sku: str, price_table: dict[str, int]) -> int | None:
    return price_table.get(sku.upper())
    # Caller cart_totals.py computes lookup_unit_price(sku, tbl) * qty -> unhandled TypeError!


# Backward-compatible safe control: preserves strict contract and adds separate optional API
def lookup_unit_price(sku: str, price_table: dict[str, int]) -> int:
    normalized = sku.strip().upper()
    if not normalized:
        raise ValueError("sku must be non-empty")
    return price_table[normalized]


def try_lookup_unit_price(sku: str, price_table: dict[str, int]) -> int | None:
    normalized = sku.strip().upper()
    return price_table.get(normalized) if normalized else None
```

## Pull Request Diff `review.json` Deliverable Example

Validate this diff-analysis output locally using `python3 <this-skill>/scripts/validate_review.py diff-review.json` (`assets/review.schema.json`):

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "Altering lookup_unit_price to return None instead of raising KeyError/ValueError crashes checkout arithmetic callers and drops whitespace trimming required by CSV imports.",
  "findings": [
    {
      "id": "DIFF-001",
      "title": "Returning None on missing SKU triggers unhandled TypeError in cart_totals",
      "severity": "high",
      "file": "src/catalog/pricing.py",
      "line": 22,
      "description": "lookup_unit_price now returns None for unknown SKUs while src/checkout/cart_totals.py:48 multiplies the return value by item.qty inside try/except (KeyError, ValueError), converting HTTP 400 client errors into HTTP 500 crashes.",
      "recommendation": "Keep lookup_unit_price raising KeyError/ValueError and introduce a separate try_lookup_unit_price helper if optional semantics are needed."
    },
    {
      "id": "DIFF-002",
      "title": "Removed .strip() normalization breaks padded SKUs from bulk CSV import",
      "severity": "medium",
      "file": "src/catalog/pricing.py",
      "line": 22,
      "description": "Replacing sku.strip().upper() with sku.upper() causes whitespace-padded SKUs from src/imports/bulk_order.py:19 to miss valid entries in price_table.",
      "recommendation": "Restore normalized = sku.strip().upper() and reject empty normalized strings prior to dictionary lookup."
    }
  ]
}
```

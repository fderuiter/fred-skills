# Worked Examples: Diff Analysis

Load these examples on demand when analyzing pull request diff hunks, tracing caller blast
radius, and validating `review.json` with `scripts/validate_review.py`.

## 1. Pull Request Diff Hunk Altering Return Semantics and Removing Guard

```diff
--- a/src/catalog/pricing.py
+++ b/src/catalog/pricing.py
@@ -22,9 +22,7 @@ def lookup_unit_price(sku: str, price_table: dict[str, int]) -> int | None:
-    normalized = sku.strip().upper()
-    if not normalized:
-        raise ValueError("sku must be non-empty")
-    if normalized not in price_table:
-        raise KeyError(f"Unknown SKU: {normalized}")
-    return price_table[normalized]
+    return price_table.get(sku.upper())
```

Caller impact trace:
- `src/checkout/cart_totals.py:48` computes `subtotal += lookup_unit_price(item.sku, table) * item.qty`
  and catches `(KeyError, ValueError)` to return HTTP 400. Changing missing/whitespace SKUs
  to return `None` raises an uncaught `TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'`
  (HTTP 500), and dropping `.strip()` breaks padded CSV imports in `src/imports/bulk_order.py:19`.

## 2. Valid Diff Review Output (`assets/review.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_review.py review.json`:

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "Changing lookup_unit_price from raising KeyError/ValueError to returning None breaks arithmetic callers in checkout and drops whitespace normalization used by bulk CSV import.",
  "findings": [
    {
      "id": "REV-001",
      "title": "Returning None instead of raising KeyError crashes cart_totals arithmetic",
      "severity": "high",
      "file": "src/catalog/pricing.py",
      "line": 22,
      "description": "lookup_unit_price now returns None on unknown SKUs, but src/checkout/cart_totals.py:48 multiplies the return value directly by item.qty inside a try/except (KeyError, ValueError) block, turning HTTP 400 validation errors into unhandled TypeError 500 responses.",
      "recommendation": "Preserve KeyError/ValueError exceptions on lookup_unit_price or introduce a dedicated lookup_optional_unit_price helper and update all callers explicitly."
    },
    {
      "id": "REV-002",
      "title": "Dropped .strip() normalization rejects whitespace-padded SKUs from bulk imports",
      "severity": "medium",
      "file": "src/catalog/pricing.py",
      "line": 22,
      "description": "Replacing sku.strip().upper() with sku.upper() causes trailing-space SKUs from src/imports/bulk_order.py:19 to miss existing price_table entries.",
      "recommendation": "Restore normalized = sku.strip().upper() and reject empty normalized strings before lookup."
    }
  ]
}
```

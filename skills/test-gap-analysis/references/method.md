# Test Gap Analysis: Specialist Method and Examples

## Cross-Referencing Production Branches Against Existing Test Assertions

1. Inventory conditional branches, exception raises, rollback blocks, and boundary thresholds across target production modules (`rg -n "raise |except |if .*<=|if .*>=|rollback"`), pairing each contract with existing test files (`tests/`).
2. Inspect the assertion bodies inside matching tests—not merely line execution—flagging mock-only call checks, vacuous loops over potentially empty lists, and happy-path-only inputs that leave error or boundary branches unverified.
3. Rank uncovered scenarios by defect blast radius and classify each proposed gap with its decisive `oracle_type` (`state`, `exception`, `output`, or `boundary`) while keeping `verified: false` until tests are authored and run.

## Failure Shape vs. Safe Control: Happy-Path-Only Discount Check vs. Boundary + Exception Matrix

```python
# Production function in src/pricing/discount_calc.py with 3 distinct behavioral branches:
def compute_discount_cents(subtotal_cents: int, pct: int) -> int:
    if subtotal_cents < 0 or pct < 0:
        raise ValueError("subtotal_cents and pct must be non-negative")
    clamped_pct = min(pct, 100)
    return (subtotal_cents * clamped_pct) // 100


# Weak existing test: exercises only nominal interior input (200, 15) -> misses 2 branches!
def test_nominal_discount():
    assert compute_discount_cents(200, 15) == 30


# Complete oracle target specification: covers negative rejection and >100% clamp boundary
def test_discount_negative_and_clamp_edges():
    assert compute_discount_cents(250, 150) == 250
    try:
        compute_discount_cents(-1, 10)
    except ValueError:
        return
    raise AssertionError("Expected ValueError for negative subtotal_cents")
```

## Coverage Gap `testing.json` Deliverable Example

Validate this gap-analysis specification locally using `python3 <this-skill>/scripts/validate_testing.py gap-report.json` (`assets/testing.schema.json`):

```json
{
  "schema_version": "1.0",
  "component": "src/pricing/discount_calc.py",
  "framework": "unittest",
  "test_cases": [
    {
      "id": "GAP-001",
      "name": "test_negative_subtotal_or_percentage_raises_value_error",
      "description": "src/pricing/discount_calc.py: assert compute_discount_cents(-1, 10) and compute_discount_cents(100, -5) raise ValueError without arithmetic evaluation.",
      "oracle_type": "exception",
      "verified": false
    },
    {
      "id": "GAP-002",
      "name": "test_percentage_above_hundred_clamps_to_subtotal",
      "description": "src/pricing/discount_calc.py: assert compute_discount_cents(250, 150) clamps discount at 250 cents and compute_discount_cents(250, 0) returns 0.",
      "oracle_type": "boundary",
      "verified": false
    }
  ]
}
```

# Worked Examples: Test Suite Verification

Load these examples on demand when verifying test suite determinism, isolation, and fault
rejection power, and validating `testing.json` with `scripts/validate_testing.py`.

## 1. Baseline Execution and Seeded Fault Sensitivity Check

```text
1. Baseline shuffled runs (isolation & determinism check):
   $ pytest -q tests/test_token_bucket.py
   -> 4 passed in 0.06s (zero wall-clock sleeps, isolated ManualClock instances)

2. Disposable fault injection check (oracle sensitivity):
   Mutate in scratch copy: remove `self._tokens < amount` early return guard so failed consume still subtracts tokens.
   $ pytest -q /tmp/mutant-check/test_token_bucket.py
   -> FAILED test_consume_exhaustion_preserves_partial_tokens (killed mutant: residual token balance dropped below 1.0)

3. Surviving weak-oracle check:
   test_constructor_repr only asserts `isinstance(repr(bucket), str)` -> mark verified=false for defect rejection and recommend asserting exact formatted capacity/rate fields.
```

## 2. Valid Test Suite Verification Deliverable (`assets/testing.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_testing.py testing.json`:

```json
{
  "schema_version": "1.0",
  "component": "src/ratelimit/token_bucket.py",
  "framework": "pytest",
  "test_cases": [
    {
      "id": "TC-001",
      "name": "test_consume_exhaustion_preserves_partial_tokens",
      "description": "Executed on baseline (pass) and disposable state-subtraction mutant (failed as expected with AssertionError).",
      "oracle_type": "state",
      "verified": true
    },
    {
      "id": "TC-002",
      "name": "test_refill_clamps_at_bucket_capacity",
      "description": "Executed on baseline (pass) and disposable min-clamp-removal mutant (failed as expected when consume(4) left excess tokens).",
      "oracle_type": "boundary",
      "verified": true
    },
    {
      "id": "TC-003",
      "name": "test_constructor_repr_format",
      "description": "Weak type-only assertion isinstance(repr(bucket), str) survived field-omission mutation; needs literal output expectation.",
      "oracle_type": "output",
      "verified": false
    }
  ]
}
```

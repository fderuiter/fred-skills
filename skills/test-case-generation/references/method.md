# Test Case Generation: Specialist Method and Examples

## Synthesizing Hermetic Fixtures and Independent Golden Oracles

1. Inspect the repository's native runner, fixture idioms, and module imports (`pyproject.toml`, `package.json`, `Cargo.toml`) so generated tests run offline with zero unapproved third-party dependencies.
2. Construct deterministic inputs and controllable seams (fake clocks, ephemeral temp directories, in-memory stores) covering exact domain boundaries (`0`, `limit - 1`, `limit`, `limit + 1`) and invalid inputs.
3. Write independent literal golden assertions—never calling the function under test or copying its formula to compute `expected`—and execute the target runner to confirm `verified: true` before reporting.

## Failure Shape vs. Safe Control: Circular Formula Oracle vs. Independent Boundary Vectors

```python
# Deceptive generated test: duplicates rate-limiter formula and misses off-by-one edge
class WeakRateLimiterTests(unittest.TestCase):
    def test_allow_request_mirrors_impl(self) -> None:
        for count in (0, 1, 2):
            expected = count < 3  # circularly copies implementation logic!
            self.assertEqual(expected, allow_request(count, burst_limit=3))


# Strong deterministic boundary test: explicit literal expectations across exact transitions
class RateLimiterBoundaryTests(unittest.TestCase):
    def test_burst_limit_exact_cutoff_and_zero_quota(self) -> None:
        self.assertTrue(allow_request(current_count=2, burst_limit=3))
        self.assertFalse(allow_request(current_count=3, burst_limit=3))
        self.assertFalse(allow_request(current_count=4, burst_limit=3))
        self.assertFalse(allow_request(current_count=0, burst_limit=0))
```

## Generated Test Suite `testing.json` Deliverable Example

Validate this test-generation record locally using `python3 <this-skill>/scripts/validate_testing.py generated-tests.json` (`assets/testing.schema.json`):

```json
{
  "schema_version": "1.0",
  "component": "src/throttling/rate_limiter.py",
  "framework": "unittest",
  "test_cases": [
    {
      "id": "TC-001",
      "name": "test_burst_limit_exact_cutoff_and_zero_quota",
      "description": "tests/test_rate_limiter_bounds.py: assert allow_request(2, 3) returns True while exact threshold (3, 3), overflow (4, 3), and zero quota (0, 0) return False.",
      "oracle_type": "boundary",
      "verified": true
    },
    {
      "id": "TC-002",
      "name": "test_negative_burst_limit_raises_value_error",
      "description": "tests/test_rate_limiter_bounds.py: assert allow_request(0, -1) raises ValueError without mutating bucket counters.",
      "oracle_type": "exception",
      "verified": true
    }
  ]
}
```

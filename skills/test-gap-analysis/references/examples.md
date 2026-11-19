# Worked Examples: Test Gap Analysis

Load these examples on demand when mapping unexercised branches and tautological assertions
and validating `testing.json` with `scripts/validate_testing.py`.

## 1. Production Module and Existing Happy-Path Test Input

```python
# src/ratelimit/token_bucket.py
class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float, clock):
        if capacity <= 0 or refill_per_sec <= 0:
            raise ValueError("capacity and refill_per_sec must be positive")
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._clock = clock
        self._tokens = float(capacity)
        self._last_ts = clock()

    def consume(self, amount: int = 1) -> bool:
        if amount <= 0 or amount > self.capacity:
            raise ValueError("amount must be within 1..capacity")
        now = max(self._last_ts, self._clock())
        self._tokens = min(
            float(self.capacity),
            self._tokens + (now - self._last_ts) * self.refill_per_sec,
        )
        self._last_ts = now
        if self._tokens < amount:
            return False
        self._tokens -= amount
        return True


# tests/test_token_bucket.py (existing suite only tests single happy-path consume(1))
def test_single_consume():
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0, clock=lambda: 100.0)
    assert bucket.consume(1) is True
```

Identified high-value gaps: depletion boundary (`_tokens < amount` without mutating remaining
tokens), capacity clamp after long idle refill, backward clock skew protection, and invalid
`amount` rejection. Because these proposed cases are specified during gap analysis rather than
executed yet, `verified` is set to `false`.

## 2. Valid Test Gap Specification (`assets/testing.schema.json`)

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
      "description": "Verify that requesting 3 tokens when 2 remain returns False and leaves remaining _tokens at 2.0 rather than going negative.",
      "oracle_type": "state",
      "verified": false
    },
    {
      "id": "TC-002",
      "name": "test_refill_clamps_at_bucket_capacity_and_handles_clock_regression",
      "description": "Verify advancing fake clock by 3600s caps tokens at exact capacity (5) and backward clock step does not subtract tokens.",
      "oracle_type": "boundary",
      "verified": false
    },
    {
      "id": "TC-003",
      "name": "test_consume_rejects_zero_or_over_capacity_amount",
      "description": "Verify consume(0) and consume(capacity + 1) raise ValueError without altering _tokens or _last_ts.",
      "oracle_type": "exception",
      "verified": false
    }
  ]
}
```

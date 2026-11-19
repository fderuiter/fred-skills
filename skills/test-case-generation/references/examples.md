# Worked Examples: Test Case Generation

Load these examples on demand when authoring deterministic unit/integration tests with
independent literal oracles and validating `testing.json` via `scripts/validate_testing.py`.

## 1. Deterministic Generated Test Suite Using Controllable Clock

Targeting uncovered `TokenBucket` gaps in `src/ratelimit/token_bucket.py` without sleeps or
circular expected-value formulas:

```python
import pytest
from src.ratelimit.token_bucket import TokenBucket


class ManualClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now


def test_consume_exhaustion_preserves_partial_tokens() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=4, refill_per_sec=2.0, clock=clock)
    assert bucket.consume(3) is True
    assert bucket.consume(2) is False
    # Exact 1 token remained; advancing 0.5s adds 1.0 token -> consume(2) now succeeds
    clock.now += 0.5
    assert bucket.consume(2) is True


def test_consume_rejects_out_of_bounds_amount_without_side_effects() -> None:
    clock = ManualClock()
    bucket = TokenBucket(capacity=4, refill_per_sec=1.0, clock=clock)
    with pytest.raises(ValueError, match="1..capacity"):
        bucket.consume(0)
    with pytest.raises(ValueError, match="1..capacity"):
        bucket.consume(5)
    assert bucket.consume(4) is True
```

## 2. Valid Generated Test Manifest (`assets/testing.schema.json`)

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
      "description": "Uses ManualClock to prove failed consume(2) leaves 1.0 residual token intact so a 0.5s refill reaches exact threshold 2.0.",
      "oracle_type": "state",
      "verified": true
    },
    {
      "id": "TC-002",
      "name": "test_consume_rejects_out_of_bounds_amount_without_side_effects",
      "description": "Asserts ValueError on amount=0 and amount=capacity+1 and verifies full capacity (4) remains consumable afterward.",
      "oracle_type": "exception",
      "verified": true
    }
  ]
}
```

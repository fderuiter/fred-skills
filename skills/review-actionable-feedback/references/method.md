# Actionable Review Feedback: Specialist Method and Examples

## Deduplicating Raw Review Notes into Root-Cause Remediation Guidance

1. Group raw review notes, static-analysis alerts, and multi-pass observations by shared root cause and corrective mechanism rather than emitting repetitive line-by-line complaints across callers.
2. Re-verify every candidate note against the target revision (`src/` and tests), discarding stale remarks, stylistic preferences that match repository conventions, and claims refuted by upstream guards.
3. Pair each surviving finding with a calibrated severity, exact `file` and `line` anchor, concise failure explanation, and minimal idiomatic code remedy that fixes the contract without broad refactoring.

## Failure Shape vs. Safe Control: Fragmented Nitpick Noise vs. Consolidated Root-Cause Fix

```python
# Vulnerable code in src/auth/token_validator.py targeted by 3 noisy reviewer comments:
# Comment A: "check exp here", Comment B: "now_epoch unused", Comment C: "expired JWT accepted"
def is_token_active(claims: dict[str, object], now_epoch: int) -> bool:
    subject = claims.get("sub")
    return isinstance(subject, str) and bool(subject.strip())


# Consolidated actionable remedy: single root-cause check enforcing exp > now_epoch
def is_token_active_fixed(claims: dict[str, object], now_epoch: int) -> bool:
    subject = claims.get("sub")
    exp = claims.get("exp")
    if not isinstance(subject, str) or not subject.strip():
        return False
    return isinstance(exp, int) and not isinstance(exp, bool) and exp > now_epoch
```

## Consolidated Feedback `review.json` Deliverable Example

Validate this synthesized feedback payload locally using `python3 <this-skill>/scripts/validate_review.py feedback-review.json` (`assets/review.schema.json`):

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "Consolidated three overlapping JWT review comments into one high-severity token expiration defect with a concrete validation remedy; suppressed one refuted style nit.",
  "findings": [
    {
      "id": "FB-001",
      "title": "Enforce integer exp > now_epoch check in is_token_active",
      "severity": "high",
      "file": "src/auth/token_validator.py",
      "line": 4,
      "description": "is_token_active ignores its now_epoch parameter and validates only claims['sub'], allowing expired session tokens to authenticate indefinitely.",
      "recommendation": "Verify isinstance(exp, int) and not isinstance(exp, bool) and exp > now_epoch before returning True, and add a boundary unit test for exp == now_epoch."
    }
  ]
}
```

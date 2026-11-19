# Test Oracle Quality: Specialist Method and Examples

## Auditing Tautological Assertions and Implementation-Mirroring Oracles

1. Inspect test assertions on critical calculations, serializers, security gates, and state transitions: ask what concrete production bug could mutate the return value or side effect while the assertion still passes.
2. Flag tautological oracles (`assert result is not None`, `assert mock.called` without argument checks, snapshot blobs updated blindly, or tests that compute `expected` by calling the same buggy helper used by the production code).
3. Recognize strong independent oracles: hand-verified golden vectors from external specifications, inverse round-trip laws (`decode(encode(x)) == x`), and differential comparisons against a simple reference model.

## Failure Trace vs. Safe Control: Tautological Helper-Mirroring Oracle vs. Independent Golden Vectors

```python
# Defect trace: test computes expected fee by calling calculate_tier_fee(), mirroring off-by-100bps rate bug!
def test_calculate_tier_fee_tautological() -> None:
    invoice = create_invoice(subtotal_cents=10_000, tier="pro")
    assert invoice.platform_fee_cents == calculate_tier_fee(
        10_000, "pro"
    )  # passes even if rate is 25% instead of 2.5%!


# Safe control: assert exact specification-derived integer vectors and monotonic boundary invariants
def test_calculate_tier_fee_independent_oracle() -> None:
    # Billing spec v3.2: "pro" tier charges 250 bps (2.5%), rounded half-up to nearest cent
    assert create_invoice(subtotal_cents=10_000, tier="pro").platform_fee_cents == 250
    assert (
        create_invoice(subtotal_cents=105, tier="pro").platform_fee_cents == 3
    )  # 2.625 -> 3 half-up
```

## Test-Oracles `audit.json` Output Example

Check this test-oracle quality ledger locally via `python3 <this-skill>/scripts/validate_audit.py test-oracles-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "test-oracles-sample",
  "repository": {
    "identity": "acme/marketplace-fees",
    "revision": "40bc62e",
    "dirty": false,
    "snapshot": "git-tree:40bc62e"
  },
  "scope": [
    "src/fees/calculator.py",
    "tests/test_fees.py"
  ],
  "coverage": [
    {
      "topic": "test-oracles",
      "scope": "tests/test_fees.py",
      "status": "inspected",
      "evidence_ids": [
        "E-233"
      ],
      "notes": "Evaluated assertion independence across all 9 tests in tests/test_fees.py.",
      "next_action": "Hand off rounding precision inspection to repo-audit-data-integrity if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-233",
      "dimension": "test-oracles",
      "scope": "tests/test_fees.py fee calculation assertions",
      "intended": "Fee unit tests fail whenever platform_fee_cents deviates from published basis-point schedule.",
      "observed": "Tests assert invoice.platform_fee_cents == calculate_tier_fee(...), reusing the exact production function under test.",
      "enforcement": "circular tautological assertion in tests/test_fees.py",
      "evidence_ids": [
        "E-233"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-233",
      "path": "tests/test_fees.py",
      "line_start": 24,
      "line_end": 33,
      "symbol": "test_pro_tier_invoice_fee",
      "context": "Computes expected value by calling calculate_tier_fee(subtotal, tier), the same helper called inside create_invoice."
    }
  ],
  "findings": [
    {
      "id": "F-233",
      "topic": "test-oracles",
      "tags": [
        "testing",
        "tautology"
      ],
      "title": "Circular fee test oracle reuses production calculate_tier_fee helper instead of independent vectors",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-233"
      ],
      "evidence_ids": [
        "E-233"
      ],
      "preconditions": "Developer edits basis-point divisor or rounding mode inside calculate_tier_fee.",
      "sequence": [
        "calculate_tier_fee divides by 1000 instead of 10000 (10x fee regression).",
        "create_invoice calls calculate_tier_fee(10000, 'pro') -> 2500.",
        "test_pro_tier_invoice_fee asserts 2500 == calculate_tier_fee(10000, 'pro') -> True."
      ],
      "expected": "Test suite fails immediately on any basis-point divisor or half-cent rounding regression.",
      "actual": "All fee tests remain green under arbitrary formula regressions inside calculate_tier_fee.",
      "impact": "Billing rate regressions ship through CI undetected.",
      "root_cause": "Expected assertion value was derived from the implementation under test rather than independent spec literals.",
      "counterevidence": "Test still verifies create_invoice passes subtotal and tier arguments unmodified.",
      "remedy": "Replace helper calls in assertions with literal golden vectors from the published fee schedule plus fractional rounding cases.",
      "acceptance": [
        "Mutating divisor 10000 -> 1000 or ROUND_HALF_UP -> ROUND_DOWN fails tests/test_fees.py."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified implementation-mirroring call graph in tests/test_fees.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

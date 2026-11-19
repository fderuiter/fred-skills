# Failure-Mode Completeness: Specialist Method and Examples

## Probing Mid-Flight Timeouts, Lost Acknowledgements, and Partial Recovery

1. Decompose each critical multi-step operation (charge card -> persist order -> decrement inventory -> send receipt) and ask what durable state remains if the process crashes, times out, or drops the network response *between* any two steps.
2. Check whether client or worker retries after an ambiguous timeout (`504 Gateway Timeout`, `ECONNRESET`) detect an already-applied remote effect via an idempotency key or reconciliation query before charging or mutating twice.
3. Separate non-critical best-effort side effects (such as analytics telemetry whose failure is intentionally logged and ignored) from uncompensated core state divergence.

## Failure Trace vs. Safe Control: Ambiguous Gateway Timeout Triggering Double Charge

```python
# Defect: fresh UUID generated inside retry loop charges card again when first ACK timed out
def charge_subscription(gateway, invoice_id: str, cents: int) -> str:
    for _ in range(3):
        try:
            return gateway.create_charge(
                amount_cents=cents,
                idempotency_key=str(uuid.uuid4()),  # new key per retry!
            )
        except TimeoutError:
            continue
    raise RuntimeError("charge failed")


# Safe control: deterministic idempotency key derived from invoice_id + billing_attempt
def charge_subscription_safe(gateway, invoice_id: str, attempt: int, cents: int) -> str:
    stable_key = f"invoice:{invoice_id}:attempt:{attempt}"
    for _ in range(3):
        try:
            return gateway.create_charge(
                amount_cents=cents,
                idempotency_key=stable_key,
            )
        except TimeoutError:
            continue
    raise RuntimeError("charge outcome ambiguous; queued for reconciliation")
```

## Failure-Mode `audit.json` Deliverable Example

Validate this retry-storm ledger locally using `python3 <this-skill>/scripts/validate_audit.py failure-modes-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "failure-modes-sample",
  "repository": {
    "identity": "acme/subscription-renewer",
    "revision": "f09c312",
    "dirty": false,
    "snapshot": "git-tree:f09c312"
  },
  "scope": [
    "src/renewals/charger.py"
  ],
  "coverage": [
    {
      "topic": "failure-modes",
      "scope": "src/renewals/charger.py",
      "status": "inspected",
      "evidence_ids": [
        "E-801"
      ],
      "notes": "Audited timeout, retry, and crash recovery boundaries in subscription renewal loop.",
      "next_action": "Cross-reference dedup key lifecycle with repo-audit-idempotency."
    }
  ],
  "contracts": [
    {
      "id": "C-801",
      "dimension": "failure-modes",
      "scope": "charge_subscription",
      "intended": "Network read timeouts after gateway capture do not bill the customer twice on retry.",
      "observed": "Retry loop mints a fresh uuid4() idempotency_key on every TimeoutError iteration.",
      "enforcement": "per-attempt random UUID in src/renewals/charger.py",
      "evidence_ids": [
        "E-801"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-801",
      "path": "src/renewals/charger.py",
      "line_start": 27,
      "line_end": 38,
      "symbol": "charge_subscription",
      "context": "Calls uuid.uuid4() inside for _ in range(3) retry loop on TimeoutError."
    }
  ],
  "findings": [
    {
      "id": "F-801",
      "topic": "failure-modes",
      "tags": [
        "idempotency"
      ],
      "title": "Regenerating idempotency_key inside TimeoutError retry loop double-charges invoices",
      "status": "supported",
      "severity": "critical",
      "confidence": "high",
      "contract_ids": [
        "C-801"
      ],
      "evidence_ids": [
        "E-801"
      ],
      "preconditions": "Payment gateway captures charge on attempt 1, but response socket times out before client reads HTTP 200.",
      "sequence": [
        "Attempt 1 sends idempotency_key K1; gateway captures funds; socket raises TimeoutError.",
        "Retry loop catches TimeoutError and generates distinct idempotency_key K2.",
        "Attempt 2 sends K2 and captures a second charge for the same invoice."
      ],
      "expected": "Retries reuse one deterministic key per invoice renewal cycle and mark exhausted timeouts pending reconciliation.",
      "actual": "Customer card is billed up to 3 times during transient gateway latency.",
      "impact": "Direct duplicate customer charges and chargeback liability.",
      "root_cause": "Idempotency key is scoped to network socket attempt instead of logical invoice operation.",
      "counterevidence": "Immediate HTTP 4xx card-decline errors do not raise TimeoutError and are not retried.",
      "remedy": "Persist or deterministically derive idempotency_key outside the retry loop from invoice_id and renewal cycle.",
      "acceptance": [
        "Simulated TimeoutError on attempt 1 followed by HTTP 200 on attempt 2 passes identical idempotency_key to both gateway calls."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace of retry loop in charger.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

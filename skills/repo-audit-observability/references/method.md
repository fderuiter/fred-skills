# Observability Gaps: Specialist Method and Examples

## Auditing Diagnostic Distinguishability Across Failure Modes

1. Walk critical failure, retry, fallback, and authorization-denial branches and ask: given only emitted logs, metrics, and trace attributes, can an on-call responder distinguish upstream timeout vs. malformed payload vs. duplicate replay without attaching a debugger?
2. Flag swallowed exception causes, correlation-ID drops across thread/queue hops, unredacted credentials/PII in error formatters, and success metrics incremented when a fallback silently served degraded data.
3. Balance diagnostic fidelity against cardinality and privacy: never recommend logging raw bearer tokens, passwords, or unbounded high-cardinality user payloads into metric labels.

## Failure Trace vs. Safe Control: Context-Free Retry Catch vs. Correlated Redacted Structured Event

```python
# Defect trace: generic warning drops exception class, HTTP status, attempt count, and tenant/operation correlation IDs
async def sync_entitlements_buggy(
    client: BillingClient, tenant_id: str, op_id: str
) -> bool:
    try:
        await client.push_entitlements(tenant_id, idempotency_key=op_id)
        return True
    except Exception:
        logger.warning("Entitlement sync failed; falling back to cached tier")
        return False


# Safe control: emit structured causal fields (op_id, tenant_id, error_code, retryable) while keeping tokens redacted
async def sync_entitlements_safe(
    client: BillingClient, tenant_id: str, op_id: str
) -> bool:
    try:
        await client.push_entitlements(tenant_id, idempotency_key=op_id)
        return True
    except BillingApiError as exc:
        logger.warning(
            "entitlement_sync_degraded",
            extra={
                "op_id": op_id,
                "tenant_id": tenant_id,
                "status_code": exc.status_code,
                "error_type": type(exc).__name__,
                "retryable": exc.retryable,
            },
        )
        metrics.increment(
            "entitlement_sync.fallback_total",
            tags={"status_class": f"{exc.status_code // 100}xx"},
        )
        return False
```

## Observability `audit.json` Output Example

Verify this observability audit ledger with `python3 <this-skill>/scripts/validate_audit.py observability-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "observability-sample",
  "repository": {
    "identity": "acme/entitlement-sync",
    "revision": "28fa40c",
    "dirty": false,
    "snapshot": "git-tree:28fa40c"
  },
  "scope": [
    "src/billing/entitlement_sync.py"
  ],
  "coverage": [
    {
      "topic": "observability",
      "scope": "src/billing/entitlement_sync.py",
      "status": "inspected",
      "evidence_ids": [
        "E-231"
      ],
      "notes": "Inspected exception logging, trace propagation, and fallback counters in sync_entitlements.",
      "next_action": "Hand off degraded-mode retry policy to repo-audit-failure-modes if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-231",
      "dimension": "observability",
      "scope": "sync_entitlements fallback diagnostics",
      "intended": "Degraded entitlement syncs emit structured tenant_id, op_id, upstream status_code, and fallback counter.",
      "observed": "Catch block logs static string 'Entitlement sync failed; falling back to cached tier' with zero causal fields or metrics.",
      "enforcement": "none; exception object discarded in src/billing/entitlement_sync.py",
      "evidence_ids": [
        "E-231"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-231",
      "path": "src/billing/entitlement_sync.py",
      "line_start": 19,
      "line_end": 26,
      "symbol": "sync_entitlements",
      "context": "Catches bare Exception without binding variable and logs static warning string lacking tenant_id, op_id, or status."
    }
  ],
  "findings": [
    {
      "id": "F-231",
      "topic": "observability",
      "tags": [
        "logging",
        "incident-response"
      ],
      "title": "Entitlement sync fallback discards upstream error cause, tenant_id, and correlation op_id",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-231"
      ],
      "evidence_ids": [
        "E-231"
      ],
      "preconditions": "Upstream billing API rejects credentials (401) or rate-limits (429) during tenant tier upgrades.",
      "sequence": [
        "client.push_entitlements raises BillingApiError(status_code=401) during API key rotation.",
        "sync_entitlements catches Exception, discards exc, logs static warning without tenant_id or status_code, and returns False.",
        "Operators see identical static log line whether failure is transient 503 or permanent 401 auth misconfiguration."
      ],
      "expected": "Structured log and low-cardinality metric distinguish 401 auth failure from 503 timeout and identify affected op_id.",
      "actual": "All failure classes collapse into an unindexed static warning with zero metrics.",
      "impact": "Broken billing credentials go undetected while upgraded tenants stay stuck on stale cached tiers.",
      "root_cause": "Exception variable omitted in catch handler and structured context omitted from logger call.",
      "counterevidence": "Happy-path syncs return True without needing fallback telemetry.",
      "remedy": "Log structured op_id, tenant_id, error_type, and status_code, and increment bounded status_class fallback metric.",
      "acceptance": [
        "Simulated 401 and 503 errors emit distinct status_code fields and increment fallback metric with bounded status_class tag."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified logger and metrics calls across src/billing/entitlement_sync.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

# Idempotency and Replay Safety: Specialist Method and Examples

## Auditing Deduplication Key Atomicity, Payload Binding, and TTL Horizons

1. Inventory at-least-once consumers (SQS/Kafka handlers, webhook endpoints, cron jobs, client POST retry handlers), identifying the exact deduplication key, storage medium, and retention window compared to the producer's maximum retry horizon.
2. Check for check-then-act races where two workers concurrently query `is_processed(event_id) == False`, execute an external side effect, and only record the marker afterward—and verify whether reusing an `Idempotency-Key` with a *different* request payload is rejected (`409`/`422`) rather than returning a mismatched cached response.
3. Avoid flagging naturally idempotent reads (`GET`) or intentionally distinct user submissions carrying distinct client command IDs.

## Failure Trace vs. Safe Control: Non-Atomic Check-Then-Act Consumer Replay

```python
# Defect: two queue workers both pass SELECT check and both issue refund credit
def handle_refund_event(conn, event_id: str, account_id: str, cents: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM seen_events WHERE id = %s", (event_id,)
    ).fetchone()
    if row is not None:
        return
    conn.execute(
        "UPDATE balances SET cents = cents + %s WHERE id = %s", (cents, account_id)
    )
    conn.execute("INSERT INTO seen_events (id) VALUES (%s)", (event_id,))


# Safe control: claim idempotency key first via atomic INSERT ... ON CONFLICT DO NOTHING
def handle_refund_event_safe(conn, event_id: str, account_id: str, cents: int) -> bool:
    claimed = conn.execute(
        "INSERT INTO seen_events (id) VALUES (%s) ON CONFLICT (id) DO NOTHING RETURNING id",
        (event_id,),
    ).fetchone()
    if claimed is None:
        return False
    conn.execute(
        "UPDATE balances SET cents = cents + %s WHERE id = %s", (cents, account_id)
    )
    return True
```

## Idempotency `audit.json` Deliverable Example

Validate this refund-webhook ledger locally using `python3 <this-skill>/scripts/validate_audit.py idempotency-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "idempotency-sample",
  "repository": {
    "identity": "acme/ledger-consumer",
    "revision": "b820d44",
    "dirty": false,
    "snapshot": "git-tree:b820d44"
  },
  "scope": [
    "src/consumers/refund_worker.py"
  ],
  "coverage": [
    {
      "topic": "idempotency",
      "scope": "src/consumers/refund_worker.py",
      "status": "inspected",
      "evidence_ids": [
        "E-1101"
      ],
      "notes": "Inspected SQS at-least-once consumer dedup key claim and balance mutation.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-1101",
      "dimension": "idempotency",
      "scope": "handle_refund_event",
      "intended": "Duplicate SQS deliveries of the same event_id credit account balance once.",
      "observed": "Dedup table is queried via non-locking SELECT before balance UPDATE, and IntegrityError on late INSERT is caught at outer worker level after autocommit.",
      "enforcement": "non-atomic SELECT before autocommit UPDATE",
      "evidence_ids": [
        "E-1101"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-1101",
      "path": "src/consumers/refund_worker.py",
      "line_start": 19,
      "line_end": 31,
      "symbol": "handle_refund_event",
      "context": "Runs autocommit UPDATE balances before INSERT INTO seen_events."
    }
  ],
  "findings": [
    {
      "id": "F-1101",
      "topic": "idempotency",
      "tags": [
        "transactions",
        "concurrency"
      ],
      "title": "Autocommit balance credit before seen_events claim double-credits duplicate deliveries",
      "status": "supported",
      "severity": "critical",
      "confidence": "high",
      "contract_ids": [
        "C-1101"
      ],
      "evidence_ids": [
        "E-1101"
      ],
      "preconditions": "SQS delivers duplicate refund message concurrently across two worker processes.",
      "sequence": [
        "Worker 1 and Worker 2 both SELECT None from seen_events for event_id 'evt-9'.",
        "Both workers autocommit UPDATE balances SET cents = cents + 5000.",
        "Worker 2 fails unique INSERT into seen_events after the extra credit has already committed."
      ],
      "expected": "Claiming seen_events and crediting balances execute in one transaction gated by INSERT ... ON CONFLICT DO NOTHING.",
      "actual": "Account receives 10000 cents instead of 5000 cents.",
      "impact": "Duplicate monetary refund credit on standard SQS redelivery.",
      "root_cause": "Deduplication marker is claimed after an autocommitted side effect rather than atomically gating it.",
      "counterevidence": "Strictly serial deliveries spaced minutes apart hit the SELECT guard.",
      "remedy": "Wrap handler in an explicit transaction and gate execution on INSERT INTO seen_events ... ON CONFLICT DO NOTHING RETURNING id.",
      "acceptance": [
        "Two concurrent workers processing identical event_id commit exactly one balance increment."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified autocommit connection mode and statement order in refund_worker.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

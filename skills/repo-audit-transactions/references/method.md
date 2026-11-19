# Implicit Transaction Boundaries: Specialist Method and Examples

## Tracing Multi-Step Atomicity, Nested Commits, and Dual Writes

1. Map every logical unit of work that mutates more than one durable record or external channel (SQL row + audit table, SQL commit + message broker, object-store blob + metadata row), verifying whether all mutations enlist in the same transaction context.
2. Inspect repository helpers called inside a transaction block for hidden `session.commit()`, separate connection-pool checkouts, or fire-and-forget async tasks that publish uncommitted or unrecoverable state.
3. Respect explicit state-machine workflows (`status = 'PROVISIONING'` committed intentionally before external cloud API call with a sweeper job) when recovery is durable and documented.

## Failure Trace vs. Safe Control: Helper Opening Separate Autocommit Connection

```python
# Defect: audit_trail helper checks out an independent autocommit connection; persists even on rollback
def approve_payout(tx_conn, payout_id: str, actor: str) -> None:
    tx_conn.execute("UPDATE payouts SET state = 'APPROVED' WHERE id = %s", (payout_id,))
    write_compliance_entry(
        payout_id, actor
    )  # opens pool.getconn() with autocommit=True!
    verify_daily_limit(
        tx_conn, actor
    )  # raises LimitExceeded -> outer tx_conn rolls back!


# Safe control: pass active transaction cursor/session so both tables commit or roll back atomically
def approve_payout_safe(tx_conn, payout_id: str, actor: str) -> None:
    verify_daily_limit(tx_conn, actor)
    tx_conn.execute("UPDATE payouts SET state = 'APPROVED' WHERE id = %s", (payout_id,))
    tx_conn.execute(
        "INSERT INTO compliance_log (payout_id, actor) VALUES (%s, %s)",
        (payout_id, actor),
    )
```

## Transactions `audit.json` Deliverable Example

Validate this checkout-outbox ledger locally using `python3 <this-skill>/scripts/validate_audit.py transactions-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "transactions-sample",
  "repository": {
    "identity": "acme/treasury-core",
    "revision": "04ad881",
    "dirty": false,
    "snapshot": "git-tree:04ad881"
  },
  "scope": [
    "src/treasury/payouts.py",
    "src/treasury/compliance.py"
  ],
  "coverage": [
    {
      "topic": "transactions",
      "scope": "src/treasury/",
      "status": "inspected",
      "evidence_ids": [
        "E-1001"
      ],
      "notes": "Traced connection propagation across payouts.py and compliance.py.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-1001",
      "dimension": "transactions",
      "scope": "approve_payout",
      "intended": "Compliance approval records exist if and only if the payout state transition committed.",
      "observed": "write_compliance_entry commits on an independent connection before verify_daily_limit can roll back payouts.",
      "enforcement": "split connection scope across payouts.py and compliance.py",
      "evidence_ids": [
        "E-1001"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-1001",
      "path": "src/treasury/payouts.py",
      "line_start": 44,
      "line_end": 53,
      "symbol": "approve_payout",
      "context": "Calls write_compliance_entry() before verify_daily_limit() raises LimitExceeded."
    }
  ],
  "findings": [
    {
      "id": "F-1001",
      "topic": "transactions",
      "tags": [
        "data-integrity"
      ],
      "title": "Rolled-back payout approvals leave orphaned committed rows in compliance_log",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-1001"
      ],
      "evidence_ids": [
        "E-1001"
      ],
      "preconditions": "Approver exceeds daily approval limit during approve_payout.",
      "sequence": [
        "approve_payout stages UPDATE payouts on tx_conn.",
        "write_compliance_entry acquires a separate autocommit connection and commits INSERT.",
        "verify_daily_limit raises LimitExceeded, rolling back tx_conn."
      ],
      "expected": "Both payouts UPDATE and compliance_log INSERT roll back together.",
      "actual": "compliance_log records an approved payout while payouts remains PENDING.",
      "impact": "Corrupted regulatory audit ledger disagreeing with actual payout state.",
      "root_cause": "Helper fails to enlist in caller transaction unit of work.",
      "counterevidence": "Approvals within daily limit commit both connections.",
      "remedy": "Require write_compliance_entry to accept the caller's transactional connection instead of acquiring a new autocommit connection.",
      "acceptance": [
        "Raising LimitExceeded in verify_daily_limit leaves zero new rows in compliance_log."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static connection-scope trace across payouts.py and compliance.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

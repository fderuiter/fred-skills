# Implicit Invariants: Specialist Method and Examples

## Auditing Aggregate Conservation and Capacity Predicates

1. Locate domain quantities, uniqueness rules, and cross-field predicates (`reserved <= capacity`, `sum(splits) == total_cents`, non-empty primary owner) across constructors, SQL DDL (`CHECK`, `UNIQUE`), and service mutators.
2. Inspect every write path—including bulk imports, admin backfills, and concurrent seat claims—to verify whether the predicate is enforced atomically at commit time rather than checked in application memory under `READ COMMITTED`.
3. Distinguish transient in-transaction steps (where debit precedes credit inside a single rollback-safe SQL transaction) from genuine invariant escapes visible to concurrent readers or persisted on error.

## Failure Trace vs. Safe Control: Concurrent Seat Reservation Overbooking

```sql
-- Vulnerable pattern: application reads remaining seats under READ COMMITTED without row lock
SELECT capacity - reserved_count AS available FROM workshops WHERE id = $1;
-- Two concurrent transactions both observe available = 1, then both execute:
UPDATE workshops SET reserved_count = reserved_count + 1 WHERE id = $1;

-- Safe control: atomic guarded UPDATE + database CHECK (reserved_count <= capacity)
UPDATE workshops
   SET reserved_count = reserved_count + 1
 WHERE id = $1
   AND reserved_count < capacity
RETURNING reserved_count;
```

## Invariants `audit.json` Deliverable Example

Validate this ledger-balance ledger locally using `python3 <this-skill>/scripts/validate_audit.py invariants-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "invariants-sample",
  "repository": {
    "identity": "acme/booking-api",
    "revision": "29c4e11",
    "dirty": false,
    "snapshot": "git-tree:29c4e11"
  },
  "scope": [
    "src/bookings/reserve.py",
    "migrations/004_workshops.sql"
  ],
  "coverage": [
    {
      "topic": "invariants",
      "scope": "src/bookings/reserve.py",
      "status": "inspected",
      "evidence_ids": [
        "E-201"
      ],
      "notes": "Checked seat reservation SQL queries and workshops table constraints.",
      "next_action": "Review transaction isolation settings in repo-audit-transactions if multi-table locks are added."
    }
  ],
  "contracts": [
    {
      "id": "C-201",
      "dimension": "invariants",
      "scope": "workshops.reserved_count",
      "intended": "Committed reserved_count never exceeds workshop capacity.",
      "observed": "Application checks capacity in Python before issuing an unguarded SQL increment without a CHECK constraint.",
      "enforcement": "non-atomic pre-read in src/bookings/reserve.py",
      "evidence_ids": [
        "E-201"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-201",
      "path": "src/bookings/reserve.py",
      "line_start": 31,
      "line_end": 46,
      "symbol": "reserve_workshop_seat",
      "context": "Reads workshop.reserved_count < workshop.capacity, then executes unguarded UPDATE increment."
    }
  ],
  "findings": [
    {
      "id": "F-201",
      "topic": "invariants",
      "tags": [
        "concurrency",
        "transactions"
      ],
      "title": "Unguarded seat increment allows concurrent bookings to exceed workshop capacity",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-201"
      ],
      "evidence_ids": [
        "E-201"
      ],
      "preconditions": "One seat remains (reserved_count == capacity - 1) and two booking requests arrive concurrently.",
      "sequence": [
        "Tx A and Tx B both SELECT reserved_count = 19, capacity = 20.",
        "Tx A commits UPDATE setting reserved_count = 20.",
        "Tx B commits UPDATE setting reserved_count = 21."
      ],
      "expected": "At most one transaction increments reserved_count to 20; the second fails with SeatUnavailableError.",
      "actual": "Both commit, leaving reserved_count = 21 > capacity = 20.",
      "impact": "Overbooked capacity and downstream fulfillment rejection.",
      "root_cause": "Capacity invariant enforced via non-locking SELECT instead of conditional UPDATE or SQL CHECK constraint.",
      "counterevidence": "Single-threaded test suite executes reservations serially and never triggers overlap.",
      "remedy": "Add AND reserved_count < capacity to the UPDATE predicate, check rowcount == 1, and add CHECK (reserved_count <= capacity) in SQL.",
      "acceptance": [
        "Two concurrent callers competing for the final seat result in exactly one committed booking and one clean rejection."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified missing WHERE guard in reserve.py and missing CHECK in 004_workshops.sql."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

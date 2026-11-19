# Worked Examples: Repository Audit Program

Load these examples on demand when initializing or resuming a multi-pass `audit.json` run
ledger across critical repository workflows. Never copy illustrative IDs or paths as evidence
about an unaudited repository.

## 1. Input Scope and Multi-Pass Coordination Slice

Given a user request to audit checkout and webhook fulfillment on a clean snapshot while
deferring UI accessibility topics:

```text
Target repository: acme/payments-api @ 8f3d2a1 (clean worktree)
Requested scope: src/checkout/, src/webhooks/
Available local runner: pytest -q
Coordination mode: sequential single-ledger run
Pass 1 focus: behavioral-contracts, state-machines, concurrency
Pass 2 focus: idempotency, transactions
Deferred scope: accessibility-state (not-applicable: headless JSON API)
```

## 2. Valid `audit.json` Run Envelope Output

The coordinator records reviewed, partial, and `not-applicable` topic coverage alongside
cross-referenced `contracts`, `evidence`, `findings`, `issues`, and `checks`, then validates
the file with `python3 <this-skill>/scripts/validate_audit.py audit.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "run-2026-11-19-payments",
  "repository": {
    "identity": "acme/payments-api",
    "revision": "8f3d2a1c90e4",
    "dirty": false,
    "snapshot": "git-tree:8f3d2a1c90e4"
  },
  "scope": [
    "src/checkout/",
    "src/webhooks/"
  ],
  "coverage": [
    {
      "topic": "idempotency",
      "scope": "src/webhooks/stripe_handler.py",
      "status": "inspected",
      "evidence_ids": [
        "E-001",
        "E-002"
      ],
      "notes": "Traced webhook event deduplication and order fulfillment commit order.",
      "next_action": "Proceed to repo-audit-synthesis for issue consolidation."
    },
    {
      "topic": "accessibility-state",
      "scope": "src/",
      "status": "not-applicable",
      "evidence_ids": [
        "E-001"
      ],
      "notes": "Service exposes JSON HTTP endpoints only without HTML/UI components.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-001",
      "dimension": "idempotency",
      "scope": "handle_charge_succeeded",
      "intended": "Duplicate webhook deliveries with identical event_id fulfill an order at most once.",
      "observed": "Deduplication key is written to Redis after Postgres order fulfillment commits.",
      "enforcement": "post-commit cache write in src/webhooks/stripe_handler.py",
      "evidence_ids": [
        "E-001",
        "E-002"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-001",
      "path": "src/webhooks/stripe_handler.py",
      "line_start": 41,
      "line_end": 58,
      "symbol": "handle_charge_succeeded",
      "context": "Checks redis.get(event_id) outside DB transaction before committing fulfillment."
    },
    {
      "id": "E-002",
      "path": "tests/test_stripe_handler.py",
      "line_start": 18,
      "line_end": 34,
      "symbol": "test_duplicate_event_sequential",
      "context": "Sequential replay safe control passes when Redis write succeeds."
    }
  ],
  "findings": [
    {
      "id": "F-001",
      "topic": "idempotency",
      "tags": [
        "transactions",
        "concurrency"
      ],
      "title": "Concurrent or Redis-evicted webhook replay fulfills order twice",
      "status": "confirmed",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-001"
      ],
      "evidence_ids": [
        "E-001",
        "E-002"
      ],
      "preconditions": "Stripe retries charge.succeeded concurrently or after Redis cache eviction before TTL.",
      "sequence": [
        "Delivery A and Delivery B both read redis.get(event_id) == None at line 44.",
        "Delivery A commits fulfillment row in Postgres.",
        "Delivery B commits a second fulfillment row before setting Redis key."
      ],
      "expected": "Second delivery observes unique event_id constraint in Postgres and exits idempotently.",
      "actual": "Two fulfillment shipments are inserted for one charge event.",
      "impact": "Duplicate physical shipment and ledger imbalance on webhook retry.",
      "root_cause": "Deduplication state lives in non-transactional cache checked before DB commit.",
      "counterevidence": "Sequential retries within Redis TTL are blocked when Redis remains reachable (E-002).",
      "remedy": "Insert event_id into a Postgres processed_events table with a UNIQUE constraint inside the same transaction as order fulfillment.",
      "acceptance": [
        "Concurrent deliveries of identical event_id commit exactly one fulfillment row.",
        "Sequential valid distinct event_ids continue to fulfill normally."
      ],
      "validation": {
        "state": "executed",
        "commands": [
          "pytest -q tests/test_stripe_handler.py"
        ],
        "result": "Confirmed sequential test passes while two-barrier concurrent harness inserts 2 rows."
      },
      "related_findings": []
    }
  ],
  "issues": [
    {
      "id": "ISS-001",
      "title": "Enforce transactional Postgres uniqueness for Stripe webhook event_id",
      "finding_ids": [
        "F-001"
      ],
      "depends_on": [],
      "related_issues": [],
      "existing_url": null,
      "publication": "draft"
    }
  ],
  "limitations": [
    "Live Stripe webhook signing rotation config in production Vault was not inspected."
  ],
  "checks": [
    {
      "name": "webhook-unit-suite",
      "command": "pytest -q tests/test_stripe_handler.py",
      "state": "executed",
      "result": "4 passed in 0.42s"
    }
  ]
}
```

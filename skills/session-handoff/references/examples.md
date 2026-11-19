# Worked Examples: Session Handoff

Use these examples on demand when compacting an active debugging or implementation session
into a cold-start continuation handoff and validating the exit record via
`scripts/validate_workflow.py`.

## 1. Dirty Working Tree Inspection Before Handoff

Inspect exact repository state and recent command outcomes without staging or mutating
files:

```sh
git rev-parse --short HEAD
git status --short
python -m unittest tests/billing/test_webhook_retry.py
```

Observed working state (`a91c4e2`, dirty): `tests/billing/test_webhook_retry.py` has an
added failing regression test reproducing duplicate webhook settlement on HTTP 502, while
`src/billing/gateway.py` remains untouched. Earlier chat history tested raising socket
timeout from `2s` to `10s`, which was refuted because `gateway_trace.log` showed upstream
`502 Bad Gateway` arrived in `38ms`.

## 2. Valid Session Handoff Artifact (`assets/session-handoff.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py session-handoff.json`:

```json
{
  "schema_version": "1.0",
  "objective": "Prevent duplicate ledger settlement when webhook delivery retries after fast upstream 502 responses.",
  "repository": {
    "path": ".",
    "revision": "a91c4e2",
    "dirty": true,
    "branch": "fix/webhook-idempotency"
  },
  "verified_facts": [
    "src/billing/webhook.py:42 inserts ledger rows before checking idempotency_key uniqueness.",
    "tests/billing/test_webhook_retry.py reproduces two ledger entries for event evt_901 under simulated 502 retry."
  ],
  "assumptions": [
    "PostgreSQL unique constraint uq_ledger_idempotency_key exists in all staging and production schemas."
  ],
  "completed_steps": [
    "Traced duplicate charge path through BillingWebhookHandler.handle_event.",
    "Added deterministic retry regression test in tests/billing/test_webhook_retry.py."
  ],
  "dead_ends": [
    {
      "hypothesis": "Client socket timeout caused premature retry while initial request was still committing.",
      "reason_rejected": "Upstream returned explicit HTTP 502 in 38ms well below the 2000ms timeout.",
      "evidence": "fixtures/logs/gateway_trace.log:19"
    }
  ],
  "touched_files": [
    "tests/billing/test_webhook_retry.py"
  ],
  "untouched_scope": [
    "src/billing/gateway.py",
    "migrations/"
  ],
  "commands_run": [
    {
      "command": "python -m unittest tests/billing/test_webhook_retry.py",
      "outcome": "failed",
      "summary": "AssertionError: expected 1 ledger row for evt_901, observed 2."
    }
  ],
  "blockers": [],
  "next_action": "Enforce ON CONFLICT (idempotency_key) DO NOTHING in src/billing/webhook.py and re-run python -m unittest tests/billing/test_webhook_retry.py."
}
```

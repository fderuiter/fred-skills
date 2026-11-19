# Worked Examples: Remediation Reconciliation

Use these examples on demand when reconciling findings across audit snapshots and enforcing
`verified-fixed` proof requirements via `scripts/validate_workflow.py`.

## 1. Cross-Run Finding Inputs and State Transition Decisions

```text
Prior run (run-2026-11-10 @ 9b41e20) -> Current snapshot (d4e812b):
- Prior F-014 (SessionCache.refresh generation race, fingerprint sha256:7c2a...):
  Matched to commit d4e812b with complete repo-verify-fix proof -> transition to "verified-fixed".
- Prior F-019 (BillingWebhookController timestamp check, fingerprint sha256:91ef...):
  Code patched in d4e812b, but regression suite has NOT been executed yet -> keep at "fixed-pending-verification" (verification: null).
```

Note that `reconciliation.schema.json` rejects `"state": "verified-fixed"` unless
`verification` supplies `original_reproduced: true`, `regression_passed: true`, and
`safe_controls_passed: true`.

## 2. Valid Reconciliation Ledger (`assets/reconciliation.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py reconciliation.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "reconcile-2026-11-19",
  "records": [
    {
      "at": "2026-11-19T14:30:00Z",
      "finding_id": "F-014",
      "fingerprint": "session_cache.mjs:SessionCache.refresh:generation-abort-order",
      "state": "verified-fixed",
      "note": "Generation increment moved before prior controller abort and verified across both completion orders.",
      "verification": {
        "schema_version": "1.0",
        "finding_id": "F-014",
        "before_revision": "9b41e20",
        "after_revision": "d4e812b",
        "original_reproduced": true,
        "regression_passed": true,
        "safe_controls_passed": true,
        "commands": [
          "node --test tests/auth/session_cache.test.mjs",
          "node --test tests/auth/"
        ],
        "result": "Pre-fix failed on 9b41e20; regression and 14 auth safe controls passed on d4e812b."
      }
    },
    {
      "at": "2026-11-19T14:30:00Z",
      "finding_id": "F-019",
      "fingerprint": "billing.mjs:BillingWebhookController:timestamp-tolerance",
      "state": "fixed-pending-verification",
      "note": "Timestamp tolerance guard added in src/webhooks/billing.mjs, awaiting pre/post execution proof.",
      "verification": null
    }
  ],
  "notes": "1 finding verified-fixed, 1 finding awaiting execution proof before closure."
}
```

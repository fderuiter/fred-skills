# Worked Examples: Fix Verification

Load these examples on demand when verifying before/after snapshots against original failure
reproduction, independent regression checks, and close safe controls.

## 1. Three-Part Before/After Execution Trace

```text
1. Pre-fix snapshot (git worktree @ 9b41e20):
   $ node --test /tmp/verify-f014/session_cache_regression.test.mjs
   -> FAIL (exit 1): token cleared to null during synchronous abort of prior controller (original_reproduced = true)

2. Post-fix snapshot (git worktree @ d4e812b):
   $ node --test tests/auth/session_cache.test.mjs
   -> PASS (exit 0): 3/3 regression assertions passed in both A->B and B->A completion orders (regression_passed = true)

3. Safe controls & neighbor auth suite (@ d4e812b):
   $ node --test tests/auth/
   -> PASS (exit 0): 14/14 auth session and sequential token renewal tests passed (safe_controls_passed = true)
```

## 2. Valid Fix Verification Proof (`assets/verification.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py verification.json`:

```json
{
  "schema_version": "1.0",
  "finding_id": "F-014",
  "before_revision": "9b41e20",
  "after_revision": "d4e812b",
  "original_reproduced": true,
  "regression_passed": true,
  "safe_controls_passed": true,
  "commands": [
    "git worktree add --detach /tmp/pre-9b41e20 9b41e20 && node --test /tmp/verify-f014/session_cache_regression.test.mjs",
    "node --test tests/auth/session_cache.test.mjs",
    "node --test tests/auth/"
  ],
  "result": "Original race reproduced on 9b41e20 (exit 1); both completion orders, synchronous abort reentrancy, and 14 existing auth safe-control tests passed on d4e812b (exit 0).",
  "notes": "Eligible to transition F-014 to verified-fixed during repo-reconcile-findings."
}
```

# Worked Examples: Scoped Implementation Planning

Use these examples on demand when grouping verified findings into acyclic implementation
tasks and validating `plan.json` with `scripts/validate_workflow.py`.

## 1. Input Findings Requiring Root-Cause Grouping and Dependency Ordering

```text
Verified inputs @ revision 9b41e20:
- F-014 (confirmed, high): SessionCache.refresh aborts prior controller before incrementing generation (src/auth/session_cache.mjs)
- F-015 (confirmed, medium): SessionCache.logout clears token without incrementing generation, allowing in-flight refresh to repopulate token after logout (src/auth/session_cache.mjs)
- F-019 (supported, high): BillingWebhookController skips timestamp tolerance check on replay header (src/webhooks/billing.mjs)
```

Planning decision:
- Combine `F-014` and `F-015` into `TASK-1` (`SessionCache` generation authority) followed by
  `TASK-2` (`blocked_by: ["TASK-1"]`) adding deterministic regression tests.
- Keep `F-019` as an independent `TASK-3` with empty `blocked_by: []` so webhook hardening
  ships without waiting on auth cache work.

## 2. Valid Implementation Plan (`assets/plan.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py plan.json`:

```json
{
  "schema_version": "1.0",
  "plan_id": "plan-2026-11-19-auth-webhooks",
  "title": "Enforce SessionCache generation fencing and webhook timestamp window",
  "task_breakdowns": [
    {
      "task_id": "TASK-1",
      "title": "Advance SessionCache generation before aborting superseded or logged-out requests",
      "description": "Fixes F-014 and F-015 at the single generation counter authority in SessionCache.",
      "file_scopes": [
        "src/auth/session_cache.mjs"
      ],
      "blocked_by": [],
      "acceptance_criteria": [
        "SessionCache.refresh increments this.generation before calling currentAbort.abort().",
        "SessionCache.logout increments this.generation and aborts any in-flight controller so pending refreshes cannot write this.token."
      ]
    },
    {
      "task_id": "TASK-2",
      "title": "Add deterministic deferred-promise regressions for SessionCache refresh and logout",
      "description": "Covers synchronous abort ordering, post-logout resolution, and sequential safe controls.",
      "file_scopes": [
        "tests/auth/session_cache.test.mjs"
      ],
      "blocked_by": [
        "TASK-1"
      ],
      "acceptance_criteria": [
        "node --test tests/auth/session_cache.test.mjs passes deterministically without timers."
      ]
    },
    {
      "task_id": "TASK-3",
      "title": "Reject webhook signatures outside 300-second timestamp tolerance",
      "description": "Independently shippable fix for F-019 in BillingWebhookController.",
      "file_scopes": [
        "src/webhooks/billing.mjs",
        "tests/webhooks/billing.test.mjs"
      ],
      "blocked_by": [],
      "acceptance_criteria": [
        "Requests older than 300s or >60s in the future return HTTP 400 before payload execution.",
        "Valid signed webhooks within 300s continue returning HTTP 200."
      ]
    }
  ],
  "dependency_order": [
    "TASK-1",
    "TASK-2",
    "TASK-3"
  ],
  "file_scopes": [
    "src/auth/session_cache.mjs",
    "tests/auth/session_cache.test.mjs",
    "src/webhooks/billing.mjs",
    "tests/webhooks/billing.test.mjs"
  ],
  "acceptance_criteria": [
    "All targeted node --test suites pass with zero regressions on sequential safe controls.",
    "No files outside declared file_scopes are modified."
  ],
  "notes": "TASK-1 and TASK-3 touch disjoint modules and may execute in parallel if authorized."
}
```

---
name: review-invariant-violations
description: "Verify pull request patches for broken safety assumptions, rollback regressions, and unbalanced resource lifecycles. Use when inspecting proposed diff hunks for broken preconditions, race hazards, or corrupted state transitions before merge."
---

# Invariant Verification

Read the [review protocol](references/review-protocol.md) before reviewing invariant preservation.
Focus on behavioral contracts, state consistency, and regression hazards.

## Workflow

1. Identify core behavioral invariants, state transitions, and concurrency safety rules.
2. Check modified logic for invariant violations, race conditions, or unhandled null states.
3. Verify that error handling preserves transactional integrity and rollback guarantees.
4. Ensure resource acquisition and release lifecycles remain balanced across exit paths.
5. Highlight subtle timing, reentrancy, or ordering bugs introduced by the proposed diff.

## Deliver

Record invariant issues using the [review template](assets/review-template.md).
Verify findings schema compliance with [review schema](assets/review.schema.json).
Provide actionable defect explanations with code citations and concrete violation scenarios.
Do not modify application code or conflate unverified hypotheses with confirmed defects; preserve the audit-only default unless explicitly authorized.

---
name: review-invariant-violations
description: "Inspect PR diff hunks for broken contracts. Use when reviewing code changes. Do not use for audits."
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
Verify findings schema compliance with [review schema](assets/review.schema.json), [worked examples](references/examples.md), and `python3 <this-skill>/scripts/validate_review.py <artifact.json>`.
Provide actionable defect explanations with code citations and concrete violation scenarios.
Do not modify application code or conflate unverified hypotheses with confirmed defects; preserve the audit-only default unless explicitly authorized.

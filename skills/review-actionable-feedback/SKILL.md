---
name: review-actionable-feedback
description: "Consolidate, deduplicate, and calibrate code review findings into an evidence-backed verdict and minimal remediation guidance. Use when synthesizing specialist review findings, calibrating blocking vs advisory severity, or producing final pull request feedback."
---

# Actionable Feedback Synthesis

Consolidate specialist review leads into deduplicated, severity-calibrated `findings` and an honest `verdict` (`approve`, `request_changes`, or `comment`).
Use verified diff evidence and `review.json` state; do not inflate stylistic preferences into blocking defects or fabricate unverified line citations.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Follow its read-only
   review default, severity rubric, and mutation guardrails.
2. Read [investigation method](references/method.md). Re-verify cited `file:line` anchors,
   deduplicate multi-symptom cascades into single root causes, and check safe controls before finalizing `verdict`.
3. Reuse and refine the workspace `review.json` accumulated from upstream review specialists, or
   synthesize directly from the diff when invoked standalone.

## Inventory

Candidate review findings across diff, compatibility, security, concurrency, and invariant passes; cited source files/lines; test gaps; and repository review guidelines.

## Investigation

1. Merge duplicate observations that stem from one underlying defect (for example, a missing null guard reported separately as a diff change and an invariant break) under the primary `file:line` root cause.
2. Calibrate `severity` (`critical`, `high`, `medium`, `low`, `advisory`) strictly by reachable impact—reserving `critical`/`high` for exploitable boundary breaks, data corruption, crashes, or contract breakage.
3. Write concise, constructive `recommendation` fields specifying the smallest safe fix that preserves surrounding callers and existing conventions.
4. Set `verdict` deterministically: `request_changes` when `critical` or `high` defects remain unresolved, `comment` when medium/advisory trade-offs or unverified questions exist, and `approve` only when reviewed surfaces are clean.
5. Summarize preserved invariants, refuted false-positive controls, and bounded follow-up checks in `summary` and the narrative report.

## Challenge and validate

Use this failure shape only as a discovery lead: Five noisy `high`-severity nitpicks on formatting and hypothetical future extensibility bury one genuine `high`-severity missing authorization check, or trigger `approve` despite a confirmed regression.

Actively test this counterexample: A well-scoped diff with zero confirmed defects and verified safe controls warrants a crisp `approve` or `comment` summary rather than invented `advisory` churn.

Re-verify every finding's `file` and `line` against the target snapshot and confirm `verdict` aligns with the highest remaining `severity`; validate `review.json` against the schema.

Check project contribution and style guides before commenting on conventions. Read
[research references](references/sources.md) only when calibrating severity or feedback clarity.
No external tool is required.

## Deliver and hand off

Render the human-readable review summary using [review template](assets/review-template.md).
Write the deduplicated `verdict`, `summary`, and `findings` to `review.json` validated against
[review schema](assets/review.schema.json). Schema validity does not guarantee calibrated judgment.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed synthesis.

Hand off open test-specification follow-ups to `test-case-generation` or deeper specialist leads back to
`review-api-compatibility`, `review-security-boundaries`, or `review-concurrency-hazards` when unresolved.
Do not automatically load sibling skills, post remote PR comments, or modify repository files outside the run workspace without explicit user permission.

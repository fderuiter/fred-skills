---
name: review-diff-analysis
description: "Map pull request diff blast radius, modified execution paths, deleted safeguards, and affected upstream callers or downstream consumers. Use when triaging pull request hunks, tracing changed symbol callers, or scoping differential review impact."
---

# Differential Blast-Radius Analysis

Trace modified diff hunks end to end across callers, callees, configuration, and tests before evaluating downstream regressions.
Use the supplied base/head diff and snapshot; do not judge a hunk in isolation from untouched callers.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Honor its read-only
   review default, `file:line` evidence requirement, and mutation guardrails.
2. Read [investigation method](references/method.md). Resolve changed files, renamed symbols,
   deleted checks, and affected call sites before promoting a diff observation to a finding.
3. Reuse the workspace `review.json` if present so sibling specialist findings remain intact.
   Sibling review skills are optional deeper passes, not automatic prerequisites.

## Inventory

Changed files and diff hunks, modified function/class signatures, deleted preconditions or error handlers, configuration defaults, and untouched upstream/downstream callers.

## Investigation

1. Reconstruct before-versus-after control flow and data flow for each non-trivial hunk rather than scanning added lines syntactically.
2. Locate untouched callers, subclasses, CLI flags, and configuration loaders whose assumptions are invalidated by modified behavior or return shapes.
3. Flag silently removed validation guards, broadened catch blocks, altered default parameters, and unreferenced dead branches introduced by partial refactors.
4. Separate cosmetic reformatting or mechanical renames from behavioral changes so specialist review focuses on high-blast-radius surfaces.
5. Record concrete `findings` (`id`, `title`, `severity`, `file`, `line`, `description`, `recommendation`) in `review.json` for confirmed diff regressions and nominate specialist leads.

## Challenge and validate

Use this failure shape only as a discovery lead: A helper function changes its empty-result return value from `[]` to `None`, while three untouched callers outside the diff still iterate over the return value directly.

Actively test this counterexample: Changing an internal helper's signature when every single repository call site is updated consistently within the same commit causes zero caller skew.

Search all repository references to each modified symbol before claiming broken callers; verify line numbers against the post-change file snapshot.

Inspect repository diff and search tooling first. Read
[research references](references/sources.md) only when selecting change-impact slicing heuristics.
No external tool is required.

## Deliver and hand off

Format human-readable review notes with [review template](assets/review-template.md).
Update `review.json` and validate strictly against [review schema](assets/review.schema.json).
Schema conformance validates payload fields, not diff correctness.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed reviews.

Offer targeted handoffs to `review-api-compatibility`, `review-security-boundaries`,
`review-concurrency-hazards`, `review-invariant-violations`, and `review-actionable-feedback` when specialized risks appear.
Do not automatically load sibling skills or modify repository files outside the run workspace without explicit user permission.

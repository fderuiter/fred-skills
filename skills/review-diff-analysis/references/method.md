# Investigation method: Differential Blast-Radius Analysis

## Scope selection

Partition the diff into pure formatting/renames versus behavioral hunks, then trace each
behavioral hunk outward to its immediate and transitive callers, config bindings, and tests.
Do not stop at the diff context window when modified functions are called from other files.

## Contract worksheet

For every modified symbol, record its before/after preconditions, return/error semantics,
side effects, deleted guards, and complete caller inventory. Flag callers that were not updated
alongside behavioral shifts.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each behavioral hunk. Record both
confirmed impact regressions and verified safe refactors.

## Adversarial case

A helper function changes its empty-result return value from `[]` to `None`, while three
untouched callers outside the diff still iterate over the return value directly.

Locate all call sites in the snapshot and trace how each handles the new return or error shape.
If every caller guards against `None` or was updated in the diff, refute the candidate cleanly.

## Safe control

Changing an internal helper's signature when every single repository call site is updated
consistently within the same commit causes zero caller skew.

Confirm private visibility and exhaustive call-site updates before flagging internal signature
changes.

## Independent validation

Verify `file` paths and 1-indexed `line` numbers on head source, run non-mutating reference
searches across the snapshot, and validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Establish the diff inventory and caller blast radius, then hand off public wire/SDK breakage
to `review-api-compatibility`, auth/trust regressions to `review-security-boundaries`, lock/race
changes to `review-concurrency-hazards`, and final deduplication to `review-actionable-feedback`.

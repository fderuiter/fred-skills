# Investigation method: Actionable Feedback Synthesis

## Scope selection

Review all candidate findings in `review.json` alongside the diff hunks and cited source files
end to end before emitting the final verdict. Do not pass through unverified upstream claims
without checking the cited `file:line` in the repository snapshot.

## Contract worksheet

For each surviving finding, confirm its root-cause identity, exact `file:line`, reachable failure
scenario, calibrated `severity`, and bounded `recommendation`. Ensure `verdict` is consistent
with the maximum confirmed severity.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` across all candidate findings. Record deduplicated
merges and refuted false positives explicitly in the review summary.

## Adversarial case

Five noisy `high`-severity nitpicks on formatting and hypothetical future extensibility bury one
genuine `high`-severity missing authorization check, or trigger `approve` despite a confirmed
regression.

Prune speculative style opinions and verify that `verdict` is `request_changes` whenever any
confirmed `critical` or `high` finding remains.

## Safe control

A well-scoped diff with zero confirmed defects and verified safe controls warrants a crisp
`approve` or `comment` summary rather than invented `advisory` churn.

Allow `findings: []` when the change is genuinely sound; never pad `review.json` to appear thorough.

## Independent validation

Re-check every `file` and 1-indexed `line` against the head snapshot, verify `verdict` consistency,
and validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Own final deduplication, severity calibration, remediation wording, and `verdict` selection in
`review.json`, while preserving specialist attribution and deferring test authoring to
`automated-testing` skills unless explicitly requested.

# Validation record and limits

## Deterministic repository checks

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md) to verify package metadata,
standalone references, schema validity, canonical-resource copies, generated discovery
indexes, and validator regression tests. The tests exercise meaningful rejection paths,
including dangling IDs, false confirmation, refuted findings assigned to defect issues,
dependency cycles, malformed packages, escaped references, and stale generated resources.

These checks run locally and in CI. Passing them means the package/artifact structure
meets the checked rules; it does not mean every semantic claim in an audit is correct.

## Sampled agent runs used during initial authoring

Initial authoring on 13 November 2026 used a synthetic two-file JavaScript repository
whose contract was latest-started operation publication and cancellation invalidation.
This was not an audit of any production repository. No application fix was made.

| Run | Executed work | Observed result and limitation |
| --- | --- | --- |
| No-suite baseline | Fresh-context code review, existing print-only diagnostic, reentrancy reproduction | Found missing publication fencing and synchronous abort-listener reentrancy; those discoveries cannot be attributed uniquely to the suite. |
| Concurrency specialist | Fresh context using the skill and 11 deterministic exact-source schedules | Reproduced both causes, retained four ordinary safe schedule observations, and recorded two refuted candidates with a schema-valid ledger. |
| Coordinator and selected specialists | Fresh context; 12 schedules per class | Reproduced the ordinary publication cause and identified conditional test-oracle hardening. It explicitly excluded synchronous reentrancy and did not discover that second cause. |
| Findings synthesis | Fresh context on source and audit artifacts; reran 11 observations | Kept the two independent causes as related drafts, excluded refuted candidates, and produced a validated acyclic plan. External duplicate checking was unavailable. |
| Refined cancellation specialist | Fresh context; 24 deterministic checks after adding an explicit synchronous callback probe | Recorded 15 passing controls and 9 violations across the two causes; preserved several valid callback/thenable behaviors and unchanged source hashes. |

The checks imported the exact synthetic implementation and used controlled completion
and native abort dispatch without timing sleeps. Harness exit 0 meant the expected
observations, including known failures, were reproduced; it did not mean the intentionally
unsafe fixture was correct. No production system or remote issue record was modified.

## Specified evaluations are not executed evaluations

Each of the 35 skills in the suite includes three scenario specifications in
`references/evaluation-scenarios.json`: relevant failure, safe control, and unrelated
activation request. These 105 specifications are not 105 executed evaluations. They are
authoring inputs for future raw fixtures and fresh-context agent runs; do not supply
their expected answers to the agent performing an audit.

The sampled runs are a small workflow/evidence-discipline check, not a statistical
benchmark or a measured improvement over the baseline. Comprehensive behavior on real
repositories and other agent hosts remains unverified. Record future actual runs,
misses, false positives, and environment constraints when extending the suite.

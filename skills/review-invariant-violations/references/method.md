# Investigation method: Behavioral and State Invariant Verification

## Scope selection

Focus on modified mutators, state transitions, multi-step writes, and resource-management
blocks in the diff, tracing each from entry precondition through normal completion and every
raised exception or early-return branch. Do not infer invariant violations from naming preferences.

## Contract worksheet

Record the exact representation or transition invariant, where it is established, which
operations mutate the guarded fields, what observers rely on it, and which transaction or
cleanup guard restores it on failure.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each stateful diff hunk. Record both
broken failure-exit invariants and verified transactional/RAII safeguards.

## Adversarial case

A diff updates an in-memory status cache before committing the database write, leaving stale
`"paid"` cache state when the commit raises an integrity error.

Trace write ordering between durable stores, caches, and outbound side effects across failure
branches. If cache invalidation runs unconditionally in `finally` or after commit hooks, record
that safe control.

## Safe control

Mutating a request-scoped local struct that is discarded immediately when an exception aborts
the handler cannot leak corrupted state across requests.

Verify whether mutated objects escape the failing stack frame before reporting state corruption.

## Independent validation

Trace exact input and exception triggers line by line on head source, confirm `file:line`
citations, and validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Own single-thread behavioral, state-machine, transaction, and resource invariants here; hand
off multi-thread/async race interleavings to `review-concurrency-hazards`, wire contract drift
to `review-api-compatibility`, and final verdict synthesis to `review-actionable-feedback`.

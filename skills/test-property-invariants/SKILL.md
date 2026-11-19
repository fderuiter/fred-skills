---
name: test-property-invariants
description: "Specify property-based, round-trip, idempotency, and metamorphic test invariants with deterministic seeds and bounded input generators. Use when testing parsers, serializers, normalizers, state-machine sequences, or algebraic domain invariants."
---

# Property-Based and Metamorphic Test Invariants

Formulate universal behavioral invariants—round-trip codecs, idempotency, monotonicity, conservation laws, and state-model equivalence—over bounded, seeded input spaces.
Keep the target repository read-only by default; do not write or modify repository test files outside the run workspace without explicit user permission.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Enforce its
   workspace-only specification default, non-tautological oracle rule, and explicit write-permission gate.
2. Read [investigation method](references/method.md). Inspect codecs, parsers, canonicalizers,
   calculators, and stateful collections before selecting property invariants.
3. Reuse the workspace `testing.json` if present and verify `run.json` `authority.application_changes`
   before creating or editing files outside the run workspace.

## Inventory

Parsers/serializers, path/URL canonicalizers, cryptographic/redaction scrubbers, quota/ledger math, state-machine reducers, and installed property-testing or stdlib random helpers.

## Investigation

1. Identify functions and state transitions governed by universal laws: `decode(encode(x)) == x`, `normalize(normalize(x)) == normalize(x)`, order independence, or balance conservation.
2. Constrain input generators tightly to valid/adversarial domain shapes (Unicode edge codepoints, path separators, NaN/overflow boundaries, duplicate keys) with explicit fixed PRNG seeds and example budgets.
3. Formulate independent relational or reference-model `oracle_type` checks (`output`, `state`, `boundary`, `exception`) that never duplicate the implementation algorithm.
4. Pair every randomized invariant with pinned deterministic minimal counterexamples so CI runs fast and reproducibly without installing unapproved third-party libraries.
5. Record prioritized `test_cases` and `execution_summary` in `testing.json`; write repository test files only when explicit user permission (`--allow-fixes`) is granted.

## Challenge and validate

Use this failure shape only as a discovery lead: A property test generates unconstrained random strings that fail precondition validation 99% of the time (vacuously passing) or uses an unseeded RNG that flakes irreproducibly in CI.

Actively test this counterexample: Fixed enum mappings with four exhaustive cases are verified more clearly and completely by an explicit exhaustive table test than by randomized sampling.

Verify generator hit rates on valid domain preconditions and pin deterministic seeds; validate `testing.json` against the schema.

Check whether `hypothesis`, `fast-check`, `proptest`, or stdlib seeded generators are already available in the repo. Read
[research references](references/sources.md) only when choosing metamorphic or model-based property patterns.
No external tool is required.

## Deliver and hand off

Document property specifications and generator bounds using [testing template](assets/testing-template.md).
Populate `test_cases` and `execution_summary` in `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity confirms JSON structure, not invariant soundness.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `test-case-generation`, `test-flakiness-isolation`,
`test-contract-harnesses`, or `test-suite-verification` when concrete regression pinning or suite execution is needed.
Do not automatically load sibling skills, add unapproved dependencies, or write repository files outside the run workspace without explicit user permission.

---
name: test-gap-analysis
description: "Discover untested behavioral contracts, missing error-path assertions, tautological mocks, and boundary blind spots across existing test suites. Use when auditing assertion coverage, prioritizing missing regression cases, or identifying weak test oracles."
---

# Behavioral Test Gap Analysis

Compare supported repository contracts against actual test assertions to expose high-risk behavioral blind spots.
Use the supplied repository snapshot; do not equate line-coverage percentages with behavioral contract verification.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Follow its
   workspace-only specification default, independent-oracle rules, and repository write guardrails.
2. Read [investigation method](references/method.md). Inspect both production entry points and
   corresponding test assertions before labeling a branch untested.
3. Reuse the workspace `testing.json` if present so sibling test specifications and `execution_summary`
   remain intact. Sibling testing skills are optional specialist continuations.

## Inventory

Public/module entry points, error and rollback branches, boundary input validators, test directories, mock/fixture definitions, and CI test commands.

## Investigation

1. Map critical behavioral guarantees, error returns, and boundary preconditions against existing unit, integration, and end-to-end test files.
2. Distinguish executed-but-unasserted code paths ("assertion-free smoke tests") and over-mocked tests that stub out the very contract under inspection.
3. Pinpoint missing negative/boundary cases—empty inputs, off-by-one limits, malformed encodings, timeout/rollback exits, and permission denials.
4. Rank each uncovered gap in `test_cases` with an explicit `priority` (`critical`, `high`, `medium`, `low`) and independent `oracle_type` (`state`, `exception`, `output`, `boundary`).
5. Record `verified: false` and `execution_summary` (`state: "not_run"` unless baseline suites were actually executed) honestly in `testing.json`.

## Challenge and validate

Use this failure shape only as a discovery lead: A payment handler achieves 95% line coverage because a unit test mocks the ledger repository and asserts only that `mock.save.called` is true, leaving duplicate-charge rollback completely untested.

Actively test this counterexample: Trivial declarative data classes or framework-generated boilerplate covered transitively by integration contract tests do not need dedicated unit-test files.

Check integration and higher-level suites before claiming a module path has zero coverage; validate `testing.json` against the schema.

Inspect the repository's configured test runner (`pytest`, `unittest`, `vitest`, `go test`) first. Read
[research references](references/sources.md) only when evaluating mutation-score or oracle-strength concepts.
No external tool is required.

## Deliver and hand off

Format gap findings with [testing template](assets/testing-template.md).
Record prioritized `test_cases` and `execution_summary` in `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity confirms structure, not oracle independence.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `test-case-generation`, `test-contract-harnesses`,
`test-property-invariants`, `test-flakiness-isolation`, or `test-suite-verification` when concrete specs or harnesses are needed.
Do not automatically load sibling skills or create/modify files outside the run workspace without explicit user permission.

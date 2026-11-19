---
name: test-case-generation
description: "Specify deterministic unit and regression test cases with independent behavioral oracles and clean fixture isolation, writing repository test files only with explicit user permission. Use when designing regression tests, unit test specifications, or boundary input cases."
---

# Deterministic Test Case Specification and Generation

Design prioritized, deterministic `test_cases` with independent behavioral oracles (`state`, `exception`, `output`, `boundary`).
Keep the run workspace read-only with respect to the target repository by default; never write or modify repository test files unless explicit user permission (`--allow-fixes` or explicit authorization) is granted.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Enforce its
   workspace-only specification default, non-tautological oracle rule, and explicit write-permission gate.
2. Read [investigation method](references/method.md). Inspect the repository's existing test
   framework, fixture conventions, and target contracts before specifying test cases.
3. Reuse the workspace `testing.json` if present (for example from `test-gap-analysis`) and check
   `run.json` `authority.application_changes` or explicit user instructions before touching any repository path.

## Inventory

Target component contracts, defect reproductions, test runner configuration, existing fixtures/helpers, temporary-directory teardown patterns, and write-authorization state.

## Investigation

1. Select high-value uncovered contracts or confirmed defects and assign each `test_cases` entry a clear `id`, `name`, `priority`, and `oracle_type`.
2. Choose the narrowest faithful test seam—real domain collaborators and in-memory/temp resources over brittle deep mocks that encode internal call counts.
3. Specify deterministic inputs, explicit preconditions, fixed clocks/seeds, and self-cleaning teardowns so cases run in any order.
4. Derive expected outputs, post-states, or raised exceptions from documented contracts rather than copying production expressions into assertions.
5. Record complete specifications in `testing.json` and the workspace report; write runnable test files into repository test directories only when explicit user permission (`--allow-fixes`) is active, keeping production source unchanged unless separately authorized.

## Challenge and validate

Use this failure shape only as a discovery lead: A generated test asserts `assert discount(p) == round(p * RATE, 2)` by importing the buggy `RATE` constant and formula directly from the module under test, passing even when `RATE` is wrong.

Actively test this counterexample: Parameterized table-driven cases with concrete literal input/expected pairs (`100 -> 85.00`, `0 -> 0.00`, `-1 -> ValueError`) verify behavior independently without duplicating production code.

Verify that every specified assertion fails when the guarded behavior regresses and passes on the intended contract; set `verified: true` only if actually executed.

Inspect existing repository test idioms before drafting code snippets. Read
[research references](references/sources.md) only when selecting test-double or boundary-value design techniques.
No external tool is required.

## Deliver and hand off

Document test case designs and runnable snippets in the workspace using [testing template](assets/testing-template.md).
Update `test_cases` and `execution_summary` in `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity checks JSON structure, not assertion strength.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `test-contract-harnesses`, `test-property-invariants`,
`test-flakiness-isolation`, or `test-suite-verification` when schema harnesses, algebraic properties, or runner execution are needed.
Do not automatically load sibling skills, modify production code, or write test files outside the run workspace without explicit user permission.

# Investigation method: Deterministic Test Case Specification and Generation

## Scope selection

Start from confirmed coverage gaps or defect specifications, inspect the target component's
public contract and existing test conventions, and design minimal, high-signal cases covering
normal, boundary, and error behavior. Check write-authorization state before creating any file
outside the run workspace.

## Contract worksheet

For each `test_cases` entry, record `id`, `name`, `priority`, `oracle_type` (`state`, `exception`,
`output`, `boundary`), preconditions, concrete input values, independent expected result,
teardown guarantees, and whether `verified` was actually observed.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each target gap. Record independent expected
values and reject tautological or mock-coupled assertions.

## Adversarial case

A generated test asserts `assert discount(p) == round(p * RATE, 2)` by importing the buggy
`RATE` constant and formula directly from the module under test, passing even when `RATE` is wrong.

Ensure expected values come from independent literal examples or specification invariants rather
than production helpers.

## Safe control

Parameterized table-driven cases with concrete literal input/expected pairs (`100 -> 85.00`,
`0 -> 0.00`, `-1 -> ValueError`) verify behavior independently without duplicating production code.

Prefer small table-driven boundary matrices over repetitive copy-pasted test functions.

## Independent validation

Validate `testing.json` against `assets/testing.schema.json`. When explicit user permission
(`--allow-fixes`) allows writing and running tests, verify the test fails on the regressed
behavior and passes on the intended contract, recording commands in `execution_summary`.

## Cross-topic boundary

Own discrete unit, boundary, and regression test case specifications in `testing.json`; hand off
producer/consumer wire harnesses to `test-contract-harnesses`, randomized invariants to
`test-property-invariants`, and full-suite execution verification to `test-suite-verification`.

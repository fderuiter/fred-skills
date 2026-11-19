# Investigation method: Behavioral Test Gap Analysis

## Scope selection

Trace one critical workflow or modified component from public entry point through validation,
state mutation, and error/rollback paths, comparing each contract branch against every existing
unit and integration test suite. Do not rely on filename matching alone when locating tests.

## Contract worksheet

For each target contract, record the expected postcondition or exception, existing test file
and assertion (`path:line`), mock fidelity, missing boundary inputs, recommended `oracle_type`,
and risk-based `priority`.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each component contract. Record both
high-priority assertion gaps and contracts already protected by integration tests.

## Adversarial case

A payment handler achieves 95% line coverage because a unit test mocks the ledger repository
and asserts only that `mock.save.called` is true, leaving duplicate-charge rollback completely
untested.

Inspect mock setups and assertion predicates to verify whether tests fail if the underlying
contract breaks.

## Safe control

Trivial declarative data classes or framework-generated boilerplate covered transitively by
integration contract tests do not need dedicated unit-test files.

Avoid flooding `testing.json` with low-value getter/setter test proposals.

## Independent validation

Cross-check candidate gaps against all test directories in the snapshot, record baseline commands
if executed, and validate `testing.json` against `assets/testing.schema.json`.

## Cross-topic boundary

Own behavioral gap discovery and risk prioritization in `testing.json`; hand off concrete test
case specification to `test-case-generation`, protocol schemas to `test-contract-harnesses`,
algebraic invariants to `test-property-invariants`, and runner verification to `test-suite-verification`.

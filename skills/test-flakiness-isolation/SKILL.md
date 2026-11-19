---
name: test-flakiness-isolation
description: "Isolate nondeterministic test failures caused by wall-clock sleeps, unseeded randomness, shared mutable state, port collisions, and uncoordinated async completions. Use when triaging intermittent CI failures, flaky test suites, order-dependent tests, or timing-sensitive assertions."
---

# Flakiness Isolation and Determinism Specifications

Trace intermittent test failures to concrete nondeterminism mechanisms—wall-clock sleeps, unseeded PRNGs, order-dependent shared state, fixed ports/paths, and unawaited background tasks—and specify deterministic synchronization and isolation replacements.
Keep the target repository read-only by default; do not write or modify repository test files outside the run workspace without explicit user permission.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Enforce its
   workspace-only specification default, deterministic harness rule, and explicit write-permission gate.
2. Read [investigation method](references/method.md). Inspect failing test logs, shared fixtures,
   timers, async teardown hooks, and concurrency primitives before proposing stabilization changes.
3. Reuse the workspace `testing.json` if present and verify `run.json` `authority.application_changes`
   before creating or editing files outside the run workspace.

## Inventory

Flaky or retried tests, `sleep`/timeout polls, system clock reads, unseeded RNG/UUID calls, module/class-scoped mutable fixtures, shared temp paths/ports/DB tables, and background worker/listener lifecycles.

## Investigation

1. Classify each intermittent failure by concrete mechanism: timing/sleep race, order-dependent shared state, unseeded randomness, resource contention (fixed port/path/table), or leaked async task outliving teardown.
2. Reconstruct the exact failing schedule, execution order, timezone/clock boundary, or seed that flips the assertion without relying on blind retry wrappers (`@flaky`, `reruns=3`).
3. Distinguish genuine test-harness nondeterminism from a real production concurrency bug surfaced intermittently by the test; never mask a real product race by loosening assertions.
4. Specify deterministic replacements: explicit event/condition barriers or fake clocks instead of `sleep()`, function-scoped isolated state/ephemeral ports, pinned RNG seeds, and deterministic worker drain hooks.
5. Record prioritized stabilization `test_cases` and `execution_summary` in `testing.json`; edit repository test files only when explicit user permission (`--allow-fixes`) is granted.

## Challenge and validate

Use this failure shape only as a discovery lead: A test starts an async worker, calls `sleep(0.05)`, and asserts output state—passing locally on idle CPUs while failing intermittently on loaded CI runners, or masking failures behind automatic test retries.

Actively test this counterexample: A bounded timeout guarding an explicit readiness event (`await ready_event.wait()`, bounded at 5s for deadlock safety) is a deterministic synchronization barrier, not a sleep-based timing guess.

Verify that proposed stabilization specs eliminate order/timing dependence rather than inflating sleep durations or adding blind retries; validate `testing.json` against the schema.

Check whether shuffled/repeated test execution flags (`pytest-randomly`, `go test -race -count`, `jest --runInBand`) are available in the workspace when execution is requested. Read
[research references](references/sources.md) only when classifying empirical flakiness root causes.
No external tool is required.

## Deliver and hand off

Document flakiness root causes and deterministic isolation specs using [testing template](assets/testing-template.md).
Populate `test_cases` and `execution_summary` in `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity confirms JSON structure, not deterministic execution.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `test-suite-verification`, `test-property-invariants`,
`test-case-generation`, or `review-concurrency-hazards` when repeated verification or production race review is needed.
Do not automatically load sibling skills, mask production races with retries, or write repository files outside the run workspace without explicit user permission.

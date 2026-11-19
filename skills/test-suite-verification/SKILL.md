---
name: test-suite-verification
description: "Execute and audit automated test suites for pass/fail fidelity, mutation sensitivity, clean teardown, and honest execution_summary telemetry. Use when verifying regression suites, checking whether tests catch seeded faults, or certifying suite execution state."
---

# Test Suite Execution and Oracle Verification

Execute authorized test commands, verify that assertions genuinely fail when guarded behavior breaks, and record honest `execution_summary` telemetry.
Use actual runner output and repository state; never mark `verified: true` or `state: "passed"` when tests were skipped, blocked, or unexecuted.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Follow its honest
   execution telemetry rules, workspace-only write default, and repository mutation guardrails.
2. Read [investigation method](references/method.md). Inspect runner configuration, environment
   prerequisites, and cleanup behavior before running suites.
3. Reuse the workspace `testing.json` if present so upstream `test_cases` entries are updated
   with accurate `verified` booleans and `execution_summary` counts.

## Inventory

Configured test runner commands (`pyproject.toml`, `package.json`, `Makefile`, CI workflows), target test files, fixture teardown hooks, environment requirements, and workspace `testing.json`.

## Investigation

1. Run bounded, non-destructive test commands matching the repository's CI configuration and capture exact exit codes, counts (`total`, `passed`, `failed`, `skipped`), and commands run.
2. Audit assertion sensitivity on target `test_cases`—confirming that assertions inspect concrete return values, state transitions, or exception types rather than swallowing errors or skipping silently.
3. Check post-run workspace cleanliness (`git status`, temp directories, background processes, open SQLite/socket handles) to verify zero residual state leakage.
4. Distinguish genuine contract failures from missing optional dependencies or sandbox environment restrictions, recording `state: "partial"` or `"not_run"` with `notes` when blocked.
5. Update `verified` flags on `test_cases` and populate `execution_summary` (`state`, `total`, `passed`, `failed`, `skipped`, `commands`, `notes`) in `testing.json`.

## Challenge and validate

Use this failure shape only as a discovery lead: A suite exits with code `0` and reports `"passed"`, yet 18 of 20 target tests were silently skipped via `@skipIf` environment guards or collected zero tests due to a mistyped filter.

Actively test this counterexample: A fast, hermetic unit suite with zero network dependencies that runs 40 assertions cleanly in 0.4s is verified for unit contracts even when optional external integration services are offline—provided skipped integration scope is noted in `execution_summary.notes`.

Inspect collected test counts and skipped reasons alongside exit code `0` before certifying `state: "passed"`; validate `testing.json` against the schema.

Check CI workflow files for canonical runner flags first. Read
[research references](references/sources.md) only when evaluating fault-seeding or runner-isolation checks.
No external tool is required.

## Deliver and hand off

Summarize verification evidence using [testing template](assets/testing-template.md).
Write `test_cases` `verified` flags and `execution_summary` to `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity checks JSON types, not runner truth.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed verification.

Offer targeted handoffs to `test-flakiness-isolation` when intermittent failures appear, or back to
`test-case-generation` and `test-gap-analysis` when weak assertions are uncovered.
Do not automatically load sibling skills, mutate repository source to seed faults in-place, or write files outside the run workspace without explicit user permission.

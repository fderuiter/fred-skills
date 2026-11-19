# Investigation method: Test Suite Execution and Oracle Verification

## Scope selection

Identify the repository's canonical test runner commands from CI/build manifests, execute the
relevant test targets non-destructively, and audit collected, passed, failed, and skipped counts
against the `test_cases` in `testing.json`. Never mutate repository files in place to test fault
detection without explicit user permission.

## Contract worksheet

Record exact runner command, exit code, `total` / `passed` / `failed` / `skipped` counts,
skipped-test reasons, assertion sensitivity observations, and post-run `git status` cleanliness.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every executed test target. Separate
verified passing assertions from skipped, uncollected, or environment-blocked checks.

## Adversarial case

A suite exits with code `0` and reports `"passed"`, yet 18 of 20 target tests were silently
skipped via `@skipIf` environment guards or collected zero tests due to a mistyped filter.

Always inspect runner collection output and `skipped` counts before setting `verified: true`
or `execution_summary.state = "passed"`.

## Safe control

A fast, hermetic unit suite with zero network dependencies that runs 40 assertions cleanly in
0.4s is verified for unit contracts even when optional external integration services are
offline—provided skipped integration scope is noted in `execution_summary.notes`.

Report partial scope honestly instead of conflating unit execution with full integration proof.

## Independent validation

Confirm `git status --porcelain` remains clean after test runs, verify `execution_summary`
totals match runner output, and validate `testing.json` against `assets/testing.schema.json`.

## Cross-topic boundary

Own suite execution telemetry, oracle sensitivity auditing, and `execution_summary` certification
in `testing.json`; hand off nondeterministic failures to `test-flakiness-isolation` and missing
coverage specifications to `test-case-generation`.

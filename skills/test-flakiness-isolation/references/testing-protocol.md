# Automated Testing Protocol

Standard protocol for automated test gap analysis, test case specification, contract harnesses, property invariants, flakiness isolation, and suite verification.

## Scope and Mutation Guardrails

- **Workspace-only specification default**: Testing specialists emit `testing.json`, test specifications, reports, and handoffs inside the run workspace by default.
- **Explicit permission required**: Do not create, overwrite, or modify test files, fixtures, or application code inside the target repository outside the run workspace without explicit user permission (for example `--allow-fixes` and explicit target scope).
- **Non-tautological oracles**: Never mirror production helper formulas or mock away the contract under test to manufacture passing assertions.

## Core Tenets

1. **Independent Oracles**: Every `test_cases` entry must specify an `oracle_type` (`state`, `exception`, `output`, `boundary`) derived from documented or caller-visible contracts rather than the implementation's internal expressions.
2. **Prioritized Gap Coverage**: Assign `priority` (`critical`, `high`, `medium`, `low`) so destructive regressions, boundary violations, and contract mismatches execute ahead of cosmetic edge cases.
3. **Deterministic Isolation**: Eliminate wall-clock sleeps, unseeded randomness, shared mutable globals, and order-dependent fixtures so suites remain reproducible under parallel or repeated runs.
4. **Honest Execution Telemetry**: Record `verified: true` only when a test was actually executed and observed in the current run, and populate `execution_summary` (`state`, `total`, `passed`, `failed`, `skipped`, `commands`, `notes`) accurately.

## Testing Stages

1. **Gap & Contract Analysis**: Compare public/module contracts, error paths, and protocol schemas against actual assertion strength in existing suites.
2. **Specification & Harness Design**: Author concrete test specifications, property invariants, and isolation controls in `testing.json`—writing repository test files only when explicit user permission is active.
3. **Verification & Handoff**: Execute authorized test commands when available, validate `testing.json` against [../assets/testing.schema.json](../assets/testing.schema.json), summarize with [../assets/testing-template.md](../assets/testing-template.md), and hand off via [../assets/handoff-template.md](../assets/handoff-template.md).

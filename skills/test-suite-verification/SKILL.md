---
name: test-suite-verification
description: "Use when executing and verifying generated test suites against regressions and failure oracles."
---

# Test Suite Verification

Read the [testing protocol](references/testing-protocol.md) before verifying test suites.
Confirm tests execute reliably, fail on seeded regressions, and leave no residual state.

## Workflow

1. Execute test suites across target environments and verify deterministic outcomes.
2. Confirm that each test exercises its stated boundary without flakiness or state leakage.
3. Verify failure oracles by validating tests fail meaningfully when invariants are broken.
4. Measure execution overhead, timeout constraints, and isolation between test cases.
5. Provide verification summaries certifying that generated suites are ready for integration.

## Deliver

Record execution verification with the [testing template](assets/testing-template.md).
Verify results payload adheres to the [testing schema](assets/testing.schema.json).
Produce structured verification reports with pass/fail logs and execution evidence.
Do not modify production application code without explicit user authorization, and never claim a test suite ran unless it actually executed.

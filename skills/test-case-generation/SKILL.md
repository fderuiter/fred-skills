---
name: test-case-generation
description: "Author deterministic unit, integration, and regression tests with isolated fixtures and independent assertion oracles. Use when writing executable test suites from gap backlogs or turning reproduced bug specs into automated checks."
---

# Test Case Generation

Read the [testing protocol](references/testing-protocol.md) before authoring automated tests.
Generate deterministic tests with independent oracles and clean boundary conditions.

## Workflow

1. Select targeted test gaps or defect specifications requiring automated coverage.
2. Choose the appropriate test layer (unit, integration, or contract) to isolate behavior.
3. Construct test cases with deterministic inputs, preconditions, and clean teardowns.
4. Formulate independent assertion oracles that do not re-implement production logic.
5. Verify that newly created tests run deterministically and fail against known defects.

## Deliver

Use the [testing template](assets/testing-template.md) to record generated test cases.
Validate test structures against the [testing schema](assets/testing.schema.json).
Produce runnable test files integrated cleanly into repository test frameworks when authorized; do not modify production code or write repository test files without explicit user authorization.

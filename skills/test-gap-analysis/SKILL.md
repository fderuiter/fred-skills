---
name: test-gap-analysis
description: "Prioritize unexercised code branches, exception handlers, and edge-value ranges into a structured test backlog. Use when planning automated test expansion, ranking untested failure branches, or scoping regression scenarios for a module."
---

# Test Gap Analysis

Read the [testing protocol](references/testing-protocol.md) before performing gap analysis.
Identify missing regression assertions, untested error branches, and edge conditions.

## Workflow

1. Map existing test coverage across target components, functions, and workflows.
2. Identify critical behavioral contracts that lack explicit test assertions.
3. Detect untested error paths, exception handling branches, and boundary input ranges.
4. Highlight fragile tests that rely on cyclic or tautological assertions.
5. Prioritize test gaps based on architectural risk and likelihood of regression.

## Deliver

Document uncovered gaps using the [testing template](assets/testing-template.md).
Ensure test gap specifications conform to [testing schema](assets/testing.schema.json).
Provide a prioritized list of test scenarios needed to eliminate coverage blind spots.
Do not modify repository or test files without explicit user authorization; keep confirmed gaps and unverified hypotheses distinct.

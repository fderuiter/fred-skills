---
name: repo-specify-regression
description: "Use when defining an independent regression test for a supported behavioral defect."
---

# Regression Specification

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Establish the independently supported contract and smallest failure trace before choosing a test layer. Preserve valid behavior and unresolved semantics.
2. Choose the lowest test boundary that actually crosses the defect: real transaction enlistment, effect publication, protocol serialization, or authorization authority.
3. Write independent expected outcomes, deterministic controls, and the meaningful incorrect behavior the test must reject. Avoid computing expected values using the same implementation.
4. When test edits are authorized, create only the scoped regression and demonstrate it fails for the original reason. Distinguish a test specification from a test written, executed, or observed failing.
5. Hand off the failure command, actual result, safe control, likely correction boundary, and compatibility assumptions. Do not loosen assertions to make a correction appear valid.

## Concrete boundary to investigate

A reversed completion order is controlled with deferred promises; a fixed sleep or duplicated mapper would not be an independent oracle.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

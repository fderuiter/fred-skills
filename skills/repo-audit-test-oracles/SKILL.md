---
name: repo-audit-test-oracles
description: "Audit test oracle quality with code evidence and counterexamples. Use when auditing assertions that mirror implementation, ineffective mocks, weak expected results, or surviving mutations."
---

# Test Oracle Quality

Investigate this contract dimension deeply and return evidence-backed findings.
Use the supplied source snapshot; do not assume a particular framework or deployment.

## Start

1. Read [shared protocol](references/protocol.md) once per run. Use its scope,
   evidence statuses, severity/confidence rubric, and application-code mutation boundary.
2. Read [investigation method](references/method.md). Inspect the listed inventory,
   callers, and safeguards before promoting a search match to a finding.
3. Reuse the existing audit.json and contract IDs if present. If running alone,
   establish a minimal run envelope and inspect required upstream contracts yourself.
   Related skills are optional deeper investigations, not mandatory dependency chains.

## Inventory

Assertion bodies, expected-value generators, fixtures, mocks, snapshot approvals, property models, and mutation results if available.

## Investigation

1. Identify the independent source of expected behavior; flag tests that calculate expected values with the same function or algorithm under test.
2. Check whether mocks bypass the boundary the test claims to validate and whether assertions inspect outcomes rather than only call counts.
3. Inspect broad snapshots, swallowed rejected promises, missing awaits, vacuous properties, and assertions that pass without exercising the scenario.
4. Use a small targeted mutation or known incorrect variant in a disposable copy when feasible; a surviving mutant can be equivalent or unreachable, so triage it.
5. Recommend independent golden cases, domain properties, metamorphic relations, or a simpler reference model. Keep coverage-gap findings separate.

## Challenge and validate

Use this failure shape only as a discovery lead: A parser test computes its expected result using the same parser helper, so the same bug exists on both sides of the assertion.

Actively test this counterexample: An intentionally shared test fixture is not circular when expected results were independently specified and reviewed.

Show a meaningful incorrect implementation that the current test accepts and specify an assertion that rejects it.

Check the repository's existing tooling before proposing commands. Read
[research references](references/sources.md) only when selecting a technique or
checking a framework/tool limitation. No listed tool is a prerequisite.

## Deliver and hand off

Produce the topic inventory and report using [report template](assets/report-template.md).
Record findings, healthy counterexamples, and coverage in audit.json according to
[run schema](assets/audit.schema.json). The schema validates structure, not truth.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for a standalone or resumed audit.

Tag cross-cutting findings and nominate one root cause. Offer deeper handoffs to
`repo-audit-test-contracts`, `repo-audit-data-integrity`, `repo-audit-nondeterminism` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

---
name: repo-audit-test-contracts
description: "Audit untested behavioral contracts with code evidence and counterexamples. Use when mapping missing behavioral tests, contract coverage gaps, or inadequate negative and recovery path coverage."
---

# Untested Behavioral Contracts

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

Independently supported requirements, reconstructed contract IDs, existing assertions, integration seams, and critical user outcomes.

## Investigation

1. Map each critical contract to an executable assertion and its layer; distinguish executed lines from verified behavior.
2. Inspect success, invalid inputs, denied actors, interruption, duplicate/reordered events, recovery, and persisted-state compatibility.
3. Read test bodies and doubles to establish what is asserted and which production boundaries are bypassed.
4. Prioritize missing tests by demonstrated risk and use the lowest layer that actually crosses the relevant boundary.
5. Separate a coverage gap from a proven product bug. Recommend targeted properties and scenarios rather than arbitrary coverage percentages.

## Challenge and validate

Use this failure shape only as a discovery lead: Unit tests verify encoding and decoding separately but never verify that transferred bytes equal the original after interruption and resume.

Actively test this counterexample: No unit test for a wrapper is not a finding when an appropriate integration test already verifies its contract.

Produce a contract-to-test matrix with asserted, partial, absent, and unknown coverage and a runnable proposed regression specification.

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
`repo-audit-test-oracles`, `repo-audit-behavioral-contracts`, `repo-audit-nondeterminism` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

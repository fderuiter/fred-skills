---
name: repo-audit-invariants
description: "Audit implicit invariants with code evidence and counterexamples. Use when investigating unenforced domain rules, invariant violations, or invalid combinations of domain values."
---

# Implicit Invariants

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

Domain aggregates, validation rules, write paths, constructors, database constraints, protocol completion criteria, and privileged operations.

## Investigation

1. Express each invariant as a predicate over a precisely named scope and time: continuously true, transaction-end true, or eventually restored within a bound.
2. Find where the predicate is established and every mutation that can invalidate it, including imports, admin paths, callbacks, migrations, and direct persistence.
3. Separate preconditions, postconditions, safety invariants, and liveness obligations; specify whose observation and which isolation level are relevant.
4. Trace alternate entry points and concurrent writers to find incomplete enforcement. Check types, encapsulation, database checks, and authoritative server validation before reporting.
5. Recommend the narrowest authoritative enforcement plus a property that would detect the failure; avoid scattering another copy of the same validation.

## Challenge and validate

Use this failure shape only as a discovery lead: Two callers each observe one available slot, both create a reservation, and the committed total exceeds capacity.

Actively test this counterexample: A temporary intermediate state hidden inside an atomic transaction may be valid when the contract only applies at commit.

Generate valid starting states and operation sequences, assert the predicate after each contract boundary, and shrink a counterexample.

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
`repo-audit-behavioral-contracts`, `repo-audit-transactions`, `repo-audit-sources-of-truth` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

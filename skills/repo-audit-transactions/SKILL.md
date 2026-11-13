---
name: repo-audit-transactions
description: "Audit implicit transaction boundaries with code evidence and counterexamples. Use when auditing multi-step consistency, partial commits, transaction scope, dual writes, or compensation guarantees."
---

# Implicit Transaction Boundaries

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

Database units of work, file-plus-metadata writes, state-plus-event publication, multi-service changes, and commit/acknowledgement points.

## Investigation

1. Define the business consistency unit, valid intermediate states, durable commit point, and observers allowed to see each state.
2. Trace connection/context enlistment, isolation level, external calls, exceptions, and rollback coverage; a transaction annotation alone is insufficient.
3. Check dual writes, outbox/inbox atomicity, concurrent uniqueness, optimistic tokens, write skew, and recovery after process termination.
4. Distinguish local ACID transactions from cross-system coordination. Evaluate idempotent compensation, sagas, or reconciliation without claiming rollback reverses an external effect.
5. Recommend the smallest enforceable consistency boundary and specify retry behavior for serialization conflicts and uncertain commits.

## Challenge and validate

Use this failure shape only as a discovery lead: A record commits, event publication fails, and subscribers never learn the change because neither an outbox nor repair path exists.

Actively test this counterexample: A workflow can intentionally expose a pending state if the contract defines it and recovery is durable.

Fail between durable steps and race competing writers under the actual isolation level; verify restart repair and externally visible consistency.

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
`repo-audit-idempotency`, `repo-audit-invariants`, `repo-audit-failure-modes` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

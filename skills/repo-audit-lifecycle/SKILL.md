---
name: repo-audit-lifecycle
description: "Audit ownership and lifecycle boundaries with code evidence and counterexamples. Use when auditing resource ownership, missing cleanup, use after disposal, leaked listeners, streams, timers, or tasks."
---

# Ownership and Lifecycle Boundaries

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

Camera/media streams, workers, handles, subscriptions, timers, sockets, DB contexts, cancellation sources, shared caches, and background tasks.

## Investigation

1. Create an ownership ledger: creator, owner, borrowers, transfer, close authority, lifetime boundary, and disposal behavior for each resource class.
2. Trace successful creation, partial initialization, failure before registration, replacement, unmount, shutdown, and repeated cleanup.
3. Check listeners with exact identities, shared ownership/reference counts, task joining, cleanup order, and callbacks after owner destruction.
4. Distinguish stopping work, detaching consumers, disposing handles, and releasing memory. Verify all owned subresources follow the intended lifetime.
5. Preserve valid shared lifetimes and recommend scoped ownership, explicit transfer, RAII/disposable patterns, or structured cleanup as appropriate.

## Challenge and validate

Use this failure shape only as a discovery lead: A camera stream opens after a component unmounts; cleanup ran before assignment, so the late stream stays active.

Actively test this counterexample: A process-wide cache or shared connection may intentionally outlive one screen; require a real ownership violation.

Count acquisitions/releases across repeated start-stop, failed setup, replacement, and late completion; assert no surviving owned resources.

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
`repo-audit-cancellation`, `repo-audit-side-effects`, `repo-audit-temporal-coupling` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

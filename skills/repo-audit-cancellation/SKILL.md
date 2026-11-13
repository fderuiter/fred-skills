---
name: repo-audit-cancellation
description: "Audit cancellation propagation with code evidence and counterexamples. Use when auditing abort propagation, cancelled tasks that keep running, stale publication after cancellation, or shutdown draining."
---

# Cancellation Propagation

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

Cancellation roots, abort signals/tokens, child work, blocking operations, background queues, cleanup, and publication boundaries.

## Investigation

1. Build a cancellation tree and identify who requests, observes, propagates, acknowledges, and joins each operation.
2. Inspect synchronous abort/event callbacks for reentrant start or cancel before assuming an operation fence is established. Trace tokens through every child call and distinguish abandoning a wait from stopping underlying work and suppressing stale publication.
3. Check cooperative checkpoints, noncancelable dependencies, signal reuse, disposal order, timeout conversion, and cancellation during cleanup.
4. Model cancel-before-start, during I/O, after effect before acknowledgement, after completion, and cancel A then start B.
5. Recommend token propagation plus publication fencing and bounded joining where appropriate; cancellation cannot undo an already committed effect.

## Challenge and validate

Use this failure shape only as a discovery lead: Promise.race returns on abort but the losing request later writes shared state and emits completion.

Actively test this counterexample: A documented noncancelable commit region can be correct if its outcome is reconciled and cancellation semantics are explicit.

Hold a dependency pending, cancel, then release it; assert children settle, owned resources release, and stale effects cannot publish.

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
`repo-audit-concurrency`, `repo-audit-lifecycle`, `repo-audit-failure-modes` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

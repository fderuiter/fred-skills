---
name: repo-audit-concurrency
description: "Audit concurrency and race conditions with code evidence and counterexamples. Use when auditing races, stale asynchronous results, reentrancy, lost updates, or unsafe overlapping operations."
---

# Concurrency and Race Conditions

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

Await points, callbacks, shared mutable fields, task groups, locks, actors, event loops, workers, read-modify-write flows, and database writers.

## Investigation

1. Distinguish thread data races, logical event-loop races, actor reentrancy, and distributed consistency races; identify the shared state and required ordering.
2. Name competing operations and construct a numbered interleaving around their reads, suspension points, writes, and external effects.
3. Find stale closures, response overwrite, check-then-act, double initialization, lock-order cycles, and state changed across actor suspension.
4. Verify synchronization actually covers the entire invariant and all writers. An await, single-thread event loop, or actor does not alone establish logical atomicity.
5. Choose generation fencing, compare-and-swap, serialization, structured tasks, or transactional control according to the actual contract; avoid global locking by default.

## Challenge and validate

Use this failure shape only as a discovery lead: Request A starts, request B starts, B resolves, then A resolves and overwrites B with stale data.

Actively test this counterexample: A monotonic generation check covering every publication can make reordered responses harmless; inspect it before filing.

Use controllable deferred completions or a scheduler, not sleeps; test both completion orders and preserve a replayable schedule.

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
`repo-audit-cancellation`, `repo-audit-lifecycle`, `repo-audit-transactions` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

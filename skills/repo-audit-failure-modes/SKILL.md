---
name: repo-audit-failure-modes
description: "Audit failure-mode completeness with code evidence and counterexamples. Use when auditing partial failures, timeouts, recovery gaps, interrupted workflows, or failure transition completeness."
---

# Failure-Mode Completeness

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

Critical step sequences, external dependencies, timeout policies, durable checkpoints, recoveries, user-visible outcomes, and compensations.

## Investigation

1. For each workflow step, enumerate plausible failure before effect, after effect before acknowledgement, partial effect, interruption, and simultaneous dependency failure.
2. Track preserved invariants, owned resources, durable state, returned error, and recovery authority at every failure point.
3. Distinguish timeout from known failure: the effect may have committed. Check retry budgets, compensation, reconciliation, and cancellation races.
4. Check recovery can itself fail or restart, and that success is not reported before all required durable outcomes are established.
5. Prioritize credible reachable fault combinations, document the fault model, and resist demanding recovery for impossible or explicitly unsupported failures.

## Challenge and validate

Use this failure shape only as a discovery lead: A write commits but its acknowledgement is lost; retry repeats the side effect while the UI reports the first attempt failed.

Actively test this counterexample: An explicitly best-effort secondary notification need not roll back a successful primary transaction.

Inject faults at each effect/acknowledgement boundary in an isolated harness and verify postconditions, cleanup, and restart recovery.

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
`repo-audit-error-semantics`, `repo-audit-idempotency`, `repo-audit-transactions` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

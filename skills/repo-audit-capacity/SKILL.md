---
name: repo-audit-capacity
description: "Audit resource bounds and backpressure with code evidence and counterexamples. Use when auditing unbounded memory, queues, retries, fan-out, polling, backpressure, or resource exhaustion."
---

# Resource Bounds and Backpressure

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

Queues, buffers, caches, parsers, decompression, workers, retry layers, request bodies, event fan-out, and retained results.

## Investigation

1. Name each resource, producer, consumer, admission policy, bound, release condition, and overload behavior; quantify from configuration or measurement.
2. Trace attacker- or user-controlled amplification, nested retry multiplication, decompression ratios, cardinality growth, and slow-consumer retention.
3. Check cancellation, fairness, starvation, queue overflow, retry deadlines, batch sizes, and whether work is bounded before expensive allocation.
4. Distinguish total work, peak memory, concurrency, latency, and sustained arrival rate; a fixed worker count alone does not bound an upstream queue.
5. Recommend admission limits, bounded buffers, coalescing, load shedding, or backpressure that preserves the intended loss and ordering semantics.

## Challenge and validate

Use this failure shape only as a discovery lead: Frames arrive faster than decoding; an unbounded queue retains each image until browser memory is exhausted.

Actively test this counterexample: A finite queue with measured drain capacity and explicit overflow handling is not unbounded merely because it uses an array.

Use synthetic burst and slow-consumer scenarios with explicit budgets; record peak retained items and overload outcomes, never invent throughput.

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
`repo-audit-complexity`, `repo-audit-failure-modes`, `repo-audit-lifecycle` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

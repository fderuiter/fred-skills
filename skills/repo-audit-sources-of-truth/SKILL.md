---
name: repo-audit-sources-of-truth
description: "Audit duplicated sources of truth with code evidence and counterexamples. Use when auditing duplicated domain state, conflicting authorities, independently maintained derived fields, or divergent projections."
---

# Duplicated Sources of Truth

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

Counters, statuses, persisted copies, UI stores, caches, DTOs, read models, and denormalized summaries for the same domain fact.

## Investigation

1. Map every representation to its authority, derivation, update owner, version, reconciliation mechanism, and permitted staleness.
2. Distinguish independent writable truth from legitimate derived values, immutable snapshots, materialized views, and event-sourced projections.
3. Trace all writers, partial updates, refreshes, restart paths, and migrations that can make the representations disagree.
4. Identify which consumers decide completion, authorization, totals, or recovery from each copy and the consequence of disagreement.
5. Recommend computing derivations, consolidating writes, versioning projections, or explicit reconciliation without destroying intentional read models.

## Challenge and validate

Use this failure shape only as a discovery lead: A transfer percentage reaches 100 while the authoritative decoded-byte set still lacks a required block.

Actively test this counterexample: A versioned projection with an explicit lag budget and reconciliation is not duplicate authority merely because data repeats.

Generate mutations through every write path, rebuild projections, and check agreed semantics at the promised consistency boundary.

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
`repo-audit-cache-coherence`, `repo-audit-invariants`, `repo-audit-data-integrity` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

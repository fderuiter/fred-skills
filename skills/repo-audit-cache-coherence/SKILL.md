---
name: repo-audit-cache-coherence
description: "Audit cache coherence with code evidence and counterexamples. Use when auditing stale caches, invalidation races, cache keys, tenant leakage through caches, or competing cache layers."
---

# Cache Coherence

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

Browser/server/CDN/query caches, memoization, keys, TTLs, tags, negative entries, refresh tasks, and source versions.

## Investigation

1. Map cache authority, full key dimensions, ownership, scope, consistency promise, invalidation events, and eviction bounds.
2. Check tenant, authorization, locale, configuration, schema version, and query parameters in keys where they affect results.
3. Trace write-invalidate-read races, stale refresh overwrite, negative caching, stampedes, and interactions among cache layers.
4. Compare observed staleness to a specified contract; inspect whether invalidation occurs after commit and failures can be repaired.
5. Choose versioned keys, generation fencing, coordinated invalidation, or explicit staleness budgets without demanding strong consistency everywhere.

## Challenge and validate

Use this failure shape only as a discovery lead: An older refresh finishes after a mutation and repopulates an entry with the value invalidation was meant to remove.

Actively test this counterexample: A deliberately stale read model within its freshness budget is not a cache defect by itself.

Use a fake clock and deferred reads, reorder refresh and mutation, and test isolation across users and key variants.

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
`repo-audit-sources-of-truth`, `repo-audit-trust-boundaries`, `repo-audit-concurrency` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

---
name: repo-audit-idempotency
description: "Audit idempotency and replay safety with code evidence and counterexamples. Use when auditing duplicate deliveries, unsafe retries, repeated initialization, replay handling, or idempotency keys."
---

# Idempotency and Replay Safety

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

Commands, submissions, event consumers, scheduled work, reconnect logic, initialization, dedup stores, and retry policies.

## Investigation

1. Define duplicate identity, caller/tenant scope, operation intent, retention window, payload equivalence, and expected response semantics.
2. Locate the atomic relationship between deduplication record, domain mutation, and side effect. Check concurrent first delivery and crash windows.
3. Test same key/same payload, same key/different payload, distinct keys/same payload, expired keys, delayed duplicates, and reordering.
4. Distinguish idempotency from commutativity, monotonicity, and at-most-once delivery; qualify any exactly-once claim by boundary and fault model.
5. Recommend an appropriate key, unique constraint, inbox/outbox, sequence fence, or reconciliation mechanism, not a universal cache.

## Challenge and validate

Use this failure shape only as a discovery lead: Two consumers both miss a dedup cache, both charge, then both store the same processed-event marker.

Actively test this counterexample: Repeated reads are often harmless; two identical payloads with different request identities can be intentional distinct operations.

Replay and concurrently deliver controlled inputs, crash before and after dedup/commit, and assert observable effects and returned outcomes.

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
`repo-audit-transactions`, `repo-audit-concurrency`, `repo-audit-protocol-drift` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

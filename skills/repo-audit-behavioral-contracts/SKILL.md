---
name: repo-audit-behavioral-contracts
description: "Audit behavioral contract reconstruction with code evidence and counterexamples. Use when reconstructing subsystem behavioral contracts across state, time, ownership, correctness, resilience, and trust."
---

# Behavioral Contract Reconstruction

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

Critical workflows, actors, entry points, durable records, external effects, versioned interfaces, and enforcement boundaries.

## Investigation

1. Separate observed implementation behavior, independently evidenced intended requirements, and proposed improvements. Never derive the requirement solely from the implementation being judged.
2. For each workflow, reconstruct state, invariant, temporal, ownership, concurrency, failure, consistency, security, resource, and compatibility contracts. Label absent evidence as unknown.
3. Trace each contract through producer, boundary, consumer, and observable outcome; locate the actual enforcing code, tests, and bypass paths.
4. Construct a cross-boundary trace, such as cancel A, start B, complete A, to detect interactions that individually reasonable functions fail to preserve.
5. Produce a contract matrix with independent evidence, implementation guarantee, enforcement gap, and specialist follow-up. A documented contract is not proof of enforcement.

## Challenge and validate

Use this failure shape only as a discovery lead: An old operation completes after replacement and publishes a success event into the new operation.

Actively test this counterexample: A deliberate eventual-consistency window with a documented reconciliation rule is not automatically a broken contract.

Use an independent reference model and end-to-end postconditions; test one representative critical workflow across all ten contract dimensions.

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
`repo-audit-invariants`, `repo-audit-state-machines`, `repo-audit-concurrency`, `repo-audit-trust-boundaries` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

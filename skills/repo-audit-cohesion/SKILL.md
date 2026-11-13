---
name: repo-audit-cohesion
description: "Audit cohesion and responsibility with code evidence and counterexamples. Use when auditing mixed module responsibilities, unrelated reasons to change, god objects, or weak cohesion."
---

# Cohesion and Responsibility

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

Module responsibilities, public interfaces, change history when available, callers, data ownership, and side-effect clusters.

## Investigation

1. Name each responsibility in domain terms and its consumers; locate distinct invariants, ownership, and lifecycle requirements.
2. Look for repeated coordinated changes and unrelated dependencies, using history only when available and reporting that limitation.
3. Distinguish a cohesive orchestration service from accidental aggregation; file size alone does not establish a design defect.
4. Explain a concrete consequence such as a cross-owner mutation, impossible isolation test, or unrelated change causing a regression.
5. Propose a focused seam aligned to data and behavior ownership, preserving public contracts and avoiding speculative microservices.

## Challenge and validate

Use this failure shape only as a discovery lead: A report formatter also updates authorization policy, so a formatting change unexpectedly changes write permissions.

Actively test this counterexample: A large parser can be cohesive if its branches implement one format contract under one ownership model.

Check the proposed seam against representative change scenarios and existing behavior, then specify boundary tests for the extracted responsibility.

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
`repo-audit-architecture`, `repo-audit-side-effects`, `repo-audit-domain-models` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

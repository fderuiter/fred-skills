---
name: repo-audit-temporal-coupling
description: "Audit temporal coupling with code evidence and counterexamples. Use when investigating hidden call-order dependencies, startup/shutdown order, initialization sequencing, or typestate contracts."
---

# Temporal Coupling

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

Configure-initialize-start-stop APIs, bootstraps, dependency readiness, async setup, shutdown hooks, and migrations before use.

## Investigation

1. Express required history as happens-before edges, not just a preferred sequence; distinguish data dependence, resource lifetime, and arbitrary convention.
2. Trace every caller for omitted, repeated, reordered, concurrent, and late operations, including configuration changes after readiness.
3. Check that a ready flag corresponds to completed initialization rather than scheduled work, and that readiness invalidates after failure or reconfiguration.
4. Examine startup dependency cycles and shutdown reverse-order needs; locate paths that publish partially initialized objects.
5. Prefer constructors/factories returning usable objects, immutable configuration, explicit state, or typed capabilities where they eliminate an observed misuse.

## Challenge and validate

Use this failure shape only as a discovery lead: Initialize sets ready before awaited setup ends; start observes ready and uses an unset dependency.

Actively test this counterexample: An API whose constructor establishes readiness has no hidden initialization-order defect merely because lifecycle methods exist.

Permute call order, duplicate calls, reconfigure during setup, and fail each readiness prerequisite with deterministic completions.

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
`repo-audit-state-machines`, `repo-audit-lifecycle`, `repo-audit-concurrency` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

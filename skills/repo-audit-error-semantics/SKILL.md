---
name: repo-audit-error-semantics
description: "Audit error semantics and taxonomy with code evidence and counterexamples. Use when auditing swallowed errors, error classification, incorrect recovery, misleading failures, or exception propagation."
---

# Error Semantics and Taxonomy

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

Exceptions, result types, status codes, retries, catches, logging, user messages, cancellation signals, and error ownership.

## Investigation

1. Trace each error from origin through wrapping, classification, propagation, observation, and recovery; preserve causal and operation identity.
2. Separate validation, authorization, conflict, transient dependency, permanent defect, cancellation, and uncertain-outcome errors where behavior differs.
3. Find success-shaped fallbacks, catch-and-continue corruption, catch-all retries, lost stack/cause, double reporting, and unobserved task failures.
4. Verify cancellation and timeout semantics across boundaries and ensure user-visible status does not contradict a committed effect.
5. Recommend a small meaningful taxonomy and clear handling authority; avoid a parallel hierarchy with no change in behavior.

## Challenge and validate

Use this failure shape only as a discovery lead: A database write failure is caught and converted to an empty list, so the caller treats failed persistence as successful absence.

Actively test this counterexample: A documented optional lookup returning an empty result can be valid; verify its contract before calling the fallback error swallowing.

Inject representative error classes, verify propagation and retry decisions, and assert no success is reported after a required failure.

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
`repo-audit-failure-modes`, `repo-audit-observability`, `repo-audit-cancellation` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

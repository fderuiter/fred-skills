---
name: repo-audit-api-consistency
description: "Audit api design consistency with code evidence and counterexamples. Use when auditing inconsistent API semantics, cancellation behavior, result types, pagination, or lifecycle conventions."
---

# API Design Consistency

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

Related public methods/endpoints, consumers, result and error types, pagination, mutation semantics, and documented guarantees.

## Investigation

1. Build a comparison matrix for analogous operations: names, input units, defaults, return values, errors, cancellation, side effects, and ownership.
2. Distinguish justified domain differences from accidental inconsistency and identify actual caller confusion or incorrect behavior.
3. Trace null/empty/not-found distinctions, sync/async completion, partial success, pagination cursors, and retry semantics.
4. Inspect consumer assumptions and compatibility costs before proposing a uniform interface or renaming.
5. Recommend a small consistent contract or adapter with a deprecation/migration path; avoid changing public behavior merely for aesthetic symmetry.

## Challenge and validate

Use this failure shape only as a discovery lead: Two delete APIs return before different completion points, but a shared caller assumes both mean the resource is durably gone.

Actively test this counterexample: A streaming API and a batch API may intentionally expose different cancellation and partial-result semantics.

Use consumer contract scenarios that compare promised outcomes and error behavior across analogous operations.

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
`repo-audit-error-semantics`, `repo-audit-side-effects`, `repo-audit-compatibility` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

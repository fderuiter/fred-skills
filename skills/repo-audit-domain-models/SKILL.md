---
name: repo-audit-domain-models
description: "Audit implicit domain models with code evidence and counterexamples. Use when auditing primitive obsession, ambiguous identifiers or units, invalid domain combinations, or missing domain concepts."
---

# Implicit Domain Models

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

IDs, units, amounts, statuses, timestamps, aggregate boundaries, validation rules, and repeated primitive parameter groups.

## Investigation

1. Reconstruct domain concepts and invariants from real workflows and stakeholder documents where available; preserve unresolved semantic ambiguity.
2. Find primitives whose interchangeable types conceal different meanings, units, scopes, or trust levels.
3. Trace construction, parsing, mutation, persistence, and serialization to locate where invalid domain values enter or change meaning.
4. Distinguish stable domain concepts from temporary transport shapes and avoid introducing wrappers without a concrete misuse or enforcement benefit.
5. Recommend branded/opaque types, value objects, validated factories, aggregate boundaries, or explicit units using native language idioms.

## Challenge and validate

Use this failure shape only as a discovery lead: Two string parameters representing study ID and site ID are swapped, and both pass generic nonempty validation.

Actively test this counterexample: A short-lived DTO need not become a rich domain object when its boundary validation and semantics are already clear.

Create negative type checks where supported and independent valid/invalid construction and serialization tests.

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
`repo-audit-invariants`, `repo-audit-data-integrity`, `repo-audit-api-consistency` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

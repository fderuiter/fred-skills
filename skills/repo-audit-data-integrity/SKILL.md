---
name: repo-audit-data-integrity
description: "Audit data transformation integrity with code evidence and counterexamples. Use when auditing lossy mappings, serialization errors, precision loss, provenance gaps, or parser-to-model data corruption."
---

# Data Transformation Integrity

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

Input parsers, DTO/domain conversions, serializers, persistence mappings, exports, units, time zones, and UI projections.

## Investigation

1. Build a field-level transformation chain with types, units, precision, nullability, identifier semantics, encoding, and provenance.
2. Check absent versus null versus zero, unknown enum values, date-only versus instants, decimal rounding, byte encodings, and collection order.
3. Identify intentional lossy transformations and their contractual limits; verify round trips only where invertibility is actually required.
4. Trace defaulting, truncation, normalization, and error paths across producer and consumer; protect authoritative source values when projecting.
5. Recommend explicit conversion boundaries and golden/reference vectors; generated expectations from the same mapper are not independent evidence.

## Challenge and validate

Use this failure shape only as a discovery lead: A decimal measurement is converted through a binary float and exported with changed precision or units.

Actively test this counterexample: A rounded display value can be valid when storage and downstream computation retain required precision.

Use independent golden vectors, round-trip properties where applicable, and metamorphic checks for units, dates, missing values, and encoding.

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
`repo-audit-protocol-drift`, `repo-audit-canonicalization`, `repo-audit-domain-models` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

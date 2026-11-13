---
name: repo-audit-protocol-drift
description: "Audit protocol contract drift with code evidence and counterexamples. Use when auditing producer-consumer disagreement, wire format drift, message versioning, or incompatible protocol assumptions."
---

# Protocol Contract Drift

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

Message schemas, encoders/decoders, HTTP clients, worker messages, file formats, generated types, and version negotiation.

## Investigation

1. Map each producer-consumer pair, deployed versions, wire schema, framing, units, ordering, required fields, and unknown-field behavior.
2. Compare actual serialization and parsing rather than trusting shared type names or generated interfaces.
3. Check length/checksum rules, duplicate messages, version fallback, capability negotiation, and partial/truncated frames.
4. Trace old producer/new consumer and new producer/old consumer compatibility with real fixtures; distinguish schema compatibility from semantic compatibility.
5. Recommend shared authoritative schema or explicit adapters and contract tests; do not assume a major upgrade can coordinate every deployed peer.

## Challenge and validate

Use this failure shape only as a discovery lead: The sender changes a length from bytes to characters while the receiver continues to allocate and validate by bytes.

Actively test this counterexample: An additive optional field can be backward compatible when consumers ignore unknown fields as designed.

Maintain a version-pair matrix and independent golden wire vectors with truncation, reordering, and unknown-value cases.

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
`repo-audit-compatibility`, `repo-audit-data-integrity`, `repo-audit-idempotency` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

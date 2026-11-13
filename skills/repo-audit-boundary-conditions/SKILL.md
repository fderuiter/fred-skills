---
name: repo-audit-boundary-conditions
description: "Audit boundary-condition correctness with code evidence and counterexamples. Use when auditing empty inputs, limits, off-by-one errors, numeric overflow, Unicode, or malformed boundary values."
---

# Boundary-Condition Correctness

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

Indexing, sizes, counts, ranges, pagination, timestamps, byte lengths, numeric conversions, parsers, and protocol limits.

## Investigation

1. Identify the exact mathematical or format boundary and whether each endpoint is inclusive, exclusive, representable, or prohibited.
2. Check zero, one, limit-minus-one, limit, limit-plus-one, negative, NaN/infinity where representable, overflow, and malformed encodings.
3. Distinguish bytes, code units, code points, graphemes, and display width; trace limit checks before decoding and allocation.
4. Follow values across signed/unsigned and width conversions, pagination continuation, timestamp boundaries, and empty collection operations.
5. Construct a reachable input and independent expected result; do not treat an intentionally rejected out-of-domain value as a bug.

## Challenge and validate

Use this failure shape only as a discovery lead: A buffer is allocated for N bytes but a loop writes indices zero through N inclusive.

Actively test this counterexample: An exclusive end offset equal to collection length is valid when the slice API defines it that way.

Generate boundary partitions and shrink failing inputs; assert independent length, range, and error contracts.

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
`repo-audit-data-integrity`, `repo-audit-capacity`, `repo-audit-canonicalization` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

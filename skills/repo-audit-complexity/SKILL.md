---
name: repo-audit-complexity
description: "Audit algorithmic complexity with code evidence and counterexamples. Use when auditing pathological input costs, quadratic work, excessive allocations, or algorithmic denial of service."
---

# Algorithmic Complexity

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

Nested loops, rescans, sorts, regexes, recursive traversals, graph expansion, copies, parsing, and input-controlled dimensions.

## Investigation

1. Name independent input dimensions and derive time, allocation, and retained-memory costs from actual operations, not loop count alone.
2. Trace repeated work through callbacks, render cycles, nested retries, serialization, and hidden copies in library calls.
3. Construct an adversarial but permitted input family and compare growth; distinguish measured results from inferred asymptotic bounds.
4. Check bounds, amortization, caching, and small fixed domains before calling an apparent quadratic path consequential.
5. Recommend the simplest algorithm or bound that preserves semantics; do not present a microbenchmark as end-to-end throughput.

## Challenge and validate

Use this failure shape only as a discovery lead: Every received chunk rescans and recopies all previous chunks, making transfer completion quadratic in total chunks.

Actively test this counterexample: A nested loop over a constant four protocol lanes is constant-factor work, not quadratic in file size.

Use controlled scaling inputs, operation counts, and isolated benchmarks with environment recorded; verify output equality against independent vectors.

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
`repo-audit-capacity`, `repo-audit-boundary-conditions`, `repo-audit-data-integrity` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

---
name: repo-audit-observability
description: "Audit observability gaps with code evidence and counterexamples. Use when auditing undiagnosable failures, missing correlation, misleading telemetry, or insufficient recovery visibility."
---

# Observability Gaps

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

Critical outcomes, logs, traces, metrics, error boundaries, operation IDs, privacy controls, and support/recovery workflows.

## Investigation

1. Start from a concrete diagnostic question: which operation failed, at which boundary, with what durable outcome and recovery action?
2. Trace correlation across async tasks, queues, retries, tenant contexts, and cancellations; distinguish attempts from logical operations.
3. Check errors retain cause and code, metrics have bounded cardinality, and sampled/missing signals do not imply successful execution.
4. Verify secret/PII handling and ensure security-sensitive audit events are distinguished from best-effort diagnostic logs.
5. Recommend the minimum useful signals and ownership; adding logs everywhere can increase noise without answering the diagnostic question.

## Challenge and validate

Use this failure shape only as a discovery lead: A retry reports success but logs lack request or attempt IDs, leaving operators unable to determine whether two effects occurred.

Actively test this counterexample: A deterministic local tool may need an actionable error and local diagnostic export rather than a hosted telemetry stack.

Inject one representative failure and verify a reviewer can reconstruct operation, attempt, causal boundary, outcome, and next action.

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
`repo-audit-error-semantics`, `repo-audit-failure-modes`, `repo-audit-trust-boundaries` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

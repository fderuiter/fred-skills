---
name: repo-audit-configuration
description: "Audit configuration combinatorics with code evidence and counterexamples. Use when auditing invalid feature-flag combinations, configuration precedence, environment drift, or unsafe configuration transitions."
---

# Configuration Combinatorics

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

Flags, environment variables, defaults, remote settings, build-time options, persistence, capability probes, and rollout states.

## Investigation

1. Map each option’s type, default, authority, precedence, evaluation time, allowed combinations, and change lifetime.
2. Identify constraints among options and configurations that require capabilities or migrations; check validation at startup and dynamic update.
3. Trace stale captures, flag changes during work, mismatched client/server flags, and missing versus explicit false values.
4. Use risk-based pairwise or higher-order interaction cases where justified; record untested combinations instead of claiming exhaustive enumeration.
5. Recommend normalized configuration, constraint validation, capability-based routing, or immutable operation snapshots rather than expanding boolean branches.

## Challenge and validate

Use this failure shape only as a discovery lead: A decoder flag enables a format while the matching encoder capability remains disabled, creating unreadable output.

Actively test this counterexample: Two independent presentation preferences do not require a combinatorial test suite solely because both are booleans.

Test default/override precedence, invalid combinations, startup errors, mid-operation updates, and a documented interaction coverage plan.

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
`repo-audit-state-machines`, `repo-audit-protocol-drift`, `repo-audit-trust-boundaries` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

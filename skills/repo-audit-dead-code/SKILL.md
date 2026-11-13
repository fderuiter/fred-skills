---
name: repo-audit-dead-code
description: "Audit dead code and obsolete compatibility paths with code evidence and counterexamples. Use when auditing unreachable code, abandoned flags, unused exports, obsolete migrations, or removable compatibility paths."
---

# Dead Code and Obsolete Compatibility Paths

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

Entrypoints, runtime discovery, exports, feature flags, migrations, compatibility adapters, generated files, reflection, and published interfaces.

## Investigation

1. Trace reachability from actual executable, framework, build, test, plugin, and externally published entry points.
2. Inspect dynamic imports, reflection, registration, code generation, string-based references, and configuration before accepting unused-code results.
3. Establish support policy and persisted data/client versions before treating an old migration or compatibility branch as obsolete.
4. Separate unreachable code, unused public surface, low-traffic behavior, and intentionally retained recovery code.
5. Recommend removal only with evidence and a scoped verification plan; this audit produces recommendations and does not delete application code.

## Challenge and validate

Use this failure shape only as a discovery lead: A retired flag permanently disables a path, but the path continues to allocate listeners during module import.

Actively test this counterexample: An old migration still needed by a fresh installation is live operational behavior even if current runtime code never calls it.

Check configured entry points and a disposable removal/build smoke test when authorized; record unverified external consumers.

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
`repo-audit-compatibility`, `repo-audit-configuration`, `repo-audit-architecture` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

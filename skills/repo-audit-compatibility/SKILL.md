---
name: repo-audit-compatibility
description: "Audit compatibility and migration contracts with code evidence and counterexamples. Use when auditing breaking persisted-state changes, migrations, older-client compatibility, or rolling deployment contracts."
---

# Compatibility and Migration Contracts

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

Persisted schemas, local storage, exported files, API versions, old clients, migration checkpoints, and rollback paths.

## Investigation

1. Define supported version pairs and compatibility direction using actual release/deployment policy; do not invent indefinite support.
2. Trace readers and writers before, during, and after migration, including mixed versions, offline clients, and interrupted upgrades.
3. Check expand-contract sequencing, data defaults, unknown enum values, destructive transforms, migration idempotency, and restart behavior.
4. Distinguish reversible code rollback from irreversible data transformation and verify recovery does not silently discard authoritative records.
5. Recommend explicit versioning, staged rollout, adapters, backups, or repair mechanisms sized to the repository’s real deployment model.

## Challenge and validate

Use this failure shape only as a discovery lead: A new version rewrites stored records; an older offline client resumes and overwrites them using the obsolete schema.

Actively test this counterexample: A private unreleased format can change incompatibly if there are no supported persisted consumers; establish that context.

Use old/new fixture matrices and interrupted migration/restart scenarios, checking preserved values and explicit unsupported-version errors.

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
`repo-audit-protocol-drift`, `repo-audit-transactions`, `repo-audit-data-integrity` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

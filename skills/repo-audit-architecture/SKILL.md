---
name: repo-audit-architecture
description: "Audit architectural boundaries and dependency direction with code evidence and counterexamples. Use when auditing dependency cycles, layer violations, module boundary erosion, forbidden imports, or dependency direction."
---

# Architectural Boundaries and Dependency Direction

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

Modules, packages, declared dependency rules, runtime composition, public APIs, data access, events, and generated/client dependencies.

## Investigation

1. Derive intended dependency rules from current architecture decisions and code ownership; do not impose a generic layered architecture.
2. Map compile-time imports and runtime calls, including direct database access, shared mutable state, callbacks, service locators, and events.
3. Distinguish legal composition-root wiring, type-only coupling, generated imports, and actual behavioral dependencies.
4. Demonstrate a violated rule or concrete change/reliability impact; inspect cycle paths and cross-module writes rather than reporting a graph metric alone.
5. Recommend a minimal seam and enforceable architecture rule; separate a necessary foundation from independent bug fixes and avoid broad rewrites.

## Challenge and validate

Use this failure shape only as a discovery lead: Module A writes module B’s tables directly, bypassing B’s validation and audit path.

Actively test this counterexample: An import cycle through types may have no runtime initialization cycle; assess the declared policy and emitted behavior.

Run stack-appropriate dependency checks against a deliberate violating fixture and a legal composition path; inspect runtime access separately.

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
`repo-audit-cohesion`, `repo-audit-side-effects`, `repo-audit-domain-models` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

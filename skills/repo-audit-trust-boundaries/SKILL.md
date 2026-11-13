---
name: repo-audit-trust-boundaries
description: "Audit trust and authorization boundaries with code evidence and counterexamples. Use when mapping untrusted inputs, authorization bypasses, tenant isolation, or security boundary enforcement."
---

# Trust and Authorization Boundaries

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

HTTP/message/file inputs, browser-to-server boundaries, tenants, privileged roles, URLs, local storage, imports, and outbound sinks.

## Investigation

1. Map actor, attacker control, entry point, parser, normalization, validator, policy decision, resource lookup, and sensitive sink.
2. Check object-level and operation-level authorization at the authoritative boundary, including alternate routes, bulk paths, workers, and cached decisions.
3. Trace identity and tenant context through async work; distinguish authentication from authorization and UI affordances from enforcement.
4. Demonstrate a reachable bypass with the actual attacker capabilities and configuration; check middleware, database policies, and framework escaping.
5. Separate a confirmed exploit path, statically established defect, credible unverified risk, and optional hardening. Protect secrets and use synthetic actors.

## Challenge and validate

Use this failure shape only as a discovery lead: Tenant A supplies tenant B’s object ID to a handler that authenticates the caller but never binds the object to that tenant.

Actively test this counterexample: A client-only local file tool has a different trust model from a shared server; do not invent server-side tenants.

Use an actor-resource-action matrix with denied controls, alternate entry points, malformed payloads, and context-switch tests.

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
`repo-audit-canonicalization`, `repo-audit-data-integrity`, `repo-audit-configuration` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

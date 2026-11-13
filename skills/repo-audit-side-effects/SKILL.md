---
name: repo-audit-side-effects
description: "Audit hidden side effects with code evidence and counterexamples. Use when investigating unexpected mutations, hidden I/O, effectful getters, render effects, or misleading function contracts."
---

# Hidden Side Effects

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

Getters, selectors, calculations, imports, render functions, hooks, callbacks, caches, events, logs, and global mutations.

## Investigation

1. Build an effect map covering mutated owners, I/O, event emission, subscriptions, randomness, clock access, and durable side effects.
2. Trace transitive callees and lazy getters: a function can appear pure while dependencies mutate shared state or perform network work.
3. Check repeated invocation, render replay, evaluation order, and test setup for effects callers do not reasonably expect.
4. Inspect whether names, types, return values, documentation, and transaction boundaries communicate those effects and who owns them.
5. Recommend explicit effect boundaries or small pure cores only where they improve correctness; do not demand purity for commands whose purpose is effects.

## Challenge and validate

Use this failure shape only as a discovery lead: A progress selector marks records processed, so rendering the dashboard changes the next export.

Actively test this counterexample: Memoization hidden inside a function may be observationally safe when keying, lifetime, and resource bounds are correct.

Call the apparent query twice with equal input and observe domain state and effect counts independently of the returned value.

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
`repo-audit-sources-of-truth`, `repo-audit-lifecycle`, `repo-audit-test-oracles` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

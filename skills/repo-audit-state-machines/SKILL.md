---
name: repo-audit-state-machines
description: "Audit implicit state machines with code evidence and counterexamples. Use when finding implicit state machines, invalid state combinations, missing transitions, or state-dependent behavior."
---

# Implicit State Machines

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

Status fields, boolean clusters, reducers, workflow handlers, UI modes, initialization, retries, persistence, and terminal states.

## Investigation

1. Enumerate reachable semantic states from code and event history; distinguish control state from contextual data and independent orthogonal regions.
2. Build a transition table with source, event, guard, action, target, resource effects, and evidence. Mark unproven edges rather than inventing transitions.
3. Check invalid combinations, unreachable states, missing cancel/error/timeout edges, duplicate terminal events, reentry, and persisted-state restoration.
4. Trace guards and atomicity across asynchronous suspension; test whether a transition remains valid when another event changes state before its action completes.
5. Compare a transition table, discriminated union, reducer, or statechart against current needs. Recommend XState or another dependency only when justified.

## Challenge and validate

Use this failure shape only as a discovery lead: A retry changes an error flag but leaves a completion flag set, making the UI claim both success and pending.

Actively test this counterexample: Several booleans may represent truly independent dimensions; their mere existence does not prove an invalid state machine.

Exercise states, transitions, guards, repeated events, and terminal-state stability; bound model traversal and record unexplored event interleavings.

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
`repo-audit-invariants`, `repo-audit-temporal-coupling`, `repo-audit-failure-modes` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

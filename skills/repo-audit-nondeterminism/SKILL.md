---
name: repo-audit-nondeterminism
description: "Audit nondeterministic behavior with code evidence and counterexamples. Use when auditing flaky tests, clock/randomness dependence, global state leakage, or order-sensitive workflows."
---

# Nondeterministic Behavior

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

Wall clocks, monotonic clocks, randomness, filesystem ordering, locale/time zone, global caches, test concurrency, and scheduling.

## Investigation

1. Identify sources of nondeterminism and distinguish required product variability from uncontrolled test dependencies.
2. Trace seed, clock, scheduler, locale, ordering, and environment inputs through assertions and generated fixtures.
3. Check shared-state isolation, parallel order sensitivity, real sleeps, polling races, and accidental dependence on unordered iteration.
4. Record the failing seed/schedule/environment and shrink the scenario; repeated passing runs are not proof the race is absent.
5. Recommend injectable clocks/randomness, controlled scheduling, explicit ordering, and test isolation only where the contract requires determinism.

## Challenge and validate

Use this failure shape only as a discovery lead: A test assumes expiry occurs at exactly one wall-clock second while scheduler delay crosses the next tick.

Actively test this counterexample: Randomized test generation is useful when the seed and counterexample are replayable.

Replay captured seeds, vary operation order and time zone, and use deterministic scheduling to explain the failure rather than masking it with retries.

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
`repo-audit-concurrency`, `repo-audit-test-oracles`, `repo-audit-boundary-conditions` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

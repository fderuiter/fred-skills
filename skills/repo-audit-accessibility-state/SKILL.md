---
name: repo-audit-accessibility-state
description: "Audit accessibility state consistency with code evidence and counterexamples. Use when auditing divergence between UI state, focus, keyboard behavior, ARIA state, or screen-reader announcements."
---

# Accessibility State Consistency

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

Dialogs, tabs, menus, async status, validation messages, loading states, disabled controls, focus owners, and live regions.

## Investigation

1. Map each interaction state to visual output, accessible role/name/value, keyboard behavior, focus position, and announcement.
2. Trace open/close, loading/success/error, disabled/enabled, route changes, cancellation, and dynamic list updates.
3. Check focus restoration, hidden-but-focusable content, stale aria-expanded/selected/busy values, live-region timing, and error association.
4. Combine DOM accessibility scans with keyboard and assistive-technology checks; do not claim automated scans establish full accessibility.
5. Recommend native semantics and unified state derivation where possible, respecting reduced motion and actual product interaction requirements.

## Challenge and validate

Use this failure shape only as a discovery lead: A dialog disappears visually on error but retains focus in hidden content and leaves its trigger marked expanded.

Actively test this counterexample: An intentional focus move after navigation can be correct; evaluate the interaction and user task rather than forcing restoration everywhere.

Exercise the interaction with keyboard and inspect the accessibility tree across transitions; record any manual screen-reader checks not performed.

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
`repo-audit-state-machines`, `repo-audit-sources-of-truth`, `repo-audit-test-contracts` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.

---
name: repo-plan-fix
description: "Use when converting verified findings into bounded implementation tasks and an implementation-agent handoff."
---

# Scoped Implementation Planning

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Load current source, findings, requirements, tests, and existing issues/PRs when accessible. Revalidate stale evidence before declaring a task ready.
2. Group by violated contract, enforcing authority, and corrective mechanism. Keep independent causes separate; cross-tag symptoms of one cause without multiplying tickets.
3. Separate necessary foundations, unresolved decisions, direct fixes, and optional refactors. Assign blocked-by only for real implementation prerequisites; check graph cycles.
4. Specify likely files, exact scope boundary, independent acceptance criteria, executable validation opportunities, and compatibility/migration requirements. Do not invent labels, estimates, or issue numbers.
5. Produce an ordered implementation prompt and record duplicate-search availability, publication authority, evidence certainty, and remaining decisions. Retain unverified risks as investigations.

## Concrete boundary to investigate

Cancellation and stale-response findings can share one publication-guard cause; a separate authorization failure remains independently shippable.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

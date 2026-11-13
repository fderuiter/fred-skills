---
name: repo-reconcile-findings
description: "Use when reconciling audit findings, verified fixes, regression evidence, and issue state across runs."
---

# Remediation Reconciliation

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Read source snapshots, finding fingerprints, evidence records, verified issue/PR links, and execution artifacts. Never identify findings across runs solely by run-local IDs or line numbers.
2. Compare corrective mechanisms, scopes, and evidence. Preserve ambiguous matches for review; a missing finding can reflect changed scope or wording rather than resolution.
3. Keep evidence status and remediation state separate. Require exact before/after reproduction, independent regression, and preserved safe controls before recommending verified-fixed.
4. Invalidate affected evidence when source, callers, configuration, runtime, or relevant tools change. Preserve refuted hypotheses with provenance and a freshness condition.
5. Update local records first. Publish remote state changes only under applicable user authorization; verify the resulting state and report partial failures without fabricating completion.

## Concrete boundary to investigate

An unchanged source line can acquire different behavior through a modified caller; cached refutation is evidence to revalidate, not permanent truth.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

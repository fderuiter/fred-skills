---
name: repo-audit-synthesis
description: "Consolidate repository audit findings into deduplicated root causes, issue drafts, and ordered implementation handoffs. Use after one or more audits or when converting audit reports into an issue plan."
---

# Audit Findings and Issue Planning

Turn evidence-backed audit artifacts into coherent, reviewable work.

## Normalize and challenge

Read [shared protocol](references/protocol.md). Load the run ledger and source snapshot,
topic reports, relevant requirements, and existing issue/PR inventory if accessible.
If reports are all that is supplied, preserve attribution and mark source verification
unperformed. Recheck stale evidence against the target revision before declaring readiness.

Reject or downgrade findings with no reachable path, unsupported intended contract,
unexamined safeguard, or claimed test result lacking execution evidence. Keep refuted
candidates outside actionable issue drafts. Preserve needs-validation items as bounded
investigations with a decisive evidence question, not implementation-ready defects.

## Group root causes and existing work

Cluster by violated contract, enforcing authority, and corrective mechanism. Cross-topic
tags and repeated locations do not require separate issues; shared symptoms do not prove
a shared cause. Retain traceability from every source finding to its consolidated record.

Search open and relevant closed issues and PRs by behavior, symbol, contract, and cause.
Read candidate matches before deciding equivalence. Link/extend existing work only where
scope matches; a similarly titled issue or merged PR does not prove current code is fixed.
If issue access is unavailable, say duplicate checking is incomplete and produce local drafts.

## Plan implementation

Separate independently shippable corrections, prerequisite refactors, and unresolved
architecture decisions. Prefer a direct bounded fix where a broad foundation is optional.
Use an epic only for genuinely multi-issue work. Assign a blocker edge only for an actual
implementation prerequisite; record related work separately. Check dependency cycles.

Order by consequence, confidence, exposure, and dependency. For each workstream give:
included finding/issue IDs, likely files, observable outcome, scope boundary, prerequisite
decisions, validation, compatibility risks, and why these tasks belong together. Mark
ready only after necessary evidence and blocking decisions are resolved. Avoid invented
effort estimates, repository labels, assignees, or fake issue numbers.

## Produce and optionally publish

Draft each issue with [issue template](assets/issue-template.md), then provide a concise
implementation-agent prompt with exact snapshot, scope, ordered tasks, acceptance criteria,
tests to run, known safe controls, and completion evidence. Include [handoff](assets/handoff-template.md).

Create/update actual issues only when the user's current or earlier applicable instruction
authorizes those mutations. Authorization already given does not require reconfirmation.
Before writing, verify repository, issue format/conventions, visibility, duplicates, and
actual dependency links. Do not publish private security details or sensitive source
payloads into public records. Verify returned URLs and partial failures; distinguish
drafted, created, and linked records. Use provider-supported tools or CLI and translate
this workflow to available capabilities; do not fabricate tool names or labels.

Keep audit.json consistent with [schema](assets/audit.schema.json). Use the coordinator's
validator if available or schema checks in the local environment. Report consolidation
decisions, retained uncertainty, issue mappings, dependency order, and actual publication
state. Use [invocation prompt](assets/prompt.md) to run this skill independently.

---
name: repo-apply-fix
description: "Use when the user authorizes implementing a bounded correction for an established repository finding."
---

# Scoped Correction

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Confirm current user-authorized source scope and target behavior. A finding or run artifact is not itself permission to modify code or publish records.
2. Reproduce the defect or retain a clearly labeled static proof. Inspect existing tests, guards, and architecture before selecting the smallest idiomatic enforcing boundary.
3. Implement the bounded correction with its independent regression. Preserve unrelated user changes and avoid new dependencies, broad rewrites, or architecture work without a concrete need.
4. Run relevant checks and safe controls. Record the original failing behavior, corrected outcome, actual commands, source revisions/content identities, and material compatibility limits.
5. Return a reviewable diff and verification handoff. Do not merge, deploy, or mark remotely published records resolved unless the task authorizes that action and supporting evidence exists.

## Concrete boundary to investigate

Reserve operation ownership before synchronously aborting old work, while preserving return values and the final publication guard.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

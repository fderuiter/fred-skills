---
name: repo-verify-fix
description: "Use when checking whether a proposed or merged correction actually resolves a reported defect without breaking safe behavior."
---

# Fix Verification

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Tie the original claim, reproduction, and intended contract to exact before/after source snapshots. A closed issue or merged PR is not verification.
2. Execute the original failure against the original source in an isolated environment when available, then execute the independent regression against the corrected source.
3. Exercise close safe controls, both event orders, relevant negative cases, and supported compatibility paths. Validate the enforcing mechanism, not just a changed line or a passing assertion count.
4. Write the verification artifact using the supplied schema. Set reproduction/regression/control flags true only when those checks actually ran and passed their respective expected outcomes; otherwise retain pending verification.
5. Recommend remediation state separately from evidence certainty. A later audit not observing the issue is not proof of correction. Preserve commands, outcomes, revisions, and untested limits.

## Concrete boundary to investigate

The newer request must retain publication authority in both completion orders and during synchronous abort-listener reentrancy.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

Write structured proof using [verification schema](assets/verification.schema.json).

---
name: repo-verify-finding
description: "Use when verifying, reproducing, refuting, or narrowing an existing repository finding."
---

# Finding Verification

Read [workflow protocol](references/workflow-protocol.md) once before the investigation.
Use provided run/audit schemas when available; keep unrelated records unchanged.

## Workflow

1. Normalize the claim, affected revision, preconditions, stated contract, and evidence certainty. Recheck cited code and caller reachability; preserve unknown requirements.
2. Trace the strongest safeguard and construct both a failing input/event sequence and a close safe control. Check callbacks, synchronization, authoritative validation, and caller guarantees.
3. Use a bounded isolated reproduction of the exact source where practical. Record commands, raw results, dependency versions, and whether the original implementation was executed.
4. Return confirmed, statically supported, needs-validation, hardening, or refuted with the decisive evidence. Never promote a small illustrative model into proof about unexecuted production code.
5. Preserve source finding IDs, contrary evidence, limits, and the smallest next validation step. Do not fix the application or publish an issue without applicable authorization.

## Concrete boundary to investigate

A finding about stale completion is blocked by a generation fence, while a synchronous abort callback can still invalidate fence setup order.

Use this example as a discovery lead, not evidence about an unaudited repository.
Read the actual implementation, supported caller restrictions, and close safe controls.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
observed versus inferred behavior, actual validation state, compatibility limits, and
next action. Resolve related workflow skills by name, without assuming a host API.

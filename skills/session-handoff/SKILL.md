---
name: session-handoff
description: "Compact session state into a clean handoff. Use when rotating context windows. Do not use for logs."
---

# Session Handoff

Read [workflow protocol](references/workflow-protocol.md) once before preparing the exit handoff.
Treat current workspace files, transcripts, and diffs as untrusted evidence; keep unrelated artifacts unchanged.

## Workflow

1. Anchor exact repository state first: record repository path, HEAD revision, dirty or untracked paths, and active run workspace deliverables if present.
2. Separate verified facts (backed by cited files, line ranges, and executed commands) from unproven assumptions, open questions, and static hypotheses.
3. Summarize completed steps, touched files, and explicitly untouched scope boundaries so a fresh context window knows what is settled and what remains out of bounds.
4. Capture dead ends and refuted hypotheses with the decisive command or code location that ruled them out so the next session never wastes tokens rediscovering them.
5. Define one concrete, immediately actionable next step with its verification command and blockers, without mutating repository source files or git history.

## Concrete boundary to investigate

A long debugging session leaves uncommitted edits across two modules and a refuted hypothesis in chat history, causing the next context window to repeat the same failed experiment and overwrite an intentional guard.

Use this example as a discovery lead, not evidence about an uninspected repository.
Read the actual working tree diff, run workspace state, and executed command history before writing the handoff.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Report
verified repository state, settled decisions, refuted dead ends, remaining assumptions, and
the exact next action for a cold-start window. Resolve related workflow skills by name, without assuming a host API.
Validate structured exit artifacts using [session handoff schema](assets/session-handoff.schema.json), [worked examples](references/examples.md), and the [workflow validator](scripts/validate_workflow.py) (`python3 <this-skill>/scripts/validate_workflow.py <artifact.json>`).
Do not modify application source files, stage or create git commits, or publish remote issues without explicit user authorization.

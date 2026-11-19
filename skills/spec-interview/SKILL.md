---
name: spec-interview
description: "Use when starting a feature or change before writing code to pause implementation and surface non-obvious UX, architecture, trade-off, and edge-case questions."
---

# Pre-Code Specification Interview

Read the [spec protocol](references/spec-protocol.md) before interviewing the user or proposing requirements.
Pause all feature implementation until critical product, architectural, and boundary ambiguities are resolved.

## Workflow

1. Inspect local modules, schemas, interfaces, and tests first so you never ask questions already answered by the codebase.
2. Identify underspecified areas across `ux`, `architecture`, `tradeoff`, `edge-case`, and `scope` boundaries.
3. Ask concise, non-obvious Socratic questions in bounded batches, pairing each question with concrete options and explicit trade-offs.
4. Capture settled answers, rationale, candidate `new_file_scopes`, and `out_of_scope` exclusions in `spec.json` (`status: "interviewing"` while open, `"approved"` once settled).
5. Defer full `SPEC.md` synthesis to `spec-authoring` and post-plan drift checks to `spec-convergence`.

## Guardrails

- Do not generate, modify, or scaffold application code or test suites during `spec-interview` runs.
- Never create or follow symlinks, traverse parent paths (`..`), or invoke external network calls.
- Avoid trivial or generic questions; ground every prompt in observed repository constraints and failure modes.

## Deliver

Structure notes for handoff to the [spec template](assets/spec-template.md) and validate `spec.json` against the [spec schema](assets/spec.schema.json).
Keep `interview_decisions`, `new_file_scopes`, `out_of_scope`, and `verification_commands` explicit, closed-schema compliant, and free of undeclared properties.

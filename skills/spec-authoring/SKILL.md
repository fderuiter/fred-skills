---
name: spec-authoring
description: "Compile settled decisions into SPEC.md files. Use when authoring specs. Do not use for ADR history."
---

# Specification Authoring

Read the [spec protocol](references/spec-protocol.md) and [worked examples](references/examples.md) before drafting specification artifacts.
Translate settled pre-code decisions and verified codebase constraints into an unambiguous contract before implementation begins.

## Workflow

1. Review settled `interview_decisions` and inspect referenced repository files to ground interfaces, invariants, and module paths.
2. Refuse to finalize `status: "approved"` if critical product or architectural ambiguities remain open; route unresolved questions back to `spec-interview`.
3. Draft `SPEC.md` following the [spec template](assets/spec-template.md), detailing scenarios, testable `REQ-*` requirements, edge cases, and non-goals.
4. Declare exact repository-relative `new_file_scopes`, explicit `out_of_scope` exclusions, and runnable `verification_commands`.
5. Emit `spec.json` and validate strict Draft 2020-12 conformance against the [spec schema](assets/spec.schema.json).

## Guardrails

- Do not create, modify, or delete application source files or repository test suites during `spec-authoring` runs.
- Never create or follow symlinks, emit absolute or `..` traversal paths in `new_file_scopes`, or invoke network calls.
- Never invent undeclared fields in `spec.json`; every object in `assets/spec.schema.json` enforces `additionalProperties: false`.

## Deliver

Emit synchronized `SPEC.md` (via the [spec template](assets/spec-template.md)) and `spec.json` (validated against the [spec schema](assets/spec.schema.json) with the [spec validator](scripts/validate_spec.py)).
Ensure `interview_decisions`, `new_file_scopes`, `out_of_scope`, and `verification_commands` agree between `SPEC.md` and `spec.json` for downstream `spec-convergence` checks.

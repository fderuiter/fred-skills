---
name: spec-convergence
description: "Use when verifying that planned task breakdowns and implemented repository changes align with SPEC.md scope boundaries, non-goals, and verification commands."
---

# Specification Convergence Verification

Read the [spec protocol](references/spec-protocol.md) and [worked examples](references/examples.md) before evaluating plan or implementation alignment.
Verify that planned tasks and modified repository files honor `SPEC.md` and `spec.json` boundaries without scope drift.

## Workflow

1. Load `SPEC.md` and `spec.json` to enumerate settled `interview_decisions`, `requirements`, `new_file_scopes`, `out_of_scope` exclusions, and `verification_commands`.
2. Inspect planned task breakdowns and modified or created repository paths against `new_file_scopes` and `out_of_scope`.
3. Map every `REQ-*` requirement and settled decision to concrete plan tasks or implemented code locations, flagging `missing` items.
4. Flag any planned or modified path outside `new_file_scopes` or violating `out_of_scope` as `out-of-scope`, and check `verification_commands` execution evidence (`aligned` vs `unverified`).
5. Record structured `convergence_checks` in `spec.json`, setting `status` to `"converged"` only when every check is `"aligned"` and `"diverged"` otherwise.

## Guardrails

- Do not modify application source files to mask convergence drift unless explicitly authorized; never rewrite `SPEC.md` silently to excuse out-of-scope changes.
- Never create or follow symlinks, never make external network calls, and never claim `verification_commands` passed unless actually executed.

## Deliver

Update `spec.json` with `convergence_checks` validated against the [spec schema](assets/spec.schema.json) via the [spec validator](scripts/validate_spec.py), report drift against the [spec template](assets/spec-template.md) checklist, and load the [invocation prompt](assets/prompt.md) when running standalone convergence checks.
Cite exact file paths, requirement IDs, and command exit statuses for every `aligned`, `missing`, `out-of-scope`, or `unverified` check.

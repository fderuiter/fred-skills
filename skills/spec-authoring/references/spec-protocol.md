# Spec-Driven Development Protocol

## Core Invariants

1. **Specification Before Implementation**: Pause application code generation until ambiguous product, UX, architectural, tradeoff, and edge-case questions are surfaced and resolved in an explicit contract (`SPEC.md` and `spec.json`).
2. **Codebase-First Grounding**: Inspect existing modules, schemas, interfaces, and tests locally before interviewing the user. Never ask the user questions whose answers are already determinable from repository files.
3. **Explicit Scope Boundaries**: Every approved specification must declare both `new_file_scopes` (repository-relative paths or directory prefixes authorized for creation or modification) and `out_of_scope` exclusions so downstream planning and implementation cannot silently expand scope.
4. **Executable Verification Contract**: Every specification must record concrete `verification_commands` that prove behavioral acceptance and non-regression before convergence can pass.

## Suite Phases and Handoffs

### Phase 0: Pre-Code Discovery (`spec-interview`)

- Pause all feature implementation and code scaffolding.
- Read relevant repository modules, configuration, and tests to establish factual constraints.
- Surface concise, non-obvious questions covering `ux`, `architecture`, `tradeoff`, `edge-case`, and `scope` dimensions—one bounded batch at a time with concrete options and tradeoffs.
- Record settled answers or open blockers in `spec.json` (`interview_decisions`, `status: "interviewing"` or `"draft"` when unresolved, `"approved"` once ready for authoring).

### Phase 1: Specification Authoring (`spec-authoring`)

- Synthesize settled `interview_decisions` and repository evidence into a clear `SPEC.md` document structured with `assets/spec-template.md`.
- Emit `spec.json` validated against `assets/spec.schema.json` (JSON Schema Draft 2020-12, `additionalProperties: false`), populating `interview_decisions`, `requirements`, `new_file_scopes`, `out_of_scope`, and `verification_commands`.
- Refuse to promote `status` to `"approved"` if critical interview ambiguities remain unresolved or if `new_file_scopes`, `out_of_scope`, or `verification_commands` are empty without explicit justification.

### Phase 2: Convergence Verification (`spec-convergence`)

- Compare planned task breakdowns and implemented repository changes against `SPEC.md` and `spec.json`.
- Check every requirement, file path, and verification command, recording structured `convergence_checks` entries with status `aligned`, `missing`, `out-of-scope`, or `unverified`.
- Flag any touched or planned file outside `new_file_scopes` or matching `out_of_scope` as `out-of-scope`, and set top-level `status` to `"diverged"` whenever any check is `missing`, `out-of-scope`, or `unverified`; set `"converged"` only when all checks pass.

## Guardrails and Safety Prohibitions

- **No Premature Application Edits**: Do not create, modify, or delete application source files or test suites during `spec-interview` or `spec-authoring` runs; write outputs only to workspace artifacts (`spec.json`, `SPEC.md`, and run reports).
- **No Symlinks or Path Escapes**: Never create or follow symbolic links, absolute paths, drive-prefixed paths, or `..` parent-traversal segments in `new_file_scopes` or workspace artifacts.
- **No Network Calls**: Never invoke external network requests, remote APIs, or package downloads; rely strictly on local repository files and user-provided context.
- **Strict Closed Schema**: Never add undeclared keys such as `run_id` or `findings` to `spec.json`; every object in `assets/spec.schema.json` enforces `additionalProperties: false`.

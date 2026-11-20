# Spec-Driven Development Protocol

Standard engineering protocol for pre-code discovery interviews, executable feature
specification authoring, and post-plan or post-implementation convergence verification. Every
skill in the `spec-driven-development` suite bundles an identical copy of this reference so
standalone installations operate offline without sibling filesystem dependencies.

## 1. Core Invariants and Authority Guardrails

1. **Specification Before Implementation**: Pause application code generation until ambiguous
   product, UX, architectural, trade-off, and edge-case questions are surfaced and resolved in
   an explicit contract (`SPEC.md` and `spec.json`).
2. **Codebase-First Grounding**: Inspect existing modules, wire schemas, interfaces,
   migrations, and test suites locally before interviewing the user. Never ask the operator
   questions whose answers are already determinable from repository source files.
3. **Explicit Positive and Negative Scope Boundaries**: Every approved specification must
   declare both `new_file_scopes` (repository-relative paths or directory prefixes authorized
   for creation or modification) and `out_of_scope` exclusions so downstream planning and
   implementation cannot silently expand scope.
4. **Executable Verification Contract**: Every specification must record concrete
   `verification_commands` that prove behavioral acceptance and non-regression before
   convergence can pass.
5. **Non-Mutation Default**: Do not create, modify, or delete application source files or
   repository test suites during `spec-interview` or `spec-authoring` runs, and do not modify
   source code or rewrite `SPEC.md` silently to mask drift during `spec-convergence` runs.

## 2. Lifecycle Status, Category, and Convergence Rubrics

### Specification Lifecycle Status (`status`)

- `interviewing`: Pre-code Socratic discovery is active; one or more blocking product,
  interface, failure-mode, or scope questions await operator decisions.
- `draft`: Initial discovery notes or candidate requirements are recorded, but open trade-offs,
  empty verification commands, or unconfirmed scope boundaries prevent implementation sign-off.
- `approved`: All critical `interview_decisions` are settled, `requirements`,
  `new_file_scopes`, `out_of_scope`, and `verification_commands` are populated and consistent
  between `SPEC.md` and `spec.json`, and downstream planning or implementation may proceed.
- `converged`: Every planned task and modified repository path aligns with `new_file_scopes`,
  respects `out_of_scope`, satisfies all `REQ-*` requirements, and passes executed
  `verification_commands` (`convergence_checks[*].status == "aligned"`).
- `diverged`: At least one convergence check is `missing`, `out-of-scope`, or `unverified`.

### Interview Decision Categories (`interview_decisions[*].category`)

- `ux`: CLI flags, API request/response ergonomics, error messages, pagination, or UI states.
- `architecture`: Module ownership, storage authority, concurrency model, or wire boundaries.
- `tradeoff`: Competing latency, memory, consistency, compatibility, or operational costs.
- `edge-case`: Partial failure recovery, empty/malformed inputs, clock skew, retries, or limits.
- `scope`: Explicit inclusions vs. deferred capabilities locked into `out_of_scope`.

### Convergence Check Outcomes (`convergence_checks[*].status`)

- `aligned`: Requirement, decision, file scope, or verification command is satisfied and backed
  by concrete file paths or executed command exit code `0`.
- `missing`: A settled `REQ-*` requirement or `DEC-*` decision has no corresponding plan task
  or implemented code enforcement.
- `out-of-scope`: A planned task or modified repository path falls outside `new_file_scopes` or
  violates an explicit `out_of_scope` exclusion.
- `unverified`: Implementation exists, but required `verification_commands` were not executed or
  failed locally.

## 3. Counterexamples and Safe Controls

Challenge candidate questions, specifications, and convergence verdicts against close safe
controls before emitting deliverables:

- **Counterexample — Codebase-Answerable Interview Question vs. Grounded Trade-Off Prompt**:
  Asking "Which database library or test runner does this project use?" wastes operator turns
  when `pyproject.toml` and `tests/` already show SQLite and `unittest`. *Safe control*: Read
  repository manifests first, cite the observed constraint, and ask only genuine product or
  policy choices (e.g., whether cursor expiration should return HTTP `410 Gone` or restart from
  the snapshot head).
- **Counterexample — Vague Aspiration Requirement vs. Testable `REQ-*` Contract**:
  Writing `REQ-1: Export should be fast and handle errors gracefully` gives no falsifiable
  oracle. *Safe control*: `REQ-1 (must): Stream NDJSON batches in <= 500-row chunks without
  buffering the full result set in memory, and abort mid-stream failures with a trailing error
  record and non-zero exit status.`
- **Counterexample — Rubber-Stamp Convergence vs. Strict Scope and Execution Proof**:
  Marking `status: "converged"` when a refactor also touched `src/billing/ledger.py` outside
  `new_file_scopes` or when tests were only inspected statically hides scope creep. *Safe
  control*: Record `status: "out-of-scope"` for `src/billing/ledger.py`, `status: "unverified"`
  for unexecuted commands, and set top-level `status` to `"diverged"`.

## 4. High-Frequency Edge Cases and Failure Modes

1. **Overlapping `new_file_scopes` and `out_of_scope` Rules**: Directory prefixes in
   `new_file_scopes` (e.g., `src/exporter/`) can accidentally encompass sensitive submodules
   listed in `out_of_scope` (e.g., `src/exporter/legacy_csv.py`). Explicit `out_of_scope`
   exclusions always take precedence over broad directory prefixes.
2. **Premature Promotion to `approved`**: Do not set `status: "approved"` while placeholder
   decisions (`TBD`, `ask user later`) or empty `verification_commands` remain; keep `status`
   at `"interviewing"` or `"draft"` and route back to `spec-interview`.
3. **Narrative vs. JSON Drift**: Updating `SPEC.md` prose via `assets/spec-template.md` without
   synchronizing `spec.json` leaves downstream `spec-convergence` checking stale scopes. Keep
   IDs (`DEC-*`, `REQ-*`, `CHK-*`), paths, and commands identical across both artifacts.
4. **Path Safety and Closed Schema Violations**: Never emit absolute paths (`/etc/...`), Windows
   drive paths (`C:\...`), backslashes, symlinks, or `..` segments in `new_file_scopes`, and
   never add undeclared fields (`run_id`, `findings`) to `spec.json` (`additionalProperties: false`).

## 5. Deliverable Contract and Deterministic Validation

1. Pair human-readable `SPEC.md` sections (`assets/spec-template.md`) with machine-readable
   `spec.json` validated against `assets/spec.schema.json` (JSON Schema Draft 2020-12).
2. Ensure every `id` in `interview_decisions`, `requirements`, and `convergence_checks` is
   unique within its list, every `new_file_scopes` entry is repository-relative, and
   `status: "converged"` is used only when every convergence check is `"aligned"`.
3. Run the bundled validator locally before concluding:
   `python3 <this-skill>/scripts/validate_spec.py <spec.json>`
   This script checks Draft 2020-12 schema conformance, unique decision/requirement/check IDs,
   repository-relative `new_file_scopes`, non-overlapping `out_of_scope` rules, and convergence
   status consistency using only the Python 3.12 standard library.

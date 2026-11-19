# Feature Specification: <Feature Title>

- **Status**: `draft` | `interviewing` | `approved` | `converged` | `diverged`
- **Primary Artifact**: `spec.json`

## 1. Objective and Repository Context

Summarize the user-visible goal, affected subsystem boundaries, and existing repository constraints discovered before authoring.

## 2. Settled Interview Decisions

| ID | Category | Question | Decision | Rationale |
| --- | --- | --- | --- | --- |
| `DEC-001` | `ux` / `architecture` / `tradeoff` / `edge-case` / `scope` | Non-obvious design question | Chosen option | Concrete tradeoff accepted |

## 3. User Scenarios and Edge Cases

- **Primary Flow**: Describe expected inputs, state transitions, and observable outputs.
- **Failure and Edge Cases**: Specify validation errors, concurrency/idempotency behavior, backward compatibility, and empty/boundary states.

## 4. Functional Requirements

- **`REQ-001` (`must`)**: Testable requirement tied to settled decisions and repository contracts.
- **`REQ-002` (`should`)**: Secondary behavioral or operational requirement.

## 5. File Scopes and Explicit Non-Goals

### Approved File Scopes (`new_file_scopes`)

List repository-relative file paths or directory prefixes authorized for creation or modification:

- `src/<module>/<file>`
- `tests/<test_file>`

### Out of Scope (`out_of_scope`)

List adjacent features, subsystems, migrations, or CLI/API surfaces explicitly excluded from this change:

- Unrelated refactors, schema migrations, or public CLI/API additions not listed above.

## 6. Verification Commands (`verification_commands`)

List deterministic local commands required to prove conformance:

- `pytest tests/<test_file>`

## 7. Convergence Checklist (`convergence_checks`)

- [ ] Every `REQ-*` requirement maps to planned tasks or implemented code within `new_file_scopes`.
- [ ] Zero modified or planned files fall outside `new_file_scopes` or violate `out_of_scope`.
- [ ] All `verification_commands` execute locally and exit `0`.

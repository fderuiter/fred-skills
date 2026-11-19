Use repo-plan-fix on <repository/revision> with <finding/run artifacts> scoped to <directory>, <symbol>, and issue <issue-number>.
Revalidate current source, tests, and open issues/PRs; group verified defects by violated contract, authoritative boundary, and minimal corrective mechanism.
Sequence acyclic remediation steps across contracts, implementations, callers, and regression tests with independent acceptance criteria.
Write `plan.json` validated against `assets/plan.schema.json` and return a standalone remediation plan handoff.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code, invent issue metadata, or publish external changes without authorization.

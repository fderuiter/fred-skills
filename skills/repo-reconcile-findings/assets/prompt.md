Use repo-reconcile-findings on <repository/revision> with <finding/run artifacts> across <directory>, <symbol>, and issue <issue-number>.
Match findings across runs by semantic fingerprint, root-cause mechanism, and code evidence rather than volatile run-local IDs or line numbers.
Compare prior findings, remediation artifacts, and verified fixes to classify new, recurring, resolved, regressed, or ambiguous items honestly.
Write `reconciliation.json` and `remediation.json` validated against `assets/reconciliation.schema.json` and return a standalone handoff.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or mutate remote issue state without explicit authorization.

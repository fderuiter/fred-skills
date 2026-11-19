Use repo-audit-failure-modes to audit partial failures, timeouts, and recovery paths in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory multi-step workflows, external RPC/storage dependencies, timeout budgets, durable checkpoints, crash-recovery routines, and compensating actions.
Enumerate failure before effect, failure after effect before acknowledgement, partial batch failure, process crash, and cascading dependency degradation.
Verify whether retries, compensations, and operator signals leave invariants and user-visible state recoverable, citing file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

Use repo-audit-idempotency to audit retry safety, deduplication, and replay idempotency in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory mutating commands, webhook/event consumers, scheduled jobs, client reconnect loops, startup initializers, idempotency-key stores, and retry policies.
Verify key scoping (tenant, actor, operation, payload hash), retention TTLs, concurrent in-flight duplicate locking, and atomicity between dedup state and side effects.
Expose duplicate billing/side-effect windows, key collisions, and poisoned failed-attempt records with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

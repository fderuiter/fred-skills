Use repo-audit-transactions to audit transactional atomicity, isolation, and outbox boundaries in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory database transactions, isolation levels, connection enlistment, file-plus-metadata writes, state-plus-message publication, and multi-service sagas.
Trace each consistency unit's commit point, rollback completeness, external RPC calls held inside open transactions, and phantom/write-skew anomalies.
Expose split commits where DB rollback leaves emitted events or external mutations uncompensated, citing file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

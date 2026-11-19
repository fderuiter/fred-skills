Use repo-audit-compatibility to audit backward/forward schema and wire compatibility in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory persisted database/file schemas, browser storage keys, CLI flags, public API versions, rolling-deploy mixed readers/writers, and rollback checkpoints.
Verify supported version pairs against actual release policy; trace old-reader/new-writer and new-reader/old-writer behavior across migrations and rollbacks.
Pinpoint breaking field removals, enum narrowing, default semantic flips, and irreversible migrations with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

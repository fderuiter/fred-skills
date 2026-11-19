Use repo-audit-sources-of-truth to audit split authority and derived-state divergence in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory authoritative tables/files, denormalized counters, status projections, client/UI stores, DTO snapshots, read replicas, and reconciliation jobs.
Map every representation of the same domain fact to its single write authority, derivation pipeline, staleness bound, and conflict resolution rule.
Expose dual-write drift, stale derived decisions, and unversioned overwrite hazards with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

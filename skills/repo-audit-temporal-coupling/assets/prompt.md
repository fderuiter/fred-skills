Use repo-audit-temporal-coupling to audit initialization order and happens-before coupling in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory configure/init/start/stop lifecycles, DI/service bootstraps, lazy singletons, async readiness gates, DB schema migrations, and graceful shutdown sequences.
Model required call ordering as explicit happens-before edges and inspect callers for omitted init, duplicate start, out-of-order use, or teardown-before-drain races.
Prefer compile-time typestate or self-initializing constructors as remedies, backing every finding with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

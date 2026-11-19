Use repo-audit-dead-code to audit unreachable code, orphaned wiring, and vestigial paths in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory CLI/HTTP entrypoints, plugin registries, exports, retired feature flags, compatibility shims, generated artifacts, and reflection/string-dispatched hooks.
Trace reachability from real build, runtime, framework, test, and published package roots before classifying any symbol as dead.
Distinguish provably unreachable code and no-op branches from dynamically loaded extension points with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

Use repo-audit-cancellation to audit cancellation propagation and abort safety in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while referencing issue <issue-number>.
Inventory cancellation roots, abort signals/tokens, spawned child tasks, blocking I/O calls, background queues, teardown hooks, and result publication gates.
Build the cancellation tree to verify who requests, propagates, observes, and joins each operation, checking synchronous abort callbacks for reentrancy hazards.
Identify orphaned background work, half-committed mutations after abort, and swallowed cancellation exceptions with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

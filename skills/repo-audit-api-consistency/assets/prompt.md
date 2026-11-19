Use repo-audit-api-consistency to audit API design consistency in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking context from issue <issue-number>.
Inventory related public methods, HTTP/RPC endpoints, consumer call sites, result and error envelopes, pagination cursors, and documented mutation guarantees.
Build a comparison matrix across analogous operations for parameter naming, units, defaults, return shapes, cancellation semantics, ownership, and side effects.
Distinguish justified domain differences from accidental drift that misleads callers, citing exact file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

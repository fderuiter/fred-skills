Use repo-audit-trust-boundaries to audit trust boundaries, authorization gates, and sink safety in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory HTTP/RPC/CLI/webhook/file inputs, browser-to-server transitions, tenant and role claims, object lookups, SSRF/command/SQL/template sinks, and secret stores.
Trace untrusted data from entry point through parsing, policy enforcement, tenant-scoped resource lookup, and privileged sink execution.
Identify missing object- or operation-level authorization, confused-deputy paths, and unvalidated sink injection with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

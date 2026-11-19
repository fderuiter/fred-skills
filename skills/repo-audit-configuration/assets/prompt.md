Use repo-audit-configuration to audit configuration precedence, validation, and rollout safety in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory feature flags, environment variables, CLI/config file defaults, remote dynamic settings, build-time constants, capability probes, and reload lifetimes.
Map each setting's type coercion, fallback authority, override precedence, evaluation timing, and cross-option compatibility constraints.
Identify silent misconfiguration fallbacks, partial hot-reload torn reads, and unsafe default combinations with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

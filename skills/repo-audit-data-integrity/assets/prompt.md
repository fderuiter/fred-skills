Use repo-audit-data-integrity to audit end-to-end data transformation integrity in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing issue <issue-number>.
Inventory input parsers, DTO-to-domain mappers, ORM/SQL persistence bindings, wire serializers, CSV/JSON exports, unit/currency math, and timezone projections.
Build field-level transformation chains tracking precision, rounding, signedness, encoding, identifier provenance, and absent-versus-null-versus-empty semantics.
Prove silent truncation, floating-point drift, timezone shifts, or dropped unknown fields with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

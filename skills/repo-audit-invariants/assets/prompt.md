Use repo-audit-invariants to audit state invariants and enforcement completeness in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory domain aggregates, constructor/setter guards, bulk/admin write paths, deserializers, database check/unique constraints, and protocol completion gates.
State each invariant as an explicit predicate with temporal scope (always, transaction boundary, or bounded eventual) and map every write path touching its fields.
Identify bypass paths—migrations, imports, reflection, partial updates, or race windows—that violate established predicates with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

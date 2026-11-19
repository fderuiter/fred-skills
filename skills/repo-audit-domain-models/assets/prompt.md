Use repo-audit-domain-models to audit domain model fidelity and primitive obsession in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory typed versus raw IDs, physical/monetary units, status enums, temporal intervals, aggregate roots, constructor validators, and repeated tuple arguments.
Locate interchangeable primitive parameters (strings, ints, booleans) that conflate distinct domain concepts, unvalidated aggregate mutations, and split invariants.
Ground every domain modeling finding in real caller bugs or ambiguous state combinations with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

Use repo-audit-architecture to audit module boundary and dependency architecture in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> alongside context from issue <issue-number>.
Inventory packages, declared dependency rules, runtime composition roots, public APIs, direct database access, event buses, and generated client couplings.
Derive intended architectural layering from repository ownership rather than generic dogma, tracing compile-time imports and runtime cross-layer bypasses.
Isolate cyclic dependencies, leaky abstractions, and unauthorized boundary crossings with file/symbol/line evidence and independent acceptance criteria.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

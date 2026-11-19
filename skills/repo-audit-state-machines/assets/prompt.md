Use repo-audit-state-machines to audit explicit and implicit state machine transitions in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory status enums, coupled boolean flag clusters, reducers, workflow orchestrators, UI interaction modes, retry loops, persistence hydration, and terminal states.
Reconstruct reachable states and build the transition table (source, event, guard, side effect, target), separating orthogonal regions from illegal flag combinations.
Identify unreachable recovery paths, dead-end non-terminal states, illegal transition bypasses, and out-of-order event corruption with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

Use repo-audit-behavioral-contracts to reconstruct and audit behavioral contracts in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while cross-referencing issue <issue-number>.
Inventory actors, entry points, preconditions, postconditions, durable records, external effects, versioned interfaces, and enforcement boundaries.
Separate observed implementation behavior from independently evidenced product requirements; never derive intended semantics solely from the code under review.
Trace real callers and adversarial sequences to separate reproduced contract violations, unproven assumptions, and refuted candidates.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

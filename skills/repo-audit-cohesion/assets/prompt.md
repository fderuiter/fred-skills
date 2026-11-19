Use repo-audit-cohesion to audit module cohesion and responsibility separation in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing context from issue <issue-number>.
Inventory module responsibilities, exported interfaces, caller subsets, domain state ownership, lifecycle coupling, and side-effect clusters.
Name each module's distinct domain invariants and consumer groups, identifying god objects, shotgun-surgery hubs, and unrelated lifecycle coupling.
Propose minimal seam splits grounded in actual caller usage and file/symbol/line evidence rather than speculative abstractions.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

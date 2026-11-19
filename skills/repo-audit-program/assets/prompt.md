Use repo-audit-program to orchestrate a full hidden-contract audit of <repository> at <revision> focused on <directory> and <symbol>.
Prioritize <critical workflows>, reuse <existing audit workspace> if present, and incorporate context from issue <issue-number>.
Complete behavioral discovery, correctness/resilience, and architecture/maintainability slices while keeping all applicable topics in the coverage ledger.
Trace real caller/consumer boundaries, challenge every candidate finding against safeguards, and preserve stable contract/evidence/finding IDs.
Consolidate root causes into an ordered issue plan, `audit.json` conforming to `assets/audit.schema.json`, and a standalone implementation handoff.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

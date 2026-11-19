Use repo-audit-nondeterminism to audit uncontrolled nondeterminism and reproducibility hazards in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory wall-clock versus monotonic time calls, unseeded PRNG/UUID generation, hash/directory iteration order, locale/timezone sensitivity, and shared test state.
Separate intentional runtime entropy from uncontrolled ordering, clock skew, or scheduler dependencies that destabilize outputs, digests, builds, or tests.
Trace non-reproducible outputs and flaky test dependencies to exact sources with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

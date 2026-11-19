Use repo-audit-test-oracles to audit test oracle independence and mock fidelity in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory test assertion bodies, expected-value helpers, shared fixtures, mocks/stubs, snapshot baselines, property invariants, and negative failure checks.
Flag tautological tests that recompute expected outputs via the implementation under test, permissive assertions, and mocks that stub away the real failure boundary.
Verify whether each test would actually fail under realistic regressions in its target contract, citing file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

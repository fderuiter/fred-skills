Use repo-audit-test-contracts to audit behavioral contract test coverage in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory independently supported behavioral contracts, existing unit/integration/e2e suites, boundary assertions, negative/auth-denial cases, and recovery paths.
Distinguish line execution coverage from actual behavioral verification across valid inputs, boundary rejections, concurrent/duplicate events, and failure rollbacks.
Rank unverified high-risk contracts and specify minimal executable test boundaries with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

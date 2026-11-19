Use test-gap-analysis to discover uncovered behavioral contracts for <repository> at <revision> across <directory>, <symbol>, and issue <issue-number>.
Map existing test assertions across <critical workflows>, pinpointing untested error branches, boundary edges, and concurrency/rollback paths.
Detect fragile or tautological assertions and rank coverage gaps by regression severity and blast radius.
Write `testing.json` conforming to the testing template and `assets/testing.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify files outside authorized scope or rely on tautological assertions.

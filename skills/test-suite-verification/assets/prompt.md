Use test-suite-verification to execute and validate test suites for <repository> at <revision> across <directory>, <symbol>, and issue <issue-number>.
Run targeted suites covering <critical workflows>, verifying deterministic pass/fail outcomes, test isolation, and bounded execution time.
Confirm failure oracles fail meaningfully when guarded invariants break rather than passing vacuously.
Write `testing.json` conforming to the testing template and `assets/testing.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify production code unauthorized, and never claim unexecuted tests ran.

Use test-flakiness-isolation to isolate intermittent test failures and nondeterminism for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Classify timing/sleep races, order-dependent shared state, clock/seed drift, and port/path contention, separating harness flake from real production races.
Record prioritized stabilization `test_cases` and `execution_summary` in `testing.json` conforming to `assets/testing.schema.json`.
Do not mask failures with retry wrappers or write repository files outside the run workspace without explicit user permission.

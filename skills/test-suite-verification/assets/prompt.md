Use test-suite-verification to execute and audit automated test suites for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Execute non-destructive test commands, verify collected/skipped counts and oracle sensitivity, and confirm clean post-run repository state.
Never mark unexecuted or silently skipped tests as `verified: true`; record exact `execution_summary` counts and `commands` in `testing.json` conforming to `assets/testing.schema.json`.
Do not modify repository source or test files outside the run workspace without explicit user permission.

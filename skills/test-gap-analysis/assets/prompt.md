Use test-gap-analysis to uncover behavioral test gaps and weak assertion oracles for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Compare supported contracts and error paths against actual unit and integration assertions rather than line counts alone.
Assign `priority` and `oracle_type` on `test_cases` and record honest `execution_summary` state in `testing.json` conforming to `assets/testing.schema.json`.
Do not create or modify repository files outside the run workspace without explicit user permission.

Use test-case-generation to specify deterministic regression and unit test cases for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Construct `test_cases` with independent `oracle_type`, `priority`, fixed preconditions, and clean teardowns—never copying production formulas into expected values.
Record specifications and `execution_summary` in `testing.json` conforming to `assets/testing.schema.json`.
Do not write or modify test or source files in the repository outside the run workspace unless explicit user permission (`--allow-fixes`) is granted.

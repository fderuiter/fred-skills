Use test-contract-harnesses to specify producer-consumer wire harnesses and fake-parity checks for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Compare producer serializers, canonical schemas, and consumer mocks/parsers to eliminate silent wire drift.
Record prioritized `test_cases` and `execution_summary` in `testing.json` conforming to `assets/testing.schema.json`.
Do not hit external production endpoints or write repository files outside the run workspace without explicit user permission.

Use review-api-compatibility to review public symbol, CLI, and wire compatibility changes for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Compare before/after signatures, required fields, serializer defaults, response keys, and status codes across exported surfaces.
Distinguish public multi-version contracts from private in-repo helpers before reporting breaking changes.
Record evidence-backed `findings` in `review.json` conforming to `assets/review.schema.json`.
Do not edit repository source or schema files outside the run workspace without explicit user permission.

Use review-diff-analysis to map differential blast radius and affected callers for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Compare before/after control flow, deleted guards, and untouched callers outside the diff hunks.
Verify whether all repository call sites were updated consistently before reporting caller breakage.
Record evidence-backed `findings` in `review.json` conforming to `assets/review.schema.json`.
Do not edit repository source or test files outside the run workspace without explicit user permission.

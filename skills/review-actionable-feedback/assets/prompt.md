Use review-actionable-feedback to deduplicate, calibrate, and synthesize code review findings for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Consolidate overlapping observations into single root-cause `findings`, verify `file:line` citations, and align `verdict` with confirmed severity.
Do not invent nitpicks on clean diffs or approve breaking contract/security regressions without verified safe controls.
Validate `review.json` against `assets/review.schema.json` and do not modify repository files or publish remote comments without explicit user permission.

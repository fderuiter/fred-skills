Use review-concurrency-hazards to inspect diffs for data races, lock-order inversions, and async interleavings in <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Identify the true concurrency model, inspect all readers/writers of shared state, and construct concrete two-actor schedules.
Verify turn-atomic single-loop or lock-serialized safe controls before reporting races.
Record evidence-backed `findings` in `review.json` conforming to `assets/review.schema.json`.
Do not edit repository source or test files outside the run workspace without explicit user permission.

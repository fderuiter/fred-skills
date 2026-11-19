Use review-invariant-violations to verify domain, state-machine, and rollback invariants for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Establish invariants from constructors, persistence constraints, and callers, then trace early returns and exception exits in the diff.
Check object lifetimes and rollback wrappers as safe controls before claiming partial-mutation bugs.
Record evidence-backed `findings` in `review.json` conforming to `assets/review.schema.json`.
Do not edit repository source or test files outside the run workspace without explicit user permission.

Use arch-component-decomposition to partition capabilities into cohesive `components` and `interfaces` for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect module responsibilities, `owned_models`, and dependency edges to eliminate cycles and shared-state tangles.
Verify whether keeping co-mutating state together is safer than premature splitting before proposing new seams.
Record `components` and `interfaces` in `architecture.json` conforming to `assets/architecture.schema.json` and preserve existing `boundaries` and `decisions`.
Do not modify application code outside the run workspace without explicit user permission.

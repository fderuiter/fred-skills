Use review-security-boundaries to trace auth guards, tenant isolation, and untrusted input sinks for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect modified routes, permissions, deserializers, path/SQL/command sinks, and secret redaction across trust boundaries.
Verify upstream parameterization and ownership guards before reporting security defects.
Record evidence-backed `findings` in `review.json` conforming to `assets/review.schema.json`.
Do not run destructive exploits or edit repository files outside the run workspace without explicit user permission.

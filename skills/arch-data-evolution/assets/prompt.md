Use arch-data-evolution to plan persistence ownership and mixed-version schema evolution for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect all SQL/ORM readers, writers, caches, and background jobs before proposing expand-backfill-contract transitions.
Verify locking, idempotent backfills, and safe controls for single-writer vs rolling deployments.
Record `boundaries`, `components`, `interfaces`, and `decisions` in `architecture.json` conforming to `assets/architecture.schema.json`.
Do not execute migrations or modify repository files outside the run workspace without explicit user permission.

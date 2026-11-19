Use arch-operational-resilience to specify failure isolation, timeout budgets, and overload controls for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect outbound clients, retry loops, connection pools, queues, and shutdown hooks for cascading-failure amplification.
Check whether simple fail-fast behavior is a safe control before prescribing complex bulkheads.
Record `boundaries`, `components`, `interfaces`, and `decisions` in `architecture.json` conforming to `assets/architecture.schema.json`.
Do not modify repository code or deployment manifests outside the run workspace without explicit user permission.

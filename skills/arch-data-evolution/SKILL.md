---
name: arch-data-evolution
description: "Plan zero-downtime schema migrations, persistence ownership, and mixed-version read/write evolution. Use when designing database migrations, splitting shared tables, evolving serialized state, or planning expand-backfill-contract rollouts."
---

# Data and Persistence Evolution

Design safe persistence boundaries and multi-phase schema transitions grounded in actual readers and writers.
Use the supplied repository snapshot; do not assume atomic fleet-wide deployments or offline maintenance windows unless documented.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Follow its
   read-only workspace default, storage boundary rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect ORM models, SQL/DDL migrations,
   serialized blobs, background backfills, and rollback paths before proposing schema changes.
3. Reuse the workspace `architecture.json` if present so existing `boundaries`, `components`,
   `interfaces`, and `decisions` stay consistent. Sibling skills remain optional extensions.

## Inventory

Migration directories, table/index definitions, ORM mappings, serialized JSON/protobuf columns, cache payloads, batch jobs, and read/write query call sites.

## Investigation

1. Map every active reader and writer of target tables, collections, caches, and durable checkpoints across services and background workers.
2. Design expand-backfill-contract phases so version `N` and `N+1` binaries operate concurrently without null-dereference, lock starvation, or silent data loss.
3. Specify `storage` boundaries, authoritative `owned_models` on `components`, and transactional outbox or dual-write reconciliation invariants.
4. Analyze index-creation locking, backfill batching/idempotency, foreign-key enforcement, and point-in-time rollback safety under partial failure.
5. Record irreversible contract-drop prerequisites and verification queries in `decisions` so destructive cleanup happens only after old readers drain.

## Challenge and validate

Use this failure shape only as a discovery lead: A single deployment renames or drops a column while in-flight workers and rolling-upgrade replicas still execute queries against the old column name.

Actively test this counterexample: Single-node CLI SQLite state upgraded exclusively behind an exclusive startup file lock may safely apply sequential DDL without dual-write shims.

Trace mixed-version compatibility across `(old code + new schema)` and `(new code + partially backfilled rows)` before marking a migration plan viable; validate `architecture.json` separately.

Inspect the repository's migration runner and database engine before prescribing DDL syntax. Read
[research references](references/sources.md) only when checking zero-downtime DDL or outbox trade-offs.
No external tool is required.

## Deliver and hand off

Format the migration architecture using [architecture template](assets/architecture-template.md).
Update `boundaries`, `components`, `interfaces`, and `decisions` in `architecture.json` validated against
[architecture schema](assets/architecture.schema.json). Schema validity does not prove lock-free DDL execution.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer deeper handoffs to `arch-component-decomposition`, `arch-operational-resilience`,
`arch-boundary-specification`, or `arch-decision-record` when service extraction or disaster recovery trade-offs emerge.
Do not automatically load sibling skills, execute destructive migrations, or modify repository files outside the run workspace without explicit user permission.

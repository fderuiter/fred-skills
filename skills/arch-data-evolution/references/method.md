# Investigation method: Data and Persistence Evolution

## Scope selection

Trace one persisted entity or table across every DDL script, ORM model, query site, cache
projection, and background worker before expanding to dependent foreign keys or event streams.
Do not plan a migration from ORM class fields alone without inspecting raw SQL and batch jobs.

## Contract worksheet

For each evolving persistence structure, record current schema invariants, reader/writer versions,
lock behavior during DDL, backfill idempotency key, dual-read/dual-write transition invariants,
and rollback cutoffs. Keep rollback claims conservative whenever destructive writes occur.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every candidate table, document collection,
or serialized state format. Record both safe additive steps and hazardous lock/skew windows.

## Adversarial case

A single deployment renames or drops a column while in-flight workers and rolling-upgrade
replicas still execute queries against the old column name.

Check deployment topology, worker queues, and read-replica paths in the repository. If no
concurrent multi-version window exists, state that explicitly rather than forcing unnecessary
dual-write ceremony.

## Safe control

Single-node CLI SQLite state upgraded exclusively behind an exclusive startup file lock may
safely apply sequential DDL without dual-write shims.

Match migration complexity to the actual deployment and concurrency envelope documented in the
codebase.

## Independent validation

Verify mixed-version read/write matrices `(v1 + expanded schema, v2 + unbackfilled rows, rollback to v1)`
against concrete query paths, and validate `architecture.json` against
`assets/architecture.schema.json`.

## Cross-topic boundary

Capture `storage` boundaries, `owned_models`, and migration `decisions` in `architecture.json`,
and hand off module code refactoring to `arch-component-decomposition` or failover/replication
SLO topology to `arch-operational-resilience`.

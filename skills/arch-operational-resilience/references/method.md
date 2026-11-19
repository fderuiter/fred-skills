# Investigation method: Operational Resilience and Failure Containment

## Scope selection

Trace one critical request or background workflow through every outbound dependency, pool,
queue, timeout, and shutdown hook before expanding to secondary subsystems. Do not claim
fault isolation from high-level diagrams without checking client timeout defaults in code.

## Contract worksheet

For each dependency crossing, record caller timeout, downstream SLA, max retry count, backoff
jitter, idempotency safety, pool/queue cap, fallback mode (fail-closed vs degraded), and
drain behavior on shutdown.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every external call, shared pool, and
background worker loop. Record both bounded safeguards and unguarded amplification paths.

## Adversarial case

Every upstream tier retries 3 times without deadline propagation or jitter against a saturated
database pool, multiplying traffic 27x during transient latency spikes.

Compute effective worst-case fan-out across nested retries and check whether caller deadlines
cancel abandoned downstream work. If retries are disabled or bounded by token budgets, record
that safe posture.

## Safe control

Offline batch CLI pipelines running sequentially for a single local operator may fail fast
without circuit breakers or multi-pool bulkheads.

Tailor resilience controls to the actual execution model rather than prescribing service-mesh
machinery for local tools.

## Independent validation

Verify timeout ordering (`caller_deadline > attempt_timeout * max_attempts` or explicit
remaining-budget propagation) and queue bounds in configuration/code, then validate
`architecture.json` against `assets/architecture.schema.json`.

## Cross-topic boundary

Record resilience `boundaries`, `components`, `interfaces`, and `decisions` in
`architecture.json`, and hand off wire-level error code schemas to `arch-integration-contracts`
or durable outbox/checkpoint storage to `arch-data-evolution`.

# Investigation method: Architectural Decision Records

## Scope selection

Anchor on one consequential architectural question end to end—such as storage ownership,
service extraction, or wire protocol selection—before expanding to secondary choices.
Record inspected source paths, manifests, and prior ADRs. Do not extrapolate repository-wide
constraints from a single helper module.

## Contract worksheet

For each candidate decision, record the binding problem statement, hard invariants,
non-functional drivers (latency, durability, deployment cadence), affected stakeholders,
viable alternatives, and supersession conditions. Keep status at `draft` when key operational
inputs remain unverified.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every architectural choice. Record both
accepted rationale and rejected options with concrete cost/risk evidence.

## Adversarial case

An ADR mandates distributed event choreography without accounting for existing single-database
transactional callers or idempotency cost.

Trace whether existing callers rely on synchronous read-your-writes semantics or atomic
rollbacks. If no such coupling exists in the target repository, state that this failure lead
was not observed rather than fabricating a risk.

## Safe control

Retaining a modular monolith with strict package boundaries may satisfy current throughput
and team autonomy without introducing network partitions.

Evaluate the lowest-complexity safe baseline alongside distributed or multi-store proposals
before recommending structural splits.

## Independent validation

Cross-check every cited constraint against dependency manifests, configuration files, and
schema definitions, then validate `architecture.json` against `assets/architecture.schema.json`.
Record any unmeasured load or latency assumptions explicitly in `consequences`.

## Cross-topic boundary

Keep `decisions` complete in `architecture.json`, and hand off detailed trust/module barrier
cataloging to `arch-boundary-specification`, module partitioning to `arch-component-decomposition`,
or migration phasing to `arch-data-evolution` without duplicating records.

# Investigation method: Component Decomposition

## Scope selection

Start from the highest-churn or most entangled subsystem, trace its read/write ownership and
collaborators end to end, and expand outward along call and import edges. Do not partition
components solely by directory names without checking runtime state sharing.

## Contract worksheet

For each proposed component, record its `name`, single-sentence `responsibility`, authoritative
`owned_models`, outbound `dependencies`, exposed `interfaces`, and temporal ordering assumptions.
Flag any model mutated by more than one component.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each candidate module seam. Record both
clean acyclic interfaces and unresolved shared-state knots.

## Adversarial case

Splitting an order module into billing and fulfillment components leaves both mutating a shared
`orders` record in arbitrary call order.

Inspect write call sites across candidate seams. If a single aggregate owner already mediates
all state transitions, record that intact ownership instead of inventing split-brain risk.

## Safe control

Keeping tightly coupled co-mutating state inside one component behind two narrow facade
interfaces avoids distributed or cross-module lock coordination.

Prefer narrow role interfaces over premature component splits when invariants require atomic
in-memory or single-transaction updates.

## Independent validation

Verify that component `dependencies` form a DAG and every `interfaces` producer/consumer pair
maps to concrete `components`, then validate `architecture.json` against
`assets/architecture.schema.json`.

## Cross-topic boundary

Populate `components` and `interfaces` in `architecture.json`, and hand off physical table
splitting to `arch-data-evolution`, external wire compatibility to `arch-integration-contracts`,
or ADR trade-offs to `arch-decision-record`.

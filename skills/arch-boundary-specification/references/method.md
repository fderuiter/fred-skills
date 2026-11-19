# Investigation method: Boundary Specification

## Scope selection

Trace one end-to-end request, job, or CLI command across every privilege, module, service,
and storage boundary it crosses, then expand to sibling entry points sharing the same state.
Do not mark a boundary enforced after checking only HTTP routes while ignoring workers or scripts.

## Contract worksheet

For each boundary (`trust`, `module`, `service`, `storage`), record its name, crossing actors,
untrusted inputs, enforcing authority (`path:line`), established invariants, leaked internal
types, and test coverage. Keep unverified deployment assumptions explicit.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every discovered perimeter. Record both
enforced crossings and alternate paths that bypass validation or encapsulation.

## Adversarial case

Background workers or internal admin CLI commands invoke domain mutators directly, bypassing
API-gateway tenant isolation and schema validation.

Search for non-HTTP callers of internal services and repositories. If all alternate entry points
pass through the same enforcing constructor or command handler, record that safe enforcement
rather than reporting a speculative bypass.

## Safe control

Internal calls behind an already-authenticated command bus may legitimately omit outer token
parsing when the bus enforces principal context invariants.

Distinguish deliberate single-point boundary validation (parse, don't re-validate) from
missing boundary enforcement.

## Independent validation

Verify package import direction and boundary crossings against actual symbol imports and
call graphs, and validate `architecture.json` against `assets/architecture.schema.json`.
Note any runtime-only reflection or DI wiring that static import scans cannot resolve.

## Cross-topic boundary

Record `boundaries` completely in `architecture.json`, then hand off internal module splitting
to `arch-component-decomposition`, wire-level schema contracts to `arch-integration-contracts`,
or ADR trade-off capture to `arch-decision-record`.

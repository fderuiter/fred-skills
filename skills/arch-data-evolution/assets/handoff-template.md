# Architecture Handoff

- Run ID, schema version, repository snapshot/revision, and read-only vs authorized mutation state.
- Architecture scope inspected (`boundaries`, `components`, `interfaces`, `decisions`) and skipped subsystems.
- Artifact paths updated in the run workspace (`architecture.json`, topic report) and owned ADR/boundary/component IDs.
- Adversarial scenarios stress-tested (rollout skew, boundary bypass, partial failure) and verified safe controls.
- Sibling specialist handoffs (`arch-decision-record`, `arch-boundary-specification`, `arch-component-decomposition`, `arch-data-evolution`, `arch-integration-contracts`, `arch-operational-resilience`).
- Open trade-offs, preconditions, and explicit user authorization needed before modifying repository source or schemas.

Revalidate `architecture.json` against `assets/architecture.schema.json` whenever workspace records change.

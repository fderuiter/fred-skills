# Software Architecture Engineering Protocol

Standard protocol for autonomous software architecture design, boundary specification, component decomposition, schema evolution, integration contracts, operational resilience, and architectural decision recording.

## Scope and Mutation Guardrails

- **Read-only workspace default**: Architecture specialists inspect the target repository and write deliverables (`architecture.json`, topic reports, and handoffs) inside the run workspace only.
- **Explicit permission required**: Do not modify application source code, migration scripts, deployment manifests, or build files outside the run workspace without explicit user permission.
- **Standalone portability**: Keep references self-contained within the skill package (`../assets/architecture.schema.json`, `../assets/architecture-template.md`, `../assets/handoff-template.md`).

## Core Tenets

1. **Explicit Boundaries**: Separate `trust`, `module`, `service`, and `storage` boundaries with explicit enforcing points and invariants.
2. **Recorded Decisions**: Document architectural trade-offs, context, rejected alternatives, and operational consequences in structured Architectural Decision Records (`decisions`).
3. **Cohesive Components & Contracts**: Partition responsibilities into explicit `components` (`name`, `responsibility`, `owned_models`, `dependencies`) and `interfaces` (`name`, `protocol`, `producer`, `consumers`, `contract`, `compatibility`).
4. **Evidence-Based Evaluation**: Ground every boundary, component seam, migration step, and failure-containment rule in concrete `path:line` evidence or explicit design constraints—never invent nonexistent infrastructure.

## Lifecycle and Validation

1. **Discovery & Invariant Analysis**: Inspect existing entry points, storage schemas, wire protocols, dependency edges, and deployment topology.
2. **Boundary, Component & Interface Modeling**: Populate `boundaries`, `components`, and `interfaces` in `architecture.json` so responsibilities do not overlap and cyclic dependencies are prevented.
3. **Adversarial Stress & Safe Control**: Test each architectural claim against an adversarial scenario (mixed-version rollout, partial network partition, unauthorized caller, schema skew) and verify a safe control path that preserves current callers.
4. **Decision Formulation & Schema Validation**: Record decisions in `architecture.json`, validate strictly against `assets/architecture.schema.json`, format narrative summaries with `assets/architecture-template.md`, and emit a specialist handoff via `assets/handoff-template.md`.

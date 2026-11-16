# Software Architecture Engineering Protocol

Standard protocol for autonomous software architecture design, boundary specification, and architectural decision recording.

## Core Tenets

1. **Explicit Boundaries**: Separate trust, capability, deployment, and data boundaries with clear ownership and access controls.
2. **Recorded Decisions**: Document architectural trade-offs, rationale, alternatives considered, and consequences in structured Architectural Decision Records (ADRs).
3. **Decoupled Decomposition**: Ensure modular decomposition minimizes cross-boundary coupling and enforces clear interface contracts.
4. **Evidence-Based Evaluation**: Base architectural assumptions on actual codebase inspection, schema definitions, and validated requirements.

## Lifecycle

1. **Discovery & Invariant Analysis**: Inspect existing interfaces, storage models, and concurrency boundaries.
2. **Boundary Modeling**: Map trust boundaries, public APIs, internal models, and cross-cutting concerns.
3. **Decision Formulation**: Formulate decisions using the standard ADR format with clear status, context, and consequences.
4. **Handoff & Artifact Generation**: Produce machine-readable architectural manifests adhering to `architecture.schema.json`.

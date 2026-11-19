---
name: arch-boundary-specification
description: "Specify and verify trust, module, service, and storage boundaries with explicit invariants and enforcement seams. Use when defining privilege barriers, encapsulating domain storage, establishing service perimeters, or auditing boundary crossings."
---

# Boundary Specification

Investigate architectural perimeters deeply and specify enforceable boundary invariants.
Use the supplied repository snapshot; do not assume network isolation or validation gates without inspecting code.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Honor its
   read-only workspace default, boundary taxonomy, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect entry points, middleware,
   package visibility, and persistence adapters before recording boundary guarantees.
3. Reuse the workspace `architecture.json` if present so existing `decisions`, `components`,
   and `interfaces` remain coherent. Sibling specialists are optional extensions, not automatic prerequisites.

## Inventory

Ingress controllers, RPC/HTTP handlers, CLI entry points, authentication/authorization middleware, package exports, database adapters, and external client gateways.

## Investigation

1. Classify each perimeter accurately as `trust`, `module`, `service`, or `storage` based on failure blast radius and privilege transition.
2. Pinpoint the exact enforcing mechanism at each crossing—schema decoder, auth guard, visibility rule, or transaction gateway—with concrete `path:line` evidence.
3. State explicit `invariants` that hold inside the boundary so downstream components do not redundantly re-parse or silently trust unvalidated inputs.
4. Detect leaky abstractions where internal ORM entities, raw credentials, or private mutable structures cross boundary seams.
5. Propose minimal enforcement seams that seal bypassed crossings without redesigning unrelated subsystems.

## Challenge and validate

Use this failure shape only as a discovery lead: Background workers or internal admin CLI commands invoke domain mutators directly, bypassing API-gateway tenant isolation and schema validation.

Actively test this counterexample: Internal calls behind an already-authenticated command bus may legitimately omit outer token parsing when the bus enforces principal context invariants.

Verify boundary rules against both untrusted ingress paths and internal administrative or batch entry points; validate `architecture.json` structure separately.

Check existing linter or package-visibility tooling before recommending enforcement checks. Read
[research references](references/sources.md) only when selecting boundary modeling conventions.
No external tool is required.

## Deliver and hand off

Format narrative documentation with [architecture template](assets/architecture-template.md).
Populate `boundaries` and preserve sibling arrays in `architecture.json` conforming to
[architecture schema](assets/architecture.schema.json). Schema conformance does not prove runtime enforcement.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `arch-component-decomposition`, `arch-integration-contracts`,
`arch-data-evolution`, or `arch-decision-record` when deeper decomposition or protocol trade-offs arise.
Do not automatically load sibling skills or modify application source code outside the run workspace without explicit user permission.

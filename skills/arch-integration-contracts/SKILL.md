---
name: arch-integration-contracts
description: "Specify synchronous RPC/HTTP APIs, asynchronous event schemas, idempotency semantics, and version compatibility across producer/consumer seams. Use when designing external integrations, webhook/event contracts, protocol versioning, or cross-service interfaces."
---

# Integration Contracts and Protocol Evolution

Design explicit producer/consumer `interfaces`, error semantics, and compatibility rules across service and external boundaries.
Use the supplied repository snapshot; do not assume exactly-once transport or synchronized client upgrades.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Follow its
   read-only workspace default, interface specification schema, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect OpenAPI/Protobuf/GraphQL schemas,
   webhook handlers, message publishers/consumers, and retry clients before defining contracts.
3. Reuse the workspace `architecture.json` if present so existing `boundaries`, `components`,
   and `decisions` stay aligned. Sibling skills are optional specialist continuations.

## Inventory

API route definitions, IDL/schema files, serialization codecs, event/queue publishers and subscribers, SDK clients, webhook verifiers, and error-mapping layers.

## Investigation

1. Catalog each cross-boundary surface in `interfaces` with explicit `name`, `protocol`, `producer`, `consumers`, `contract`, and `compatibility` rules.
2. Specify request/event preconditions, idempotency keys, ordering guarantees, pagination cursors, and deterministic error taxonomy (retryable vs terminal).
3. Define backward- and forward-compatibility rules for unknown fields, enum extensions, optional defaults, and deprecation horizons.
4. Separate external wire DTOs from internal domain `owned_models` so storage refactors never leak directly onto public payloads.
5. Capture protocol selection trade-offs (sync RPC vs async durable events, schema registry vs versioned endpoints) as linked `decisions`.

## Challenge and validate

Use this failure shape only as a discovery lead: An event producer adds a new enum variant and retries at-least-once delivery, causing older consumers to crash-loop and duplicate non-idempotent side effects.

Actively test this counterexample: Strictly versioned internal build-time codegen within a single atomic monorepo artifact may reject unknown fields safely without public deprecation shims.

Check producer/consumer skew across retry, duplicate delivery, out-of-order arrival, and partial timeout paths; validate `architecture.json` against the schema.

Inspect existing schema/IDL tooling in the repository before proposing new contract formats. Read
[research references](references/sources.md) only when checking wire compatibility or idempotency specifications.
No external tool is required.

## Deliver and hand off

Document integration specifications using [architecture template](assets/architecture-template.md).
Populate `interfaces`, `boundaries`, and `decisions` in `architecture.json` validated against
[architecture schema](assets/architecture.schema.json). Schema validity confirms structure, not wire interoperability.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer deeper handoffs to `arch-boundary-specification`, `arch-operational-resilience`,
`arch-data-evolution`, or `arch-decision-record` when timeout budgets, outbox storage, or auth perimeters require dedicated design.
Do not automatically load sibling skills or modify repository source/schema files outside the run workspace without explicit user permission.

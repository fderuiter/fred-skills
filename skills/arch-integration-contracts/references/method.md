# Investigation method: Integration Contracts and Protocol Evolution

## Scope selection

Trace one external or cross-service interaction from producer serialization through transport,
consumer decoding, error translation, and retry handling before expanding to sibling endpoints
or topics. Do not infer wire compatibility from server handler signatures alone without checking
serializers and client callers.

## Contract worksheet

For each `interfaces` record, document `protocol`, `producer`, `consumers`, payload schema source,
idempotency mechanism, delivery/ordering semantics, retryable vs terminal error codes, and
`compatibility` policy (backward, forward, full, or lockstep).

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each synchronous endpoint, webhook, and
message topic. Record both explicit compatibility guarantees and implicit coupling to internal models.

## Adversarial case

An event producer adds a new enum variant and retries at-least-once delivery, causing older
consumers to crash-loop and duplicate non-idempotent side effects.

Trace decoder behavior on unknown variants/fields and check consumer deduplication stores. If
all consumers live inside an atomically deployed binary with no persisted queue backlog, note
that safe scope explicitly.

## Safe control

Strictly versioned internal build-time codegen within a single atomic monorepo artifact may
reject unknown fields safely without public deprecation shims.

Avoid imposing external API deprecation overhead onto private in-process interfaces that ship
together atomically.

## Independent validation

Cross-check declared OpenAPI/Protobuf/JSON schemas against actual serializer flags and handler
status codes, and validate `architecture.json` against `assets/architecture.schema.json`.

## Cross-topic boundary

Maintain complete `interfaces` contracts in `architecture.json`, and hand off retry storms,
circuit breakers, and timeout budgets to `arch-operational-resilience` or outbox persistence
design to `arch-data-evolution`.

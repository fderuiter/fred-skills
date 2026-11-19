---
name: test-contract-harnesses
description: "Specify producer-consumer contract test harnesses, wire serialization round-trips, and fake/stub parity checks across HTTP, RPC, CLI, and event schemas. Use when testing API schemas, preventing mock drift from real providers, or verifying cross-version wire fixtures."
---

# Producer-Consumer Contract Harnesses

Design hermetic contract harnesses that bind serialized producer outputs and consumer decoders to the same canonical wire specification.
Keep the target repository read-only by default; do not write or modify repository test files outside the run workspace without explicit user permission.

## Start

1. Read [testing protocol](references/testing-protocol.md) once per run. Enforce its
   workspace-only specification default, independent-oracle rule, and explicit write-permission gate.
2. Read [investigation method](references/method.md). Inspect OpenAPI/JSON Schema/Protobuf definitions,
   client stubs, and server serializers before specifying contract test cases.
3. Reuse the workspace `testing.json` if present and check `run.json` `authority.application_changes`
   before writing any files outside the run workspace.

## Inventory

JSON/OpenAPI/Protobuf schemas, HTTP/RPC server handlers, SDK/client parsers, hand-written test doubles/fakes, golden wire fixtures, and error-envelope codecs.

## Investigation

1. Identify every boundary where consumer tests rely on hand-crafted mocks or fixtures that can drift silently from the real producer's emitted payload.
2. Specify shared contract `test_cases` (`oracle_type: "output"` or `"boundary"`, with explicit `priority`) that validate real producer responses against the canonical schema and feed those exact payloads into consumer parsers.
3. Include compatibility matrix cases: unknown additive fields, optional/null fields, error-status envelopes, pagination cursors, and empty collections.
4. Design parity checks ensuring in-memory test fakes enforce the same preconditions, error codes, and idempotency rules as the real adapter.
5. Record harness specifications and `execution_summary` in `testing.json`; materialize repository harness files only when explicit user permission (`--allow-fixes`) is active.

## Challenge and validate

Use this failure shape only as a discovery lead: Consumer unit tests pass against a hand-written mock returning `{"userId": 123}` while the real producer serializer emits `{"user_id": "123"}`, causing immediate production deserialization failure.

Actively test this counterexample: In-process calls between strongly typed functions compiled together in a single artifact already have compiler-checked signature parity and do not need JSON wire-contract harnesses.

Verify both producer emission and consumer parsing against the same authoritative schema or golden fixture; validate `testing.json` against the schema.

Inspect existing schema validators (`jsonschema`, `openapi`, `protoc`) in the repository first. Read
[research references](references/sources.md) only when selecting consumer-driven contract or fake-parity patterns.
No external tool is required.

## Deliver and hand off

Format contract harness specifications using [testing template](assets/testing-template.md).
Populate `test_cases` and `execution_summary` in `testing.json` validated against
[testing schema](assets/testing.schema.json). Schema validity checks JSON format, not producer/consumer parity.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer targeted handoffs to `test-property-invariants`, `test-case-generation`,
`test-flakiness-isolation`, or `test-suite-verification` when randomized payload fuzzing or suite runs are needed.
Do not automatically load sibling skills, hit live external production endpoints, or write repository files outside the run workspace without explicit user permission.

# Investigation method: API and Wire Compatibility Review

## Scope selection

Identify every exported function/type, HTTP/RPC endpoint, event payload, CLI flag, and configuration
key touched by the diff, then trace both old-caller-to-new-code and new-caller-to-old-code
interactions. Do not treat internal unexported helpers as public API breaks.

## Contract worksheet

For each public or wire-visible surface, record declared stability policy, before/after input
requirements, before/after output/error guarantees, serializer unknown-field behavior, and
deprecation window.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every exported or serialized change.
Record both breaking contract regressions and safe additive extensions.

## Adversarial case

A handler promotes an optional request field to required and changes validation failure from
HTTP `400` to `422`, breaking existing clients that omit the field or branch on `400`.

Check request decoder defaults, route version prefixes, and client callers/tests. If the endpoint
is newly introduced on an unreleased v2 prefix with zero prior consumers, refute the breaking-change
claim.

## Safe control

Adding a new optional response field with a backward-compatible default or changing an unexported
internal helper updated across all local call sites breaks zero external contracts.

Confirm tolerant-reader decoding before approving additive response fields.

## Independent validation

Compare before/after signatures, serializer annotations, and status-code mappings at exact
`file:line` locations, and validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Own public symbol, CLI, wire-format, and error-code compatibility here; hand off auth/privilege
exposure on new endpoints to `review-security-boundaries` and final severity synthesis to
`review-actionable-feedback`.

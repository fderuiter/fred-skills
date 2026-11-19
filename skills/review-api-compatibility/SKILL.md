---
name: review-api-compatibility
description: "Review code changes for backward and forward API compatibility across exported symbols, wire schemas, CLI flags, status codes, and error contracts. Use when reviewing public signature edits, OpenAPI/Protobuf changes, SDK exports, or rolling-deploy wire changes."
---

# API and Wire Compatibility Review

Evaluate modified public symbols, wire payloads, CLI flags, and error contracts for breaking consumer impact across versions.
Use the supplied diff and snapshot; do not assume all external or rolling-deploy callers upgrade simultaneously.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Honor its read-only
   review default, `file:line` citation rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Distinguish public/exported or wire-visible
   interfaces from package-private helpers before classifying a change as breaking.
3. Reuse the workspace `review.json` if present so findings from sibling review specialists remain intact.
   Sibling skills are optional specialist passes.

## Inventory

Exported module symbols (`__all__`, `pub`, `export`), HTTP/RPC route schemas, request/response serializers, Protobuf/JSON tags, CLI flags/exit codes, and error status mappings.

## Investigation

1. Classify each touched interface by visibility and consumer upgrade cadence (external SDK/HTTP consumers, rolling-deploy peers, or private single-binary callers).
2. Check input contracts for newly required fields, narrowed parameter types, tightened validation on previously accepted values, reordered positional arguments, or removed CLI flags.
3. Check output and error contracts for deleted/renamed response keys, widened nullability, altered HTTP/gRPC status codes, or changed pagination/idempotency semantics.
4. Inspect wire serialization tags, enum variant numbering, and default-value handling under mixed-version `(old client -> new server)` and `(new client -> old server)` paths.
5. Record concrete compatibility `findings` in `review.json` with additive deprecation or dual-support remedies rather than blocking evolution outright.

## Challenge and validate

Use this failure shape only as a discovery lead: A handler promotes an optional request field to required and changes validation failure from HTTP `400` to `422`, breaking existing clients that omit the field or branch on `400`.

Actively test this counterexample: Adding a new optional response field with a backward-compatible default or changing an unexported internal helper updated across all local call sites breaks zero external contracts.

Verify visibility exports, serializer configuration (`ignore_unknown_fields`, `extra`), and versioned route prefixes before reporting compatibility breakage; validate `review.json` separately.

Inspect declared stability policies (`semver`, deprecation notices, API versions) in the repository first. Read
[research references](references/sources.md) only when checking wire or semantic-versioning rules.
No external tool is required.

## Deliver and hand off

Format compatibility review notes using [review template](assets/review-template.md).
Record validated `findings` in `review.json` conforming to [review schema](assets/review.schema.json).
Schema conformance checks payload shape, not wire compatibility.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed reviews.

Offer targeted handoffs to `review-diff-analysis`, `review-security-boundaries`,
`review-invariant-violations`, or `review-actionable-feedback` when internal invariants, auth exposure, or verdict synthesis are needed.
Do not automatically load sibling skills or modify repository files outside the run workspace without explicit user permission.

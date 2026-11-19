---
name: review-security-boundaries
description: "Review code changes for authentication or authorization bypasses, tenant isolation leaks, untrusted input sinks, secret exposure, and unsafe deserialization or path traversal. Use when diffs touch auth guards, permissions, parsers, file/shell/SQL sinks, or trust boundaries."
---

# Security and Trust-Boundary Review

Trace untrusted inputs and principal identity across modified diff hunks into privileged sinks, storage queries, and outbound calls.
Use the supplied diff and snapshot; do not report generic vulnerability checklists without demonstrating a reachable source-to-sink path.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Follow its read-only
   review default, `file:line` citation rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect upstream middleware, parameter
   binders, and object-level ownership checks before promoting a sink match to a security finding.
3. Reuse the workspace `review.json` if present so findings from sibling review passes stay intact.
   Sibling skills are optional specialist extensions.

## Inventory

New or modified routes/RPCs, authn/authz decorators and middleware, tenant-scoped queries, shell/subprocess/SQL/template/path/URL sinks, deserializers, and log/error serializers.

## Investigation

1. Verify that every new or refactored entry point enforces both authentication and object/tenant-level authorization against the authenticated principal rather than caller-supplied IDs.
2. Trace untrusted request parameters, headers, filenames, archive entries, and webhook payloads into SQL, command execution, filesystem paths, SSRF targets, and HTML/template sinks.
3. Check for unsafe deserialization (`yaml.load`, `pickle`, `eval`), unverified cryptographic signatures, weak randomness, or timing-unsafe token comparisons introduced by the diff.
4. Inspect structured logs, error responses, and diagnostic bundles to ensure credentials, tokens, PII, or signing keys are never echoed or persisted unredacted.
5. Record exploitable or missing-guard `findings` in `review.json` with concrete source-to-sink traces and minimal guard remediations.

## Challenge and validate

Use this failure shape only as a discovery lead: A new batch-export endpoint verifies a valid login token in middleware but queries records by request `org_id` without checking that the caller belongs to `org_id`.

Actively test this counterexample: Passing a constant compile-time SQL string or parameterized query placeholder with bound arguments is safe even when nearby string formatting logs non-sensitive request IDs.

Verify both attacker control over the input source and absence of neutralizing upstream guards before reporting a security finding; validate `review.json` against the schema.

Inspect existing security helpers (path canonicalizers, query builders, auth policies) in the repo first. Read
[research references](references/sources.md) only when checking sink-specific neutralization rules.
No external tool is required.

## Deliver and hand off

Format security review notes using [review template](assets/review-template.md).
Record validated `findings` in `review.json` conforming to [review schema](assets/review.schema.json).
Schema conformance checks JSON fields, not security completeness.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed reviews.

Offer targeted handoffs to `review-concurrency-hazards` (TOCTOU races), `review-api-compatibility`,
`review-invariant-violations`, or `review-actionable-feedback` when cross-cutting issues appear.
Do not automatically load sibling skills, run destructive exploit payloads, or modify repository files outside the run workspace without explicit user permission.

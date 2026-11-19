---
name: review-invariant-violations
description: "Verify that code changes preserve domain invariants, state-machine transitions, transactional rollback guarantees, and resource lifecycles. Use when reviewing state transitions, error-recovery paths, partial mutations, or invariant preservation in diffs."
---

# Behavioral and State Invariant Verification

Verify that modified execution paths preserve established domain, state-machine, transaction, and lifecycle invariants across normal and exceptional exits.
Use the supplied diff and repository snapshot; do not invent undocumented invariants unsupported by callers or contracts.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Follow its read-only
   review default, `file:line` citation rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Establish each invariant from constructors,
   validators, persistence constraints, or tests before asserting that a diff breaks it.
3. Reuse the workspace `review.json` if present so findings from sibling review passes are preserved.
   Sibling skills are optional specialist passes, not mandatory chains.

## Inventory

Domain state enums and transition tables, constructors/validators, transaction scopes, compensation/rollback handlers, `try/finally` or context-manager blocks, and loop/pagination invariants.

## Investigation

1. Derive concrete preconditions, postconditions, and representation invariants maintained around the modified module before the change.
2. Trace whether newly added branches, early returns, or exception paths leave objects, caches, or database rows in half-mutated states.
3. Check state-machine transitions for illegal shortcuts, skipped terminal-state guards, or non-idempotent replay corruption.
4. Verify that acquired files, sockets, cursors, temp directories, and transactions release deterministically on every error exit.
5. Emit reproducible failure sequences and minimal remediation guidance in `review.json` without demanding speculative redesigns.

## Challenge and validate

Use this failure shape only as a discovery lead: A diff updates an in-memory status cache before committing the database write, leaving stale `"paid"` cache state when the commit raises an integrity error.

Actively test this counterexample: Mutating a request-scoped local struct that is discarded immediately when an exception aborts the handler cannot leak corrupted state across requests.

Trace object lifetime and rollback scope end to end before reporting partial-mutation bugs; validate `review.json` against the schema.

Inspect existing transaction wrappers and context managers in the codebase first. Read
[research references](references/sources.md) only when checking state-machine or exception-safety formulations.
No external tool is required.

## Deliver and hand off

Format invariant review notes using [review template](assets/review-template.md).
Record validated `findings` in `review.json` conforming to [review schema](assets/review.schema.json).
Schema conformance checks structure, not invariant truth.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed reviews.

Offer targeted handoffs to `review-concurrency-hazards`, `review-api-compatibility`,
`review-security-boundaries`, or `review-actionable-feedback` when lock races, wire contracts, or verdict synthesis are needed.
Do not automatically load sibling skills or modify repository files outside the run workspace without explicit user permission.

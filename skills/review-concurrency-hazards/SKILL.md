---
name: review-concurrency-hazards
description: "Review code changes for data races, lock-order inversions, check-then-act TOCTOU windows, async await interleavings, and leaked background tasks. Use when diffs modify locks, shared caches, worker pools, async coroutines, or concurrent database updates."
---

# Concurrency and Async Hazard Review

Reconstruct multi-thread, multi-process, or async-event-loop schedules across modified diff hunks to expose concrete interleaving defects.
Use the supplied diff and runtime model; do not assume thread-pool execution for single-threaded event loops or vice versa.

## Start

1. Read [review protocol](references/review-protocol.md) once per run. Follow its read-only
   review default, `file:line` citation rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Identify the actual execution model
   (OS threads, multiprocessing, `asyncio`/event loop, or multi-replica DB workers) before claiming a race.
3. Reuse the workspace `review.json` if present so findings from sibling review specialists stay intact.
   Sibling skills remain optional extensions.

## Inventory

Mutexes/RW-locks, atomic primitives, shared module/singleton caches, `async`/`await` suspension points, thread/task spawn sites, cancellation scopes, and `SELECT ... FOR UPDATE` / CAS queries.

## Investigation

1. Map every shared mutable structure touched by the diff alongside the lock, actor, or transaction invariant meant to serialize access across all readers and writers.
2. Check lock discipline for lock-order inversions, blocking I/O or network `await` calls held inside critical sections, and forgotten unlocks on exception paths.
3. Trace `await` or callback suspension points where in-memory maps, connection states, or cached tokens can mutate mid-coroutine (check-then-await-then-act).
4. Inspect spawned background tasks, goroutines, or futures for unbounded fan-out, swallowed exceptions, missing cancellation propagation, and shutdown leaks.
5. Record concrete two-actor interleaving `findings` in `review.json` with exact `file:line` anchors and minimal atomic/lock fixes.

## Challenge and validate

Use this failure shape only as a discovery lead: An `async` cache refresh checks `if key not in cache:`, awaits an HTTP fetch without singleflight/lock coordination, and overwrites newer entries when two coroutines interleave.

Actively test this counterexample: Mutating a plain dictionary without a lock between two `await`-free synchronous statements on a single-threaded Python `asyncio` loop cannot interleave with another coroutine on that same loop.

Write out the concrete Actor A / Actor B step schedule before reporting any concurrency defect; validate `review.json` against the schema.

Check the language memory model and server worker configuration in the repository first. Read
[research references](references/sources.md) only when checking memory-ordering or structured-concurrency rules.
No external tool is required.

## Deliver and hand off

Format concurrency review notes using [review template](assets/review-template.md).
Record validated `findings` in `review.json` conforming to [review schema](assets/review.schema.json).
Schema conformance validates JSON fields, not schedule freedom.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed reviews.

Offer targeted handoffs to `review-invariant-violations`, `review-security-boundaries`,
`test-flakiness-isolation`, or `review-actionable-feedback` when single-thread rollback invariants or deterministic stress tests are needed.
Do not automatically load sibling skills or modify repository files outside the run workspace without explicit user permission.

# Investigation method: Concurrency and Async Hazard Review

## Scope selection

Identify every shared mutable variable, lock, channel, `await` suspension point, task spawn,
and concurrent database row update introduced or altered by the diff, then inspect ALL reader
and writer sites of that shared state across the codebase—not only the lines inside the diff.

## Contract worksheet

Record the concurrency model (threads, event loop, processes, replicas), guarded state,
authoritative synchronization primitive, lock acquisition order, suspension points inside
critical sections, and task cancellation owner.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each shared-state or async diff hunk.
Record both concrete race schedules and verified single-thread or lock-serialized controls.

## Adversarial case

An `async` cache refresh checks `if key not in cache:`, awaits an HTTP fetch without
singleflight/lock coordination, and overwrites newer entries when two coroutines interleave.

Construct the exact interleaved step sequence (`T1 check -> T1 await -> T2 write -> T1 stale overwrite`).
If per-key futures or locks coalesce concurrent refreshes, record that safe control.

## Safe control

Mutating a plain dictionary without a lock between two `await`-free synchronous statements on
a single-threaded Python `asyncio` loop cannot interleave with another coroutine on that same loop.

Never demand thread mutexes for purely synchronous turn-atomic operations on a single-threaded
event loop unless worker threads also touch that object.

## Independent validation

Verify every step of the proposed interleaving against actual `file:line` statements and runtime
worker configuration, and validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Own multi-actor interleavings, lock ordering, async suspension races, and task lifecycle leaks
in `review.json`; hand off sequential state-machine invariants to `review-invariant-violations`
and verdict synthesis to `review-actionable-feedback`.

# Repository prioritization profiles

Verify these assumptions against the current checkout; none is a fresh audit finding.

| Profile | Start with | Next | Evidence needed |
| --- | --- | --- | --- |
| QRCraftly/client optical transfer | Behavioral contracts, invariants, lifecycle, concurrency, temporal coupling | Cancellation, protocol/data integrity, replay, capacity, complexity | Current browser/native boundaries, frame format, decode completion and resource ownership |
| Cadence Clinical/modular backend | Behavioral contracts, invariants, trust, transactions | Replay, data provenance, module boundaries, migrations, audit events | Actual write authority, permissions, database isolation, outbox/inbox and version model |
| deruiter.dev/web portfolio | Trust/canonicalization triage, asynchronous lifecycles | Data flows, caching, accessibility state, architecture, test oracles | Current server/client surfaces, URL use, deployment config, actual dynamic entry points |

Apply stack idioms only after discovery. For React, inspect effect cleanup/dependencies
and production behavior separately from development replay. For Swift, verify current
actor/reentrancy and task semantics from version-matched official docs. For .NET, trace
tokens, DI lifetimes, EF transaction contexts, and queued work. For JS/TS, use controlled
promise scheduling and dependency analysis where available. For Java, ArchUnit may help;
do not prescribe a Java tool to another runtime. No profile warrants a critical severity
without a specific reachable consequence.

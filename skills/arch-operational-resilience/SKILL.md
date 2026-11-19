---
name: arch-operational-resilience
description: "Specify failure-domain isolation, timeout and retry budgets, backpressure bulkheads, graceful degradation, and startup/shutdown recovery invariants. Use when designing fault-tolerant services, preventing cascading outages, bounding queue overload, or planning operational recovery."
---

# Operational Resilience and Failure Containment

Specify concrete failure containment boundaries, overload controls, and recovery invariants grounded in actual runtime behavior.
Use the supplied repository snapshot; do not assume unbounded queues, infinite retries, or instantaneous dependency recovery.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Follow its
   read-only workspace default, boundary invariants, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect HTTP/RPC clients, connection pools,
   worker queues, health probes, and shutdown hooks before specifying resilience architecture.
3. Reuse the workspace `architecture.json` if present so existing `boundaries`, `components`,
   `interfaces`, and `decisions` remain intact. Sibling skills are optional specialist extensions.

## Inventory

Outbound network clients, retry/backoff policies, timeout hierarchies, thread/connection pools, bounded vs unbounded queues, readiness/liveness probes, and signal/shutdown handlers.

## Investigation

1. Map failure domains across `service` and `storage` boundaries, identifying single points of contention where a slow downstream dependency exhausts caller pools.
2. Specify end-to-end deadline budgets, jittered retry caps, and idempotency preconditions so retries never amplify an active overload.
3. Define bulkhead isolation, bounded queue capacities, and explicit load-shedding or fallback behavior on `components` and `interfaces`.
4. Verify startup readiness gating, in-flight request draining on termination, and crash-only restart recovery for durable work items.
5. Record operational trade-offs (fail-open vs fail-closed caches, stale reads vs hard unavailability) as explicit `decisions` in `architecture.json`.

## Challenge and validate

Use this failure shape only as a discovery lead: Every upstream tier retries 3 times without deadline propagation or jitter against a saturated database pool, multiplying traffic 27x during transient latency spikes.

Actively test this counterexample: Offline batch CLI pipelines running sequentially for a single local operator may fail fast without circuit breakers or multi-pool bulkheads.

Trace partial brownouts—slow responses, half-open TCP sockets, poisoned queue messages, and SIGTERM mid-batch—against actual client configuration; validate `architecture.json` separately.

Inspect the runtime's existing concurrency primitives and proxy timeouts before recommending new middleware. Read
[research references](references/sources.md) only when checking backoff, bulkhead, or load-shedding patterns.
No external tool is required.

## Deliver and hand off

Format resilience specifications using [architecture template](assets/architecture-template.md).
Update `boundaries`, `components`, `interfaces`, and `decisions` in `architecture.json` validated against
[architecture schema](assets/architecture.schema.json). Schema validity confirms shape, not overload survival.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer deeper handoffs to `arch-integration-contracts`, `arch-data-evolution`,
`arch-boundary-specification`, or `arch-decision-record` when wire retry contracts or durable recovery tables need refinement.
Do not automatically load sibling skills or modify repository source/configuration outside the run workspace without explicit user permission.

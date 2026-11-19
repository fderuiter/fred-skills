---
name: arch-component-decomposition
description: "Partition system capabilities into cohesive components, owned domain models, and narrow interfaces without cyclic coupling. Use when decomposing monoliths, untangling god modules, assigning model ownership, or designing module seams."
---

# Component Decomposition

Analyze module responsibilities and dependency graphs to design cohesive `components` and `interfaces`.
Use the supplied repository snapshot; do not propose arbitrary microservice splits unsupported by domain seams.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Follow its
   read-only workspace default, component/interface rules, and mutation guardrails.
2. Read [investigation method](references/method.md). Inspect package imports, shared domain
   types, and stateful collaborators before proposing new component boundaries.
3. Reuse the workspace `architecture.json` if present so existing `boundaries` and `decisions`
   remain intact. Sibling specialists are optional follow-ups, not mandatory chains.

## Inventory

Top-level packages, service classes, domain models/aggregates, repository/DAO layers, composition roots, and cross-package dependency edges.

## Investigation

1. Map cohesive business or technical capabilities and assign each `components` entry a single primary `responsibility` and explicit `owned_models`.
2. Trace `dependencies` between components to ensure acyclic direction from orchestration/adapters toward stable domain contracts.
3. Specify narrow `interfaces` (`name`, `protocol`, `producer`, `consumers`, `contract`, `compatibility`) wherever capabilities collaborate across seams.
4. Expose hidden temporal coupling, shared mutable singletons, or bidirectional callbacks that would defeat nominal package separation.
5. Sequence incremental seam extraction steps so callers remain functional at each step instead of requiring a flag-day rewrite.

## Challenge and validate

Use this failure shape only as a discovery lead: Splitting an order module into billing and fulfillment components leaves both mutating a shared `orders` record in arbitrary call order.

Actively test this counterexample: Keeping tightly coupled co-mutating state inside one component behind two narrow facade interfaces avoids distributed or cross-module lock coordination.

Validate that every `owned_models` item has a single authoritative mutating component and that `dependencies` form a directed acyclic graph; validate `architecture.json` against the schema.

Inspect existing package layout and import rules before proposing directory reorganizations. Read
[research references](references/sources.md) only when evaluating cohesion or seam-extraction tactics.
No external tool is required.

## Deliver and hand off

Document decomposition rationale using [architecture template](assets/architecture-template.md).
Populate `components` and `interfaces` in `architecture.json` validated against
[architecture schema](assets/architecture.schema.json). Schema validity confirms shape, not acyclic runtime behavior.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed sessions.

Offer deeper handoffs to `arch-boundary-specification`, `arch-data-evolution`,
`arch-integration-contracts`, or `arch-decision-record` when storage splits or wire protocols need dedicated design.
Do not automatically load sibling skills or modify application source code outside the run workspace without explicit user permission.

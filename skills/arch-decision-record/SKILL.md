---
name: arch-decision-record
description: "Formulate and evaluate architectural decision records grounded in repository constraints and operational trade-offs. Use when recording ADRs, comparing architectural alternatives, superseding obsolete design choices, or documenting binding system trade-offs."
---

# Architectural Decision Records

Investigate architectural decision drivers deeply and emit evidence-backed decision records.
Use the supplied repository snapshot; do not invent requirements or assume unverified infrastructure.

## Start

1. Read [architecture protocol](references/architecture-protocol.md) once per run. Follow its
   read-only workspace default, evidence rules, and application-code mutation guardrails.
2. Read [investigation method](references/method.md). Inspect existing ADRs, module seams,
   configuration constraints, and operational limits before drafting or superseding decisions.
3. Reuse the workspace `architecture.json` if present so existing `boundaries`, `components`,
   and `interfaces` stay intact. Related architecture skills are optional specialist extensions,
   not mandatory dependency chains.

## Inventory

Existing ADR directories, design docs, deployment manifests, dependency constraints, persistence engines, public contracts, and operational SLAs.

## Investigation

1. Extract concrete decision drivers from observed code structure, runtime constraints, and stated requirements rather than generic pattern catalogs.
2. Enumerate at least two viable architectural alternatives alongside the status quo and state why non-viable options fail hard constraints.
3. Trace positive, negative, and operational consequences across build complexity, migration effort, failure modes, and team ownership.
4. Link each `decisions` entry (`id`, `title`, `context`, `decision`, `consequences`) to affected `boundaries`, `components`, and `interfaces` in `architecture.json`.
5. Define explicit supersession criteria and measurable re-evaluation triggers when an accepted or draft decision should be revisited.

## Challenge and validate

Use this failure shape only as a discovery lead: An ADR mandates distributed event choreography without accounting for existing single-database transactional callers or idempotency cost.

Actively test this counterexample: Retaining a modular monolith with strict package boundaries may satisfy current throughput and team autonomy without introducing network partitions.

Cross-check every claimed constraint against actual repository configuration, dependency manifests, and schema definitions before finalizing `status`.

Check the repository's existing ADR conventions before proposing numbering. Read
[research references](references/sources.md) only when selecting an ADR structure or
trade-off evaluation method. No external tool is a prerequisite.

## Deliver and hand off

Produce the narrative specification using [architecture template](assets/architecture-template.md).
Record `decisions` and preserve sibling sections in `architecture.json` validated against
[architecture schema](assets/architecture.schema.json). The schema validates structure, not architectural soundness.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for standalone or resumed runs.

Offer deeper handoffs to `arch-boundary-specification`, `arch-component-decomposition`,
`arch-data-evolution`, `arch-integration-contracts`, or `arch-operational-resilience` when specialized modeling is warranted.
Do not automatically load sibling skills, publish external tickets, or modify application code outside the run workspace without explicit user permission.

---
name: arch-decision-record
description: "Author structured ADRs weighing trade-offs. Use when recording design choices. Do not use for audits."
---

# Architectural Decision Records

Read the [architecture protocol](references/architecture-protocol.md) before formulating decisions.
Ground decisions in observed codebase constraints and explicit requirements.

## Workflow

1. Identify significant architectural decisions involving technology selection, patterns, or boundaries.
2. Characterize the context, drivers, and technical constraints motivating the decision.
3. Evaluate viable alternatives and document their comparative trade-offs honestly.
4. Formalize the chosen approach, rationale, and consequences (positive, negative, and operational).
5. Document status transitions and record superseding decisions when context evolves.

## Deliver

Use the [architecture template](assets/architecture-template.md) to format records.
Verify conformance against the [architecture schema](assets/architecture.schema.json), [worked examples](references/examples.md), and `python3 <this-skill>/scripts/validate_architecture.py <artifact.json>`.
Report findings with concrete source locations and rationale.
Do not modify application code or publish external records without explicit user authorization; never claim evaluations or tests ran unless actually executed.

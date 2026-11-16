---
name: review-diff-analysis
description: "Use when analyzing pull request diffs, change impact surfaces, and affected interface contracts."
---

# Diff Analysis

Read the [review protocol](references/review-protocol.md) before analyzing change diffs.
Focus on change impact surfaces, API compatibility, and contract modifications.

## Workflow

1. Inspect pull request diffs to identify modified lines, files, and hunk contexts.
2. Determine whether exported APIs, data contracts, or serialized schemas are altered.
3. Map downstream callers and dependents affected by modified signatures or behaviors.
4. Check for unintended side effects, removed validation checks, or introduced dependencies.
5. Summarize change blast radius and identify areas needing targeted invariant verification.

## Deliver

Structure change findings with the [review template](assets/review-template.md).
Validate findings format against the [review schema](assets/review.schema.json).
Report change impact surfaces with precise file paths and line ranges.

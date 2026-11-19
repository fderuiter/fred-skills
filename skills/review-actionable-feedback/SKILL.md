---
name: review-actionable-feedback
description: "Turn PR review notes into ranked comments. Use when drafting PR feedback. Do not use for patches."
---

# Feedback Synthesis

Read the [review protocol](references/review-protocol.md) before synthesizing review comments.
Focus on actionable recommendations, clear severity grading, and practical remediation.

## Workflow

1. Consolidate individual review findings and deduplicate overlapping observations.
2. Assign accurate severity classifications (critical, high, medium, low, advisory).
3. Draft clear, polite, and constructive comments explaining the rationale for each finding.
4. Provide concrete code diffs or implementation patterns to assist remediation.
5. Summarize overall review verdict (approve, request changes, or comment) with next steps.

## Deliver

Format review feedback following the [review template](assets/review-template.md).
Ensure feedback payload conforms to [review schema](assets/review.schema.json), [worked examples](references/examples.md), and `python3 <this-skill>/scripts/validate_review.py <artifact.json>`.
Produce structured, developer-ready review output with actionable next actions.
Do not modify application code or publish review comments remotely without explicit user authorization.

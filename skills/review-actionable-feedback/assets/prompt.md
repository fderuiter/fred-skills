Use review-actionable-feedback to synthesize deduplicated code review findings for <repository> at <revision> across <directory> and <symbol>.
Consolidate overlapping diff and invariant observations linked to issue <issue-number>, assigning calibrated severity (critical, high, medium, low, advisory).
Draft constructive rationale paired with minimal, concrete remediation patterns and an explicit overall review verdict.
Write `review.json` adhering to the review template and `assets/review.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not approve breaking contract changes without remediation guardrails or modify code unauthorized.

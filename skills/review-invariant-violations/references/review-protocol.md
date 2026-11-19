# Code Review Protocol

Standard protocol for differential pull request analysis, API compatibility checks, security boundary inspection, concurrency hazard detection, invariant verification, and actionable feedback synthesis.

## Scope and Mutation Guardrails

- **Read-only review default**: Code review specialists inspect base/head diffs and repository context and emit `review.json`, topic reports, and handoffs inside the run workspace only.
- **Explicit permission required**: Never edit repository source files, tests, or configuration outside the run workspace without explicit user permission.
- **Evidence-first discipline**: Every finding in `review.json` (`critical`, `high`, `medium`, `low`, `advisory`) must cite a real repository `file` and 1-indexed `line` corroborated against caller/consumer behavior.

## Core Tenets

1. **Diff Context & Blast Radius**: Anchor findings in modified hunks, deleted safeguards, and affected upstream callers or downstream consumers rather than isolated style nitpicks.
2. **Contract & Invariant Verification**: Check backward/forward API compatibility, authentication/authorization gates, lock/async interleavings, and state-machine invariants across before/after paths.
3. **Adversarial Case & Safe Control**: Demonstrate a concrete failure input, schedule, or caller sequence for each defect, and check whether surrounding guards or tests already neutralize candidate leads before reporting.
4. **Structured & Actionable Feedback**: Provide bounded remediation guidance in `suggestion` without rewriting unrelated architecture or masking uncertainty.

## Review Stages

1. **Change & Contract Discovery**: Resolve diff hunks, map changed symbols and public contracts, and inventory affected trust, concurrency, and persistence boundaries.
2. **Specialist Verification**: Trace adversarial schedules, boundary crossings, compatibility skew, and invariant breaks while recording refuted candidates as safe controls.
3. **Synthesis & Handoff**: Validate `review.json` against `assets/review.schema.json`, format human summaries with `assets/review-template.md`, and emit continuation records via `assets/handoff-template.md`.

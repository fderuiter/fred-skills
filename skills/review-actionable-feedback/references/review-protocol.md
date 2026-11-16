# Code Review Protocol

Standard protocol for automated review of code changes, pull request diffs, and invariant adherence.

## Core Tenets

1. **Diff Context & Scope**: Ground every finding in specific modified lines, committed diff hunks, and explicit caller contexts.
2. **Invariant Verification**: Check for regression risks against core system invariants, locking protocols, and state transitions.
3. **Structured & Actionable Feedback**: Produce concrete, actionable recommendations with severity, file locations, and suggested remediations.
4. **Non-Disruptive Analysis**: Distinguish blocking defects, potential bugs, and non-blocking maintainability observations.

## Review Stages

1. **Change Discovery**: Parse diffs, identify touched components, and map public contract changes.
2. **Invariant Analysis**: Verify data validity, null-safety, race conditions, error propagation, and resource leaks.
3. **Synthesis & Reporting**: Generate structured findings matching `review.schema.json` and human-readable summaries.

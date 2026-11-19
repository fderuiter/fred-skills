# Code Review Handoff

- Run ID, schema version, base/head revisions, changed-file scope, and read-only review state.
- Specialist surface reviewed (diff blast radius, API compatibility, security boundaries, concurrency hazards, invariants, feedback synthesis).
- Artifact paths updated in the run workspace (`review.json`, topic report), verdict, and owned finding IDs.
- Concrete failure paths corroborated with `file:line` evidence versus candidate leads refuted by existing safe controls.
- Sibling specialist handoffs (`review-diff-analysis`, `review-api-compatibility`, `review-security-boundaries`, `review-concurrency-hazards`, `review-invariant-violations`, `review-actionable-feedback`).
- Unexecuted verification checks, follow-up test requests, and explicit user permission required before editing repository files.

Revalidate `review.json` against `assets/review.schema.json` whenever findings or verdicts change.

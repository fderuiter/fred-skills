# Automated Testing Handoff

- Run ID, schema version, target component/framework, snapshot state, and explicit write-authorization status (`allow_fixes` / user permission).
- Specialist surface covered (coverage gaps, test specifications, contract harnesses, property invariants, flakiness isolation, suite verification).
- Artifact paths updated in the run workspace (`testing.json`, topic report), owned `test_cases` IDs, and priority distribution.
- Independent oracle derivation, adversarial fault/mutation checks, and determinism/isolation controls verified.
- `execution_summary` state (`not_run`, `passed`, `failed`, `partial`), exact commands run, and any blocked or unexecuted checks.
- Sibling specialist handoffs (`test-gap-analysis`, `test-case-generation`, `test-contract-harnesses`, `test-property-invariants`, `test-flakiness-isolation`, `test-suite-verification`) and authorization required before writing repository test files.

Revalidate `testing.json` against `assets/testing.schema.json` whenever test cases or execution summaries change.

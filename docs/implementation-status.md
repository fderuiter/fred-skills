# Usability implementation status

This release develops the original 35-package audit suite into a local toolkit with
41 packages. Features below are implemented, while quality claims remain bounded by
actual validation. There is no claim of being the world's best skill repository.

| Improvement | Delivered | Remaining evidence or decision |
| --- | --- | --- |
| Task-oriented discovery | 13 recipes, 8 packs, metadata/symptoms, offline searchable explorer | Observe real user completion and improve confusing routes |
| Cross-host use | Explicit copy adapters for four hosts, manual standalone packages | Real host activation/precedence/behavior checks for each host/version |
| Managed installation | Dry runs, receipts/hashes, modified-content refusal, backup/rollback, prepared-install recovery | Power-loss recovery for remove/rollback remains manual; shared-target concurrency is unsupported |
| Bounded investigations | Plans, 3 budgets, 4 modes, explicit Git base, selected/deferred work | Budgets are investigation counts, not cost guarantees |
| Repository context | Read-only preflight, source identities, opt-in profiles/config | Project profiles are hypotheses; no current project audit is bundled |
| Continuity | Run manifests, checkpoints, stale-evidence/impact review, engineering-memory export | Semantic dependencies and completion require agent/reviewer judgment |
| Reviewable outcomes | Escaped offline HTML, local issue drafts, comparisons/fingerprints | Missing findings do not imply resolution; duplicate remote issue checks are external |
| Verification lifecycle | Six skills, independent regression guidance, separate remediation and proof records | Proof fields cannot independently establish truthful execution |
| Analyzer interoperability | SARIF observations with safe locations, no auto-confirmation | Additional scanner formats can be added when justified by actual users |
| Evaluations | 15 executable cases, paired configurations, trace/usage/timeout/failure records | Deterministic adapter is a harness check; broad multi-model quality remains unmeasured |
| Runnable onboarding | Exact synthetic original/corrected behavior, controls, reports/drafts/proof | Generalize only after real bounded application runs |
| Versioned distribution | Deterministic pack archives, manifest/checksums, CI validation | Owner license selection and tag/manual release approval; no release published by this change |
| Maintenance and trust | Canonical resources, generated discovery, Linux/Windows CI, browser checks, contributor/security/release guidance | Private-report channel and public license are owner decisions |

Prioritize future work using observed failures: time to a useful first result, incorrect
activation, missed known defects, unsupported confirmed claims, repeated findings, and
whether verified corrections preserve safe behavior. Collect feedback without adding
telemetry by default. Add focused fixtures and independent evaluations before expanding
hosts, scanners, or skill counts.

# Usability implementation status

This release develops the original 35-package audit suite into a local toolkit with
57 total packages across 6 suites, 13 packs, and 9 host layouts. Features below are
implemented, while quality claims remain bounded by actual validation.

| Improvement | Delivered | Remaining evidence or decision |
| --- | --- | --- |
| Task-oriented discovery | 18 recipes, 13 packs, metadata/symptoms, [hosted](https://fderuiter.github.io/fred-skills) and [offline](explorer.html) searchable explorer | Observe real user completion and improve confusing routes |
| Cross-host use | Explicit copy adapters for 9 host layouts, manual standalone packages | Real host activation/precedence/behavior checks for each host/version |
| Managed installation | Dry runs, receipts/hashes, modified-content refusal, backup/rollback, prepared-install recovery | Power-loss recovery for remove/rollback remains manual (see [operational runbooks](runbooks.md)); shared-target concurrency is unsupported |
| Bounded investigations | Plans, 3 budgets, 4 modes, explicit Git base, selected/deferred work | Budgets are investigation counts, not cost guarantees |
| Repository context | Read-only preflight, source identities, opt-in profiles/config | Project profiles are hypotheses; no current project audit is bundled |
| Continuity | Run manifests, single-task loop progression (`advance`), checkpoints, stale-evidence/impact review, multi-suite memory export | Semantic dependencies and completion require agent/reviewer judgment |
| Reviewable outcomes | Escaped offline HTML, local issue drafts, comparisons/fingerprints | Missing findings do not imply resolution; duplicate remote issue checks are external |
| Verification lifecycle | Eight skills (six remediation steps plus session hygiene), independent regression guidance, separate remediation and proof records | Proof fields cannot independently establish truthful execution |
| Analyzer interoperability | SARIF observations with safe locations, no auto-confirmation | Additional scanner formats can be added when justified by actual users |
| Evaluations | 171 executable cases across 57 skills and six suites, blind prompts, property-anchor and suite-validator grading, scoped `writable_paths` checks, `--max-tokens` caps, paired `skill_lift` and token summaries, trace/usage/timeout/failure records | Deterministic adapter is a harness check; broad multi-model quality remains unmeasured |
| Runnable onboarding | Exact synthetic original/corrected behavior, controls, reports/drafts/proof | Generalize only after real bounded application runs |
| Versioned distribution | Deterministic pack archives, manifest/checksums, CI validation, automated `main` GitHub Release publishing under MIT [LICENSE](../LICENSE) | Downstream consumers should verify SHA-256 checksums and SLSA provenance attestations |
| Maintenance and trust | Canonical resources, generated discovery, Linux/Windows CI, browser checks, MIT [LICENSE](../LICENSE), contributor/security/release guidance | Dedicated private-report channel remains an owner decision |

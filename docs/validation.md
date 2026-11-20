# Validation record and limits

## Deterministic repository checks

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md) to verify package metadata,
standalone references, schema validity, canonical-resource copies, generated discovery
indexes, and validator regression tests. The tests exercise meaningful rejection paths,
including dangling IDs, false confirmation, refuted findings assigned to defect issues,
dependency cycles, malformed packages, escaped references, and stale generated resources.

These checks run locally and in CI. Passing them means the package/artifact structure
meets the checked rules; it does not mean every semantic claim in an audit is correct.

## Sampled agent runs used during initial authoring

Initial authoring on 13 November 2026 used a synthetic two-file JavaScript repository
whose contract was latest-started operation publication and cancellation invalidation.
This was not an audit of any production repository. No application fix was made.

| Run | Executed work | Observed result and limitation |
| --- | --- | --- |
| No-suite baseline | Fresh-context code review, existing print-only diagnostic, reentrancy reproduction | Found missing publication fencing and synchronous abort-listener reentrancy; those discoveries cannot be attributed uniquely to the suite. |
| Concurrency specialist | Fresh context using the skill and 11 deterministic exact-source schedules | Reproduced both causes, retained four ordinary safe schedule observations, and recorded two refuted candidates with a schema-valid ledger. |
| Coordinator and selected specialists | Fresh context; 12 schedules per class | Reproduced the ordinary publication cause and identified conditional test-oracle hardening. It explicitly excluded synchronous reentrancy and did not discover that second cause. |
| Findings synthesis | Fresh context on source and audit artifacts; reran 11 observations | Kept the two independent causes as related drafts, excluded refuted candidates, and produced a validated acyclic plan. External duplicate checking was unavailable. |
| Refined cancellation specialist | Fresh context; 24 deterministic checks after adding an explicit synchronous callback probe | Recorded 15 passing controls and 9 violations across the two causes; preserved several valid callback/thenable behaviors and unchanged source hashes. |

The checks imported the exact synthetic implementation and used controlled completion
and native abort dispatch without timing sleeps. Harness exit 0 meant the expected
observations, including known failures, were reproduced; it did not mean the intentionally
unsafe fixture was correct. No production system or remote issue record was modified.

## Specified evaluations are not executed evaluations

Each of the 57 skills in the suite includes three scenario specifications in
`references/evaluation-scenarios.json`: relevant failure, safe control, and near-neighbor
sibling routing request (`sibling_skill`). These 171 specifications are not 171 executed evaluations. They are
authoring inputs for future raw fixtures and fresh-context agent runs; do not supply
their expected answers to the agent performing an investigation.

The sampled runs are a small workflow/evidence-discipline check, not a statistical
benchmark or a measured improvement over the baseline. Comprehensive behavior on real
repositories and other agent hosts remains unverified. Record future actual runs,
misses, false positives, and environment constraints when extending the suite.

## 0.2.0 usability and correction-workflow validation

The expanded deterministic suite exercises 57 total packages across 6 suites, generated
recipes, 13 packs and explorer, explicit Git change scope, incomplete inventories, stale evidence,
proof requirements, comparison collisions, safe SARIF imports, HTML escaping, managed
installation lifecycle/recovery, deterministic archives/tamper detection, and evaluation
failure/timeout/input-isolation paths. Exact test counts and CI results are recorded in
the PR because they change during review.

The runnable Node walkthrough executes the exact unsafe source and a separate corrected
variant, controlled overlap/cancellation, and synchronous abort reentrancy. It produces a
schema-valid audit, report, local issue draft, and separate proof/remediation record. This
is a synthetic demonstration, not an agent audit or correction of a user's application.

The reference evaluation adapter executed 171 raw cases across all 57 skills and all six
suites in baseline and with-skill configurations (342 runs), validating audit,
architecture, code-review, automated-testing, spec-driven-development, and repository-workflow contracts,
property-anchor matching, suite semantic validators, scoped `writable_paths` source edits, token caps and summaries, and paired `skill_lift` deltas.
Its deterministic contract matching passed in both configurations; this verifies harness
plumbing and is not evidence of a model-quality improvement.

Two fresh-context agent tasks on 13 November 2026 used only selected skill instructions
and one raw source fixture each, without expected keys or other cases:

| Workflow | Observed result | Limit |
| --- | --- | --- |
| Verify finding and specify regression | Executed unchanged SQLite transfer; interruption and a real failed credit left total 115 rather than 140. Caller transaction could not undo the internal commit. Independent suite had 3 intended failures and 1 passing control. | Only the supplied SQLite mechanism was established; production caller/backend restrictions remained unknown. |
| Full six-step correction workflow | Established latest-start/cancel publication contract, specified and reproduced 7 failing regressions while 6 controls passed, applied one generation guard, and passed the identical 13 checks afterward. Recorded before/after identity, diff, commands, and schema-valid proof. | Authorized synthetic fixture only; does not establish general fix correctness or host/model superiority. |

The local optional Chromium check could not launch in the managed environment because
Unix socket creation was denied. No browser assertions passed locally and no restriction
was bypassed. The PR's dedicated CI job runs the actual report/explorer search, filter,
escaping, and no-external-request assertions. CI status is separate from this local record.
Copy-path tests cover 9 host layouts using temporary directories; none are host runtime
certification. Additional host versions, real repositories, all 171 scenario specifications,
and statistically meaningful model comparisons remain future evidence, not completed work.

# Existing approaches and adoption decisions

Research date: 2026-11-13. This is a representative capability comparison, not a benchmark,
exhaustive market census, or proof that no comparable suite exists. Inspect licenses and
versions before adopting third-party code. No external package is installed by this suite.

| Existing approach | Useful coverage | Boundary and suite decision |
| --- | --- | --- |
| Trail of Bits skills | Security audits, variant analysis, sharp edges, false-positive review, mutation workflows | Strong source of audit procedure patterns; complement with cross-topic contracts and common evidence IDs. Original instructions authored here; no third-party implementation copied. |
| CodeQL | Cross-function data flow and path-based evidence | Use when available and language models apply; custom business invariants need models/queries and human verification. |
| Semgrep CE / Semgrep Code | Pattern/taint rules; richer cross-function and cross-file analysis in Code | Verify engine/language/features; CE findings cannot be assumed to establish a cross-file path. |
| Infer RacerD | Supported-language thread-safety analysis | Not a generic detector for JS event-loop or Swift actor logical races; check language support and limitations. |
| fast-check | Generated values, command sequences, shrinking, asynchronous schedules | Good validation for invariants and races; keep the model independent and record seed/schedule. |
| Stately/XState | Explicit state models and generated paths | Useful when a state model fits; inspect installed version because test APIs differ. A reconstructed model must be checked against implementation. |
| ArchUnit / dependency-cruiser | Enforceable dependency and cycle rules | Use stack-appropriate tool and project-specific policy; dependency graphs do not establish every runtime ownership violation. |
| Pact | HTTP/message producer-consumer contracts | Useful for integration semantics; does not establish all domain, transaction, and lifecycle contracts. |
| Stryker | Mutation testing on supported stacks | Useful for oracle strength; surviving mutants require equivalence/reachability triage. |
| Playwright with accessibility checks | Stateful browser checks and automated accessibility scans | Add keyboard/focus/manual checks; scans alone do not verify all accessibility behavior. |
| Knip | Unused files, exports, dependencies in JS/TS | Validate entry points and dynamic consumers before proposing deletion. |
| OpenTelemetry | Correlated telemetry signals | Helps observe failures; instrumentation alone does not prove correctness or audit-log completeness. |
| Superpowers | Composable development skills | Useful workflow/evaluation ideas; avoid importing mandatory orchestration or approval assumptions into a read-only audit. |
| skills.sh | Skill discovery and distribution | Installation popularity is a discovery signal, not assurance of correctness or security. |

The suite's design goal is evidence reuse across behavioral discovery, specialized review,
and issue planning. No claim of measured superiority or market uniqueness is made.

# Source register

Reviewed 2026-11-13. Use these as methods, not evidence about an unaudited repository.
Check the actual installed language, engine, library version, and license before reuse.
These files contain original instructions; third-party implementations are not bundled.

- [Agent Skills specification](https://agentskills.io/specification): Portable SKILL.md format and optional resources.
- [Agent Skills authoring guidance](https://agentskills.io/skill-creation/best-practices): Coherent scope, progressive disclosure, calibrated procedures.
- [OpenAI Build skills](https://developers.openai.com/plugins/build/skills): Workflow boundaries, resources, MCP distinction.
- [OpenAI skill evaluations](https://developers.openai.com/blog/eval-skills): Outcome, process, efficiency checks and captured runs.
- [Anthropic skill authoring](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): Baselines, representative scenarios, iterative testing.
- [Gemini skill guidance](https://geminicli.com/docs/cli/skills-best-practices/): Runtime-specific authoring and activation guidance.
- [Trail of Bits skills](https://github.com/trailofbits/skills): Security audit, variant-analysis, false-positive, and sharp-edge workflows.
- [CodeQL data flow](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/): Path and value-flow evidence; analysis has modeling limitations.
- [Semgrep cross-file analysis](https://docs.semgrep.dev/semgrep-code/semgrep-pro-engine-intro): Analysis scope depends on engine and language; CE and Code differ.
- [Infer RacerD](https://fbinfer.com/docs/checker-racerd/): Thread-safety analysis with explicit language and modeling limitations.
- [fast-check model-based testing](https://fast-check.dev/docs/advanced/model-based-testing/): Command sequences, shrinking, and independent model design.
- [fast-check race conditions](https://fast-check.dev/docs/advanced/race-conditions/): Controlled asynchronous scheduling and replay.
- [Stately graph and paths](https://stately.ai/docs/graph): State traversal and model-based tests; select documentation for installed version.
- [ArchUnit guide](https://www.archunit.org/userguide/html/000_Index.html): Java architecture rules and cycle checks.
- [dependency-cruiser](https://github.com/sverweij/dependency-cruiser): JavaScript/TypeScript dependency rules and visualization.
- [Stryker mutation testing](https://stryker-mutator.io/docs/): JS/TS, .NET, and Scala mutation tooling; triage survivors.
- [Pact contract testing](https://docs.pact.io/): HTTP/message producer-consumer contract tests.
- [OWASP ASVS](https://asvs.owasp.org/): Versioned security verification requirements; not a blanket compliance certification.
- [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html): Isolation anomalies and consistency guarantees; select actual database version.
- [AWS safe retries](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/): Caller intent, request identity, atomic deduplication, uncertain outcomes.
- [React useEffect](https://react.dev/reference/react/useEffect): Setup/cleanup, dependencies, replay, and stale asynchronous work.
- [.NET cooperative cancellation](https://learn.microsoft.com/en-us/dotnet/standard/threading/cancellation-in-managed-threads): Cancellation roots, propagation, and cooperative observation.
- [Playwright accessibility testing](https://playwright.dev/docs/accessibility-testing): Automated checks plus manual accessibility verification.
- [OpenTelemetry signals](https://opentelemetry.io/docs/concepts/signals/): Logs, metrics, traces, and contextual telemetry.
- [Knip](https://knip.dev/): Unused JS/TS files, exports, dependencies, and configured entry points.
- [Superpowers](https://github.com/obra/superpowers): Composable development workflows and behavior-driven skill evaluation.
- [Skills directory](https://skills.sh/docs): Discovery and distribution; installation telemetry is not correctness evidence.

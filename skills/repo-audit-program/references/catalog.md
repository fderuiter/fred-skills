# Specialist catalog

Resolve by frontmatter name; never depend on installation directory IDs.
Load only selected skills. All use shared protocol/schema v1.

| Pass | Skill | Scope |
| --- | --- | --- |
| 1 | `repo-audit-behavioral-contracts` | Behavioral Contract Reconstruction |
| 1 | `repo-audit-invariants` | Implicit Invariants |
| 1 | `repo-audit-state-machines` | Implicit State Machines |
| 1 | `repo-audit-concurrency` | Concurrency and Race Conditions |
| 1 | `repo-audit-lifecycle` | Ownership and Lifecycle Boundaries |
| 1 | `repo-audit-temporal-coupling` | Temporal Coupling |
| 2 | `repo-audit-failure-modes` | Failure-Mode Completeness |
| 2 | `repo-audit-trust-boundaries` | Trust and Authorization Boundaries |
| 3 | `repo-audit-side-effects` | Hidden Side Effects |
| 3 | `repo-audit-sources-of-truth` | Duplicated Sources of Truth |
| 2 | `repo-audit-transactions` | Implicit Transaction Boundaries |
| 2 | `repo-audit-idempotency` | Idempotency and Replay Safety |
| 2 | `repo-audit-error-semantics` | Error Semantics and Taxonomy |
| 2 | `repo-audit-capacity` | Resource Bounds and Backpressure |
| 3 | `repo-audit-architecture` | Architectural Boundaries and Dependency Direction |
| 3 | `repo-audit-test-contracts` | Untested Behavioral Contracts |
| 2 | `repo-audit-cancellation` | Cancellation Propagation |
| 2 | `repo-audit-cache-coherence` | Cache Coherence |
| 2 | `repo-audit-data-integrity` | Data Transformation Integrity |
| 2 | `repo-audit-boundary-conditions` | Boundary-Condition Correctness |
| 2 | `repo-audit-protocol-drift` | Protocol Contract Drift |
| 2 | `repo-audit-configuration` | Configuration Combinatorics |
| 3 | `repo-audit-cohesion` | Cohesion and Responsibility |
| 3 | `repo-audit-observability` | Observability Gaps |
| 3 | `repo-audit-nondeterminism` | Nondeterministic Behavior |
| 3 | `repo-audit-test-oracles` | Test Oracle Quality |
| 2 | `repo-audit-compatibility` | Compatibility and Migration Contracts |
| 2 | `repo-audit-complexity` | Algorithmic Complexity |
| 1 | `repo-audit-domain-models` | Implicit Domain Models |
| 3 | `repo-audit-api-consistency` | API Design Consistency |
| 2 | `repo-audit-canonicalization` | Security-Sensitive Canonicalization |
| 3 | `repo-audit-accessibility-state` | Accessibility State Consistency |
| 3 | `repo-audit-dead-code` | Dead Code and Obsolete Compatibility Paths |

After any pass, use `repo-audit-synthesis` to consolidate root causes and issue drafts.

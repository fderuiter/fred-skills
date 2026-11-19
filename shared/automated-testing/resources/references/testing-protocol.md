# Automated Testing Protocol

Standard protocol for evidence-backed test gap analysis, deterministic test case generation,
and test suite oracle verification. Every skill in the `automated-testing` suite bundles an
identical copy of this reference so standalone skill packages operate offline.

## 1. Execution Authority and Test Isolation

1. **Inspect Existing Test Harness First**: Detect the repository's configured runner,
   assertion library, fixture conventions, and environment constraints (`pyproject.toml`,
   `package.json`, `Cargo.toml`, `go.mod`, `Makefile`) before proposing or running commands.
   Never introduce a new test framework or third-party mocking library without authorization.
2. **Application Code Boundary**: Test gap analysis and suite verification operate read-only
   against application code. Test generation writes or proposes test files only within the
   user-authorized scope and never alters production logic to force a failing test to pass.
3. **Separate Executed Proof from Static Specification**: Set `verified: true` on a test case
   only when the test was actually executed in the current environment and observed to pass
   against valid behavior (and fail against the targeted fault when verifying regression power).
   Otherwise set `verified: false` and record the exact runner limitation.

## 2. Oracle Classification and Gap Priority Rubric

### Oracle Taxonomy (`test_cases[*].oracle_type`)

- `state`: Verifies post-execution durable or in-memory state invariants (e.g., account
  balance conservation, state-machine status transition, idempotency ledger entry, released
  lock/handle count) after an operation completes.
- `output`: Verifies deterministic return values, serialized wire payloads, HTTP status/headers,
  or canonicalized command output against independently derived expected values.
- `exception`: Verifies explicit rejection of invalid, unauthorized, expired, or conflicting
  inputs via typed exceptions, error codes, or rollback guarantees—AND confirms no partial
  side effect persisted before failure.
- `boundary`: Verifies behavior at exact domain transitions: `0`, `1`, `max - 1`, `max`,
  `max + 1`, empty collections, Unicode multi-byte boundaries, leap/DST timestamps, and
  timeout/capacity thresholds.

### Risk-Driven Gap Prioritization

1. **Critical/High Priority Gaps**: Unexercised authorization denials, partial-failure
   rollback paths, retry/idempotency duplicate delivery, concurrent write interleavings, and
   corrupted/truncated input parsing.
2. **Medium Priority Gaps**: Off-by-one boundary transitions, pagination exhaustion, null vs.
   empty collection handling, and cancellation cleanup.
3. **Low/Skip Priority**: Trivial auto-generated getters, framework boilerplate already
   guaranteed by static types, or private formatting helpers already covered through public
   module contracts.

## 3. Counterexamples and Safe Controls

Challenge weak or deceptive test patterns before accepting coverage claims or emitting tests:

- **Counterexample — Circular/Tautological Oracle vs. Independent Golden Expectation**:
  Computing `expected = format_currency(raw_cents)` inside `test_format_currency` mirrors any
  rounding or sign bug in the implementation under test. *Safe control*: Assert against
  independently specified literal golden vectors (e.g., `assert format_currency(-105) == "-$1.05"`).
- **Counterexample — Mock-Call Verification vs. Boundary Contract Verification**:
  Mocking `db.execute` and asserting `mock_execute.assert_called_once()` passes even when the
  SQL query contains a syntax error or omits a `WHERE tenant_id = ?` clause. *Safe control*:
  Run against an in-memory/ephemeral local store or inspect the bound query parameters and
  resulting state invariant (`oracle_type: "state"`).
- **Counterexample — Wall-Clock Sleep Timing vs. Controllable Clock/Barrier**:
  Using `time.sleep(0.5)` to test TTL expiry or async ordering causes flaky CI failures under
  CPU contention. *Safe control*: Inject a fake/monotonic clock or explicit `Event`/`Deferred`
  latch so expiry and completion order are 100% deterministic.

## 4. High-Frequency Testing Edge Cases

1. **Swallowed Async Rejections and Missing Awaits**: Unawaited promises/coroutines or
   assertions placed inside uninvoked callbacks allow broken async code to exit `0` silently.
2. **Shared Mutable Fixture Pollution**: Module-scoped dictionaries, singleton caches,
   environment variables (`os.environ`), or timezone settings mutated in test A corrupt test B
   when execution order changes (`pytest-randomly`, parallel workers).
3. **Vacuous Loop Assertions**: Iterating `for item in result: assert item.valid` passes
   vacuously when `result` is unexpectedly empty `[]`; always assert non-emptiness or exact
   cardinality first.
4. **Over-Broad Exception Matching**: `pytest.raises(Exception)` or bare `expect(...).toThrow()`
   masks `TypeError` bugs in the test setup itself; match the specific domain error class and
   message/code invariant, plus post-exception state cleanliness.
5. **Surviving Equivalent Mutants**: When verifying suite sensitivity via targeted fault
   injection, check whether a surviving mutation is genuinely unreachable/equivalent (e.g.,
   logging text or idempotent clamp) before reporting an oracle defect.

## 5. Deliverable Contract and Deterministic Validation

1. Produce machine-readable test specifications conforming to `assets/testing.schema.json`
   paired with human-readable gap/verification summaries following `assets/testing-template.md`.
2. Ensure `component` identifies the exact module or repository-relative target path,
   `framework` names the repository's real test runner, and every item in `test_cases` carries
   a unique `id` (`TC-001`, `TC-002`, ...), descriptive `name`, concrete `description`,
   accurate `oracle_type`, and honest `verified` boolean.
3. Validate the JSON output locally before finishing:
   `python3 <this-skill>/scripts/validate_testing.py <testing.json>`
   This script enforces Draft 2020-12 schema rules, repository-relative component paths, and
   unique `test_cases` IDs.

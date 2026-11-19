# Investigation method: Security and Trust-Boundary Review

## Scope selection

Trace every new or modified ingress point, auth policy, and privileged sink touched by the diff
from untrusted caller input through middleware/validation to the sink execution point. Do not
flag keyword matches (`exec`, `sql`, `token`) without tracing data control.

## Contract worksheet

For each boundary crossing, record the untrusted source, principal identity authority, required
object/tenant authorization predicate, neutralizing sanitizer or parameterization (`file:line`),
and failure-mode behavior (fail-closed vs fail-open).

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to every modified endpoint, parser, query, file
operation, and outbound client. Record both confirmed bypasses and verified upstream guards.

## Adversarial case

A new batch-export endpoint verifies a valid login token in middleware but queries records by
request `org_id` without checking that the caller belongs to `org_id`.

Compare route middleware guarantees (authn only vs tenant authz) against repository query filters.
If a session-scoped database filter automatically binds `principal.org_id`, record that safe control.

## Safe control

Passing a constant compile-time SQL string or parameterized query placeholder with bound
arguments is safe even when nearby string formatting logs non-sensitive request IDs.

Verify sink binding mechanisms before reporting injection vulnerabilities.

## Independent validation

Trace complete source-to-sink call chains with exact `file:line` citations on head source, and
validate `review.json` against `assets/review.schema.json`.

## Cross-topic boundary

Own authentication, authorization, tenant isolation, secret leakage, and untrusted-sink safety
in `review.json`; hand off lock/TOCTOU race schedules to `review-concurrency-hazards` and verdict
deduplication to `review-actionable-feedback`.

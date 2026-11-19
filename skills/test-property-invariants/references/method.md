# Investigation method: Property-Based and Metamorphic Test Invariants

## Scope selection

Target codecs, parsers, path/text normalizers, redaction filters, financial/quota arithmetic,
and stateful reducers where single hand-picked examples miss multi-character or multi-step
interactions. Do not force randomized properties onto small finite enums where exhaustive
enumeration is simpler.

## Contract worksheet

For each property `test_cases` entry, record the universal predicate (round-trip, idempotence,
metamorphic relation, reference state machine), domain generator grammar, explicit seed and
iteration bound, `priority`, and `oracle_type`.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each candidate function or reducer.
Ensure generators construct valid domain structures directly rather than discarding almost all inputs.

## Adversarial case

A property test generates unconstrained random strings that fail precondition validation 99% of
the time (vacuously passing) or uses an unseeded RNG that flakes irreproducibly in CI.

Require structural generators, explicit precondition hit assertions, and logged deterministic seeds.

## Safe control

Fixed enum mappings with four exhaustive cases are verified more clearly and completely by an
explicit exhaustive table test than by randomized sampling.

Prefer exhaustive deterministic tables whenever the input domain cardinality is tiny.

## Independent validation

Check that every property predicate is derived from algebraic or reference-model laws rather
than production code, and validate `testing.json` against `assets/testing.schema.json`.

## Cross-topic boundary

Own algebraic, round-trip, metamorphic, and stateful-model test specifications in `testing.json`;
hand off single-defect regression pinning to `test-case-generation` and nondeterministic seed
cleanup in existing suites to `test-flakiness-isolation`.

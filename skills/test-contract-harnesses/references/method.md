# Investigation method: Producer-Consumer Contract Harnesses

## Scope selection

Trace one serialized boundary—HTTP endpoint, RPC method, webhook event, or CLI JSON output—from
the producer's serializer through schema validation into the consumer's parser and test doubles.
Do not design network-dependent live staging harnesses when hermetic schema/golden verification suffices.

## Contract worksheet

For each boundary, record canonical schema path, producer serializer (`path:line`), consumer
decoder (`path:line`), existing mock/fake definitions, drift vectors (field casing, nullability,
enum variants, error codes), `priority`, and `oracle_type`.

## Topic-specific discovery probes

Apply the five investigation steps in `SKILL.md` to each serialized boundary and test double.
Record both unverified mock-drift gaps and boundaries already locked by shared schema validators.

## Adversarial case

Consumer unit tests pass against a hand-written mock returning `{"userId": 123}` while the real
producer serializer emits `{"user_id": "123"}`, causing immediate production deserialization failure.

Check whether mock fixtures are validated against the producer's actual serializer or schema.

## Safe control

In-process calls between strongly typed functions compiled together in a single artifact already
have compiler-checked signature parity and do not need JSON wire-contract harnesses.

Reserve contract harnesses for serialization, IPC, CLI, storage, or mock/fake seams.

## Independent validation

Verify schema conformance of `testing.json` against `assets/testing.schema.json`, and when authorized
to execute existing contract checks, record exact commands and counts in `execution_summary`.

## Cross-topic boundary

Own producer/consumer wire conformance and fake-vs-real parity specifications in `testing.json`;
hand off randomized codec round-trips to `test-property-invariants` and suite execution telemetry
to `test-suite-verification`.

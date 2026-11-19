Use arch-integration-contracts to specify producer/consumer `interfaces` and protocol compatibility for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect API schemas, event codecs, idempotency keys, and retry/error semantics across service boundaries.
Distinguish public multi-version wire contracts from atomic internal interfaces before adding shims.
Record `interfaces`, `boundaries`, and `decisions` in `architecture.json` conforming to `assets/architecture.schema.json`.
Do not modify repository source or schema files outside the run workspace without explicit user permission.

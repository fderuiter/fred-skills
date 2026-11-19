Use arch-boundary-specification to specify and verify `trust`, `module`, `service`, and `storage` boundaries for <repository> at <revision>.

Prioritize <critical workflows> and reuse <existing audit workspace, if any>.
Inspect ingress paths, internal callers, and persistence gateways to verify where boundary invariants are actually enforced.
Check alternate entry points and safe controls before declaring a boundary intact or bypassed.
Record `boundaries` in `architecture.json` conforming to `assets/architecture.schema.json` and preserve existing `decisions`, `components`, and `interfaces`.
Do not modify repository source or configuration outside the run workspace without explicit user permission.

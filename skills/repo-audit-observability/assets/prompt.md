Use repo-audit-observability to audit diagnostic telemetry, correlation, and redaction in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory structured logs, distributed trace spans, metrics/counters, error boundaries, request/correlation IDs, PII/secret scrubbers, and operator runbooks.
Test whether an operator can determine which operation failed, at which boundary, with what durable side effects, across async queues, retries, and cancellations.
Flag silent failure black holes, dropped trace context, high-cardinality metric explosions, and secret leakage in telemetry with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

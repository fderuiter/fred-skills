Use repo-audit-protocol-drift to audit producer-consumer wire and schema drift in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory HTTP/gRPC/WebSocket endpoints, client SDKs, worker/queue payloads, file format codecs, generated bindings, content-type headers, and version negotiation.
Compare actual serializer emission against parser acceptance across producer-consumer pairs rather than trusting shared type aliases or stale OpenAPI/proto specs.
Expose mismatched field optionality, enum/union drift, unit/encoding mismatches, and framing bugs with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

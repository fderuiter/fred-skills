Use repo-audit-capacity to audit resource bounds, backpressure, and overload behavior in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory in-memory queues, buffers, caches, streaming parsers, decompression routines, worker pools, retry loops, request body limits, and fan-out broadcasts.
Map each resource's producer rate, admission bound, eviction or backpressure policy, release condition, and failure behavior under saturation.
Trace user- or network-controlled amplification, nested retry storms, and unbounded batch retention with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

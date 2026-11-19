Use repo-audit-canonicalization to audit input normalization and canonicalization ordering in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing issue <issue-number>.
Inventory URL and path parsers, hostname allowlists, Unicode case/NFKC normalizers, percent/HTML decoders, symlink/filesystem resolvers, and auth cache keys.
Trace raw input step-by-step through decoding, normalization, validation, authorization policy checks, and final sink consumption.
Flag time-of-check versus time-of-use representation drift, double decoding, path traversal, and homograph collisions with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

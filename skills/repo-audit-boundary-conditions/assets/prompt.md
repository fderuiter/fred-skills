Use repo-audit-boundary-conditions to audit numeric, collection, and format boundaries in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing context from issue <issue-number>.
Inventory indexing arithmetic, slice bounds, counts, pagination offsets, timestamps, byte/UTF-8 lengths, numeric casts, parsers, and protocol frame limits.
Verify inclusive versus exclusive endpoints across zero, one, limit-minus-one, exact limit, limit-plus-one, negative values, overflow, and NaN/infinity inputs.
Trace caller guards and downstream consumers to prove off-by-one, truncation, or wraparound defects with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

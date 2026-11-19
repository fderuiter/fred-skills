Use repo-audit-error-semantics to audit error classification, propagation, and recovery contracts in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing issue <issue-number>.
Inventory exception hierarchies, result unions, HTTP/RPC status mappings, retry classifiers, catch-all blocks, structured error logs, and user-facing messages.
Trace errors from origin through wrapping and translation to ensure validation, auth, conflict, cancellation, and transient dependency failures stay distinct.
Flag swallowed exceptions, misclassified retryable errors, leaked internal stack details, and lost causal chains with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

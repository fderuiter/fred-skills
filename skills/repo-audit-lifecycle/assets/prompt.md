Use repo-audit-lifecycle to audit resource ownership, acquisition, and disposal lifecycles in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while reviewing issue <issue-number>.
Inventory file descriptors, network sockets, DB connections/transactions, timers, event subscriptions, worker handles, temp directories, and cancellation scopes.
Build an ownership ledger mapping creator, borrower, transfer, close authority, and disposal ordering across normal completion, early return, and partial-init failure.
Pinpoint resource leaks, use-after-close hazards, double-close bugs, and unjoined background tasks with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

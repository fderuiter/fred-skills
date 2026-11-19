Use repo-audit-concurrency to audit concurrency, interleavings, and synchronization safety in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory await suspension points, callbacks, shared mutable state, locks, channels, actor mailboxes, worker threads, read-modify-write flows, and DB writers.
Distinguish thread data races, async event-loop interleavings, actor reentrancy, and distributed lost updates; construct numbered schedules showing invariant breakage.
Check lock ordering, check-then-act gaps across awaits, and non-atomic state transitions with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

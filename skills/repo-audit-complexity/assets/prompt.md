Use repo-audit-complexity to audit algorithmic and memory scaling complexity in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while correlating issue <issue-number>.
Inventory nested loops, linear rescans inside iterations, repeated sorts, backtracking regexes, graph traversals, deep clones, and N+1 query/RPC patterns.
Derive asymptotic time, allocation, and retained-memory growth in terms of independent attacker- or dataset-controlled dimensions rather than loop nesting alone.
Trace hidden quadratic work across callbacks, UI render cycles, and serialization passes with concrete file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

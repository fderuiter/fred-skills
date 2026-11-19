Use repo-audit-side-effects to audit hidden mutations and unexpected side effects in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory getters, memoized selectors, pure-named helpers, module import initializers, UI render hooks, comparators, logging formatters, and shared default objects.
Build a transitive effect map covering mutated arguments/globals, I/O, event emission, clock/random reads, and lazy cache population beneath ostensibly pure APIs.
Demonstrate order sensitivity, replay divergence, or caller-corrupting aliasing caused by hidden effects with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code or publish issues unless explicitly authorized.

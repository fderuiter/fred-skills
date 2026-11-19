Use repo-verify-fix on <repository/revision> with <finding/run artifacts> scoped to <directory>, <symbol>, and issue <issue-number>.
Tie the original claim and intended contract to exact before/after snapshots; a merged PR or closed issue alone is never proof of fix.
Execute the independent regression test, close safe controls, negative boundaries, and compatibility checks against the corrected source.
Write `verification.json` validated against `assets/verification.schema.json`, marking check flags true only when actually executed and passed.
Honor <allowed-paths> and <do-not-modify-paths>; do not modify application code, publish remote updates, or mark unexecuted checks verified.

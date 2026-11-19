Analyze the failed repository audit session transcript. Locate the root cause of the failure,
correlate it with the active audit specialist and assets/audit.schema.json ledger entries,
scrub any credentials, and output a recovery bundle conforming to assets/bundle.schema.json.
Do not modify canonical ledger files; enforce diagnostic boundary guardrails.

Transcript: <path-to-session-transcript>
Workspace: <path-to-run-workspace-directory>
Output: <path-to-diagnostic-bundle-output>

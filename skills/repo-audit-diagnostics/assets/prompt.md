# Diagnostic Session Triage Prompt

Use this prompt to diagnose a failed or stalled repository audit session.

```markdown
Analyze the failed repository audit session transcript. Locate the root cause of the failure,
correlate it with the active audit specialist and ledger entries, scrub any credentials,
and output actionable recovery steps.

Transcript: <path-to-session-transcript>
Workspace: <path-to-run-workspace-directory>
Output: <path-to-diagnostic-bundle-output>
```

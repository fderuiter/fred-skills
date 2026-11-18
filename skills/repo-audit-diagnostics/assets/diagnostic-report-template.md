# Diagnostic Triage Report

- **Session / Run ID:** {session_id}
- **Timestamp:** {timestamp}
- **Failure Classification:** {failure_type}
- **Root Cause Line:** {line_number}
- **Correlated Domain:** {specialist_skill}

## Summary of failure

{summary}

```
{matched_line}
```

## Audit domain and ledger correlation

- **Specialist Skill:** {specialist_skill}
- **Domain Topic:** {topic}
- **Correlated Ledger Entries:** {ledger_references}

## Transcript discovery

- **Source:** {discovery_source}
- **Selected Transcript:** `{selected_transcript}`
- **Selection Reason:** {selection_reason}
- **Rejected Candidates:**
{rejected_candidates}

## Actionable recovery instructions

{recovery_instructions}

## Sanitization status

- **Redaction Tier:** {redaction_tier}
- **Credentials Redacted:** {credentials_redacted}
- **Tool Outputs Omitted:** {tool_outputs_omitted}
- **Total Redactions:** {total_redactions}

All private credentials, tokens, authorization headers, URI passwords, and environment secrets
have been redacted from this bundle.

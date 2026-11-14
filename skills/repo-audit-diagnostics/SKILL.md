---
name: repo-audit-diagnostics
description: "Diagnose stalled repository audits, parse session transcripts for timeouts and schema errors, scrub credentials, and generate recovery bundles."
---

# Repository Audit Session Diagnostics

Triage interrupted, stalled, or failed audit sessions, locate root-cause errors,
redact sensitive credentials, and produce structured recovery instructions.

## Triage workflow

Read [diagnostics guide](references/diagnostics-guide.md) for failure patterns and scrubbing rules.
Consult [protocol](references/protocol.md) for multi-pass coordination invariants.

1. **Ingest transcript and run state:** Load the agent session transcript (`.json`, `.jsonl`,
   `.md`, or raw log text) alongside the audit workspace directory containing `run.json`
   and `audit.json` when available.
2. **Detect failure signatures:** Scan transcript execution history to identify:
   - Tool timeouts (long-running commands, process timeouts, tool execution deadlines).
   - Schema validation failures (structural mismatches against [schema](assets/audit.schema.json),
     invalid references, duplicate IDs, reversed line numbers, cycle errors).
   - Context window exhaustion (token limit exceptions, truncated model turns).
   Pinpoint the precise transcript line and failed command or tool invocation.
3. **Correlate specialist domain and ledger entries:** Identify the active specialist
   audit skill (e.g. `repo-audit-concurrency`, `repo-audit-trust-boundaries`) and correlate
   failure context with existing `audit.json` records (evidence, contracts, findings).
4. **Scrub private credentials:** Redact authorization headers, Bearer tokens, API keys
   (`sk-...`, `ghp_...`, AWS keys), passwords, private keys, and environment secrets
   from transcripts, logs, and diagnostic exports.
5. **Formulate recovery steps:** Inspect the last recorded checkpoint in `run.json`.
   Produce actionable resumption commands using `python scripts/fred.py resume` or
   bounded checkpoint repair commands.
6. **Export diagnostic bundle:** Generate a structured bundle validated against
   [bundle schema](assets/bundle.schema.json) and format a human-readable summary using
   [diagnostic report template](assets/diagnostic-report-template.md).

## Operational guardrails

- Never modify the canonical `audit.json` or `run.json` ledger files automatically;
  remedies must be operator-reviewed and executed via standard coordinator commands.
- Live process monitoring or process supervision is out of scope; diagnostics operates
  on completed or interrupted session transcripts and saved artifacts.
- All exported bundles must pass credential redaction before leaving the workspace.

## Deliverables

Run `python <this-skill>/scripts/triage.py <transcript> --workspace <run-dir> --output-dir <bundle-dir>`.
Use [prompt template](assets/prompt.md) to initialize operator diagnosis.
Provide the scrubbed diagnostic bundle, failure root-cause analysis, and exact next resumption commands.

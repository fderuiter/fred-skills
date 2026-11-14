# Diagnostic Triage and Scrubbing Guide

Guidelines for diagnosing stalled or failed multi-pass audit runs.

## Common failure modes

### 1. Tool timeouts
- **Indicators:** `TimeoutError`, `timed out after X seconds`, `Process timed out`,
  `Command '['...']' timed out`, `tool execution exceeded deadline`.
- **Root causes:** Deep directory tree scans without exclusions, hanging subprocesses,
  network calls in an isolated environment, unbounded test runner execution.
- **Triage action:** Pinpoint the tool name and command arguments; recommend narrow
  file path filters, timeout extensions, or omitting non-essential static analysis passes.

### 2. Schema validation mismatches
- **Indicators:** `jsonschema.exceptions.ValidationError`, `'schema_version' is a required property`,
  `Duplicate IDs in <field>`, `Reversed lines`, `Unknown <target> reference <id>`,
  `Actionable defect lacks contract/evidence`, `Issue dependency cycle`.
- **Root causes:** Incomplete ledger write by an interrupted specialist, malformed manual edit,
  inconsistent cross-record referencing between findings, contracts, and evidence.
- **Triage action:** Extract line number and invalid JSON path; output exact corrective diff
  to make ledger conform to `audit.schema.json` without destroying valid evidence.

### 3. Context window exhaustion
- **Indicators:** `context_length_exceeded`, `maximum context length is X tokens`,
  `prompt is too long`, `RateLimitError`.
- **Root causes:** Ingesting large minified vendor bundles, dumping full transcripts into
  single prompt, excessive findings in single pass without synthesis.
- **Triage action:** Recommend checkpointing current pass, scoping specialist to smaller
  workflow slice, and resuming with cleared conversational memory.

## Credential scrubbing specifications

All exports must redact the following patterns:
1. **Authorization headers:** `Authorization: Bearer <token>`, `Basic <base64>`
2. **Provider API keys:** `sk-[a-zA-Z0-9_\-]{20,}`, `xoxb-...`, Anthropic/OpenAI keys
3. **GitHub / Git tokens:** `ghp_[a-zA-Z0-9]{36}`, `gho_...`, `github_pat_...`
4. **Cloud credentials:** `AKIA[0-9A-Z]{16}`, AWS secret access keys
5. **Private keys:** `-----BEGIN [A-Z ]*PRIVATE KEY-----...-----END [A-Z ]*PRIVATE KEY-----`
6. **Key-value secrets:** `password: "..."`, `token = "..."`, `api_key = "..."`
7. **JSON Web Tokens:** `eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+`

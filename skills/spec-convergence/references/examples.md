# Specification Convergence Worked Examples

## Example 1: Detecting Scope Creep and Missing Requirement Coverage

Compare `git diff --name-only` and task breakdowns against `spec.json` boundaries before
declaring convergence:

```text
Authorized new_file_scopes:
  - src/exporter/ndjson.py
  - src/exporter/routes.py
  - tests/test_ndjson_exporter.py
Explicit out_of_scope:
  - src/exporter/legacy_csv.py

Observed modified paths in workspace:
  M src/exporter/ndjson.py
  M src/exporter/legacy_csv.py   <-- violates out_of_scope -> status: "out-of-scope"
  M tests/test_ndjson_exporter.py
```

Even when `python -m unittest tests/test_ndjson_exporter.py -v` exits `0`, modifying
`src/exporter/legacy_csv.py` violates `out_of_scope` (`CHK-03: "out-of-scope"`), requiring
top-level `status: "diverged"` rather than rewriting `SPEC.md` silently.

## Example 2: Verified Convergence Ledger (`spec.json`)

When every requirement maps to in-scope files, zero files escape `new_file_scopes`, and all
`verification_commands` exit `0`, set `status: "converged"` and validate locally with
`python3 <this-skill>/scripts/validate_spec.py spec.json`:

```json
{
  "schema_version": "1.0",
  "title": "Streaming NDJSON Event Exporter",
  "status": "converged",
  "summary": "All streaming NDJSON requirements, file-scope boundaries, and unit test verification commands aligned.",
  "interview_decisions": [
    {
      "id": "DEC-01",
      "category": "ux",
      "question": "How should mid-stream read errors be surfaced after HTTP 200 headers are flushed?",
      "decision": "Emit a final NDJSON error line with code stream_aborted and close the response.",
      "rationale": "Allows deterministic truncation detection by line-oriented clients."
    }
  ],
  "requirements": [
    {
      "id": "REQ-01",
      "description": "Stream events in <= 500-row KeysetCursor batches without full-dataset buffering.",
      "priority": "must"
    },
    {
      "id": "REQ-02",
      "description": "Emit terminal error JSON record on mid-stream storage failure.",
      "priority": "must"
    }
  ],
  "new_file_scopes": [
    "src/exporter/ndjson.py",
    "src/exporter/routes.py",
    "tests/test_ndjson_exporter.py"
  ],
  "out_of_scope": [
    "src/exporter/legacy_csv.py",
    "Cross-tenant S3 archival"
  ],
  "verification_commands": [
    "python -m unittest tests/test_ndjson_exporter.py -v"
  ],
  "convergence_checks": [
    {
      "id": "CHK-01",
      "target": "REQ-01",
      "status": "aligned",
      "evidence": "Implemented 500-row KeysetCursor generator in src/exporter/ndjson.py:L24-L61."
    },
    {
      "id": "CHK-02",
      "target": "REQ-02",
      "status": "aligned",
      "evidence": "Mid-stream storage exception handler writes stream_aborted trailer in src/exporter/ndjson.py:L63-L74."
    },
    {
      "id": "CHK-03",
      "target": "new_file_scopes",
      "status": "aligned",
      "evidence": "Changed files limited to src/exporter/ndjson.py, src/exporter/routes.py, and tests/test_ndjson_exporter.py; src/exporter/legacy_csv.py untouched."
    },
    {
      "id": "CHK-04",
      "target": "python -m unittest tests/test_ndjson_exporter.py -v",
      "status": "aligned",
      "evidence": "Executed locally; 6 tests passed with exit status 0."
    }
  ]
}
```

# Worked Examples: Repository Audit Session Diagnostics

Use these examples on demand when triaging interrupted transcripts, scrubbing credentials,
and validating diagnostic bundles against `assets/bundle.schema.json` and `assets/audit.schema.json`.

## 1. Raw Interrupted Session Transcript Input

An interrupted JSONL session log containing an embedded Bearer secret and a `validate_audit.py`
schema validation failure during `repo-audit-transactions`:

```jsonl
{"role":"assistant","content":"Active specialist: repo-audit-transactions. Inspecting src/billing/ledger.py."}
{"role":"tool_output","content":"Authorization: Bearer sk-live-9876543210abcdefSECRET\nLoaded 12 ledger routes."}
{"role":"error","content":"audit.json schema_validation_error: findings/0: Confirmed finding lacks executed command: F-003"}
```

## 2. Valid Scrubbed Diagnostic Bundle (`assets/bundle.schema.json`)

Running `python <this-skill>/scripts/triage.py session.jsonl --workspace /tmp/run-01 --output-dir /tmp/diag`
redacts the secret token, pinpoints line 3, correlates `repo-audit-transactions`, and emits a
diagnostic bundle matching `assets/bundle.schema.json`:

```json
{
  "schema_version": "1.0",
  "session_id": "sess-20261119-01",
  "timestamp": "2026-11-19T12:15:00Z",
  "failure_classification": {
    "failure_type": "schema_validation_error",
    "summary": "Confirmed finding F-003 in audit.json lacks an executed validation command.",
    "line_number": 3,
    "matched_line": "Confirmed finding lacks executed command: F-003"
  },
  "correlated_domain": {
    "specialist_skill": "repo-audit-transactions",
    "topic": "transactions",
    "ledger_references": [
      "F-003"
    ]
  },
  "environment": {
    "platform": "Linux-6.8-x86_64",
    "python_version": "3.12.15",
    "repository_path": "acme/billing-service",
    "run_id": "run-2026-11-19-01"
  },
  "recovery": {
    "last_checkpoint": "pass-2-transactions",
    "instructions": [
      "Downgrade F-003 status to 'supported' if no command ran, or record the executed validation command.",
      "Re-run python3 skills/repo-audit-diagnostics/scripts/validate_audit.py /tmp/run-01/audit.json.",
      "Resume coordinator from checkpoint pass-2-transactions."
    ]
  },
  "scrubbed_transcript": "[line 1] Active specialist: repo-audit-transactions.\n[line 2] Authorization: [REDACTED]\n[line 3] Confirmed finding lacks executed command: F-003",
  "redaction": {
    "tier": "evidence",
    "credentials_redacted": 1,
    "tool_outputs_omitted": 0,
    "total_redactions": 1,
    "by_category": {
      "bearer_token": 1
    }
  },
  "transcript_discovery": {
    "source": "explicit",
    "selected_transcript": "session.jsonl",
    "selection_reason": "Explicit transcript path supplied by operator.",
    "rejected_candidates": []
  }
}
```

## 3. Repaired `audit.json` Ledger Verified with `scripts/validate_audit.py`

After downgrading unexecuted `F-003` from `confirmed` to `supported`, the ledger passes
`assets/audit.schema.json` and `scripts/validate_audit.py`:

```json
{
  "schema_version": "1.0",
  "run_id": "run-2026-11-19-01",
  "repository": {
    "identity": "acme/billing-service",
    "revision": "4a19bc2",
    "dirty": false,
    "snapshot": "git-tree:4a19bc2"
  },
  "scope": [
    "src/billing/"
  ],
  "coverage": [],
  "contracts": [
    {
      "id": "C-003",
      "dimension": "transactions",
      "scope": "transfer_funds",
      "intended": "Debit and credit legs commit atomically.",
      "observed": "Credit leg runs in autocommit connection outside debit transaction.",
      "enforcement": "separate connection pool call",
      "evidence_ids": [
        "E-003"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-003",
      "path": "src/billing/ledger.py",
      "line_start": 72,
      "line_end": 89,
      "symbol": "transfer_funds",
      "context": "Opens second autocommit cursor before outer transaction commits."
    }
  ],
  "findings": [
    {
      "id": "F-003",
      "topic": "transactions",
      "tags": [
        "data-integrity"
      ],
      "title": "Split connection commits credit even when outer debit rolls back",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-003"
      ],
      "evidence_ids": [
        "E-003"
      ],
      "preconditions": "Outer transaction raises IntegrityError after inner credit commits.",
      "sequence": [
        "transfer_funds opens outer tx and stages debit.",
        "record_credit commits on separate autocommit connection.",
        "Outer tx rolls back on post-check failure."
      ],
      "expected": "Both debit and credit roll back together.",
      "actual": "Credit persists without matching debit.",
      "impact": "Unbacked balance creation on failed transfer.",
      "root_cause": "Helper acquires independent connection instead of caller session.",
      "counterevidence": "Happy-path transfers without post-check failure balance both legs.",
      "remedy": "Pass caller transaction session into record_credit and remove autocommit.",
      "acceptance": [
        "Forced post-check exception leaves zero net rows in both debit and credit tables."
      ],
      "validation": {
        "state": "blocked",
        "commands": [],
        "result": "Local Postgres service unavailable in sandbox; static trace verified."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [
    "Database integration harness not available during offline triage."
  ],
  "checks": []
}
```

# Implicit State Machines: Specialist Method and Examples

## Auditing Boolean Flag Clusters and Transition Tables

1. Enumerate coupled status enums and boolean fields (`isLoading`, `isComplete`, `error`, `retryCount`, `paid_at`, `refunded_at`) on stores, reducers, and persisted entities, then tabulate which combinations represent reachable domain states.
2. Trace non-happy-path transitions—retry after failure, re-submission after success, timeout after cancel, out-of-order webhook arrival—checking whether every transition resets mutually exclusive flags.
3. Confirm terminal states (`cancelled`, `settled`, `revoked`) reject backward transitions in storage/reducer guards; do not flag orthogonal UI toggles (such as `sidebarExpanded` alongside `darkTheme`) that represent genuinely independent dimensions.

## Failure Trace vs. Safe Control: Retry Leaving Stale Completion Flag Active

```typescript
// Defect: RETRY clears errorMessage but forgets completed = false, producing {loading: true, completed: true}
function uploadReducer(state: UploadState, event: UploadEvent): UploadState {
  switch (event.type) {
    case "SUCCEEDED":
      return { ...state, loading: false, completed: true, errorMessage: null };
    case "FAILED":
      return { ...state, loading: false, errorMessage: event.reason };
    case "RETRY":
      return { ...state, loading: true, errorMessage: null }; // completed stays true if triggered after prior upload!
  }
}

// Safe control: explicit discriminated union makes impossible flag combinations unrepresentable
type SafeUploadState =
  | { tag: "idle" }
  | { tag: "uploading"; attempt: number }
  | { tag: "succeeded"; artifactId: string }
  | { tag: "failed"; reason: string; attempt: number };
```

## State-Machine `audit.json` Deliverable Example

Validate this subscription-state ledger locally using `python3 <this-skill>/scripts/validate_audit.py state-machines-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "state-machines-sample",
  "repository": {
    "identity": "acme/studio-uploader",
    "revision": "51d08ef",
    "dirty": false,
    "snapshot": "git-tree:51d08ef"
  },
  "scope": [
    "src/state/upload_reducer.ts"
  ],
  "coverage": [
    {
      "topic": "state-machines",
      "scope": "src/state/upload_reducer.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-301"
      ],
      "notes": "Tabulated all 8 boolean combinations across loading, completed, and errorMessage.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-301",
      "dimension": "state-machines",
      "scope": "uploadReducer",
      "intended": "An upload workflow cannot simultaneously report loading === true and completed === true.",
      "observed": "Dispatching RETRY or START_NEXT spreads prior state without resetting completed to false.",
      "enforcement": "partial boolean spread in src/state/upload_reducer.ts",
      "evidence_ids": [
        "E-301"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-301",
      "path": "src/state/upload_reducer.ts",
      "line_start": 19,
      "line_end": 33,
      "symbol": "uploadReducer",
      "context": "RETRY branch sets loading: true, errorMessage: null while retaining completed: true."
    }
  ],
  "findings": [
    {
      "id": "F-301",
      "topic": "state-machines",
      "tags": [
        "accessibility-state"
      ],
      "title": "Upload reducer enters contradictory loading-and-completed state on re-upload",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-301"
      ],
      "evidence_ids": [
        "E-301"
      ],
      "preconditions": "A completed upload slot is reused via RETRY/START_NEXT for a replacement file.",
      "sequence": [
        "First upload dispatches SUCCEEDED -> {loading: false, completed: true}.",
        "User selects replacement file and dispatches RETRY -> {loading: true, completed: true}."
      ],
      "expected": "Transition to uploading clears completed (or replaces boolean triple with a single status union).",
      "actual": "Submit button enables premature Continue action while spinner is still active.",
      "impact": "Form submits stale artifact reference before replacement upload finishes.",
      "root_cause": "Implicit state encoded across three independent booleans with incomplete reset on transition.",
      "counterevidence": "Initial upload from fresh mount starts with completed: false.",
      "remedy": "Replace boolean flags with a discriminated status union ('idle' | 'uploading' | 'succeeded' | 'failed').",
      "acceptance": [
        "Transitioning from succeeded to uploading never exposes completed state until the new upload resolves."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static reducer transition matrix verified."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

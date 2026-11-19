# Error Semantics and Taxonomy: Specialist Method and Examples

## Tracing Error-to-Empty and Retry Misclassifications

1. Map repository-specific error carriers (`Exception` subclasses, `Result<T, E>` variants, HTTP/gRPC status codes, sentinel tuples) across storage adapters, domain services, and transport serializers.
2. Flag catch blocks that collapse infrastructure or persistence failures (`OperationalError`, timeout, permission denied) into empty collections (`[]`, `None`, `false`) indistinguishable from genuine domain absence.
3. Distinguish intentional optional lookups (where `NotFound` maps to `None` while connection/query errors still propagate) from broad `except Exception: return []` handlers that masquerade outages as empty state.

## Failure Trace vs. Safe Control: Persistence Outage Masquerading as Empty Inbox

```python
# Defect trace: database timeout during save_and_list_notifications returns [], prompting caller to clear UI badge
def list_unread_notifications(db: Connection, user_id: str) -> list[Notification]:
    try:
        return db.query_unread(user_id)
    except Exception:
        return []  # connection drop looks identical to zero unread notifications!


# Safe control: translate only domain LookupMiss; propagate storage/connectivity errors with cause intact
def list_unread_notifications_safe(db: Connection, user_id: str) -> list[Notification]:
    try:
        return db.query_unread(user_id)
    except UserMailboxNotProvisionedError:
        return []
```

## Error-Semantics `audit.json` Deliverable Example

Verify this `error-semantics` ledger locally with `python3 <this-skill>/scripts/validate_audit.py error-semantics-audit.json` against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "error-semantics-sample",
  "repository": {
    "identity": "acme/notifications-api",
    "revision": "12bc84e",
    "dirty": false,
    "snapshot": "git-tree:12bc84e"
  },
  "scope": [
    "src/notifications/repository.py"
  ],
  "coverage": [
    {
      "topic": "error-semantics",
      "scope": "src/notifications/repository.py",
      "status": "inspected",
      "evidence_ids": [
        "E-212"
      ],
      "notes": "Inspected exception translation across notification query and sync callers.",
      "next_action": "Hand off retry backoff policy inspection to repo-audit-failure-modes if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-212",
      "dimension": "error-semantics",
      "scope": "list_unread_notifications",
      "intended": "Storage unavailability raises a transient repository error so sync callers preserve cached unread state.",
      "observed": "Broad except Exception converts database timeouts into an empty list, causing sync callers to wipe local unread state.",
      "enforcement": "none; blanket catch in src/notifications/repository.py",
      "evidence_ids": [
        "E-212"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-212",
      "path": "src/notifications/repository.py",
      "line_start": 27,
      "line_end": 33,
      "symbol": "list_unread_notifications",
      "context": "Catches Exception around db.query_unread(user_id) and returns []."
    }
  ],
  "findings": [
    {
      "id": "F-212",
      "topic": "error-semantics",
      "tags": [
        "resilience"
      ],
      "title": "Blanket exception catch converts database outage into empty unread-notification list",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-212"
      ],
      "evidence_ids": [
        "E-212"
      ],
      "preconditions": "Primary database connection times out while mobile sync polls unread notifications.",
      "sequence": [
        "Mobile sync calls GET /v1/notifications/unread.",
        "db.query_unread raises OperationalError('timeout') inside list_unread_notifications.",
        "Blanket except Exception returns [] with HTTP 200, and client reconciles local cache by deleting unread items."
      ],
      "expected": "Endpoint returns 503 Service Unavailable with retryable error taxonomy so client retains local cache.",
      "actual": "Endpoint returns HTTP 200 with [] and erases cached unread notifications.",
      "impact": "Transient database hiccups silently clear users' unread alert queues.",
      "root_cause": "Catch block fails to distinguish domain mailbox-missing condition from storage transport failure.",
      "counterevidence": "Healthy DB queries with zero unread rows legitimately return [].",
      "remedy": "Catch only UserMailboxNotProvisionedError for [] fallback and let OperationalError map to 503.",
      "acceptance": [
        "Simulated OperationalError in query_unread surfaces 503 without returning HTTP 200 []."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace verified from list_unread_notifications to HTTP sync handler."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

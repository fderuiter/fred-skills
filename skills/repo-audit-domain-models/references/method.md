# Implicit Domain Models: Specialist Method and Examples

## Detecting Primitive Obsession, Unit Confusion, and Swapped Identifiers

1. Inspect function signatures accepting adjacent positional primitives of the same runtime type (`(org_id: str, user_id: str, actor_id: str)` or `(timeout: float, amount: int)`), tracing every caller for transposed arguments or mixed units (seconds vs. milliseconds, cents vs. dollars, UTC vs. local naive timestamps).
2. Check whether domain invariants (currency match before addition, normalized lowercase email, non-negative basis points) are re-implemented divergently across handlers instead of enforced once at value construction.
3. Keep recommendations proportionate: a localized wire DTO with clear named fields and boundary validation does not require heavy wrapper classes unless primitive mixing creates an actual bug or inconsistent rule.

## Failure Trace vs. Safe Control: Positional String ID Transposition in Role Revocation

```python
# Vulnerable signature: two positional str IDs; admin endpoint accidentally passes (actor_id, target_user_id)
def revoke_workspace_member(workspace_id: str, member_user_id: str) -> None:
    db.execute(
        "DELETE FROM memberships WHERE workspace_id = :w AND user_id = :u",
        {"w": workspace_id, "u": member_user_id},
    )


# Caller bug in src/api/admin_members.py:52 swaps workspace_id and member_user_id:
revoke_workspace_member(current_Member_id, workspace_id)

# Safe control: distinct NewType / branded value objects + keyword-only parameters
from typing import NewType

WorkspaceId = NewType("WorkspaceId", str)
UserId = NewType("UserId", str)


def revoke_member_safe(
    *, workspace_id: WorkspaceId, member_user_id: UserId
) -> None: ...
```

## Domain-Model `audit.json` Deliverable Example

Validate this discount-rate ledger locally using `python3 <this-skill>/scripts/validate_audit.py domain-models-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "domain-models-sample",
  "repository": {
    "identity": "acme/team-directory",
    "revision": "83b120c",
    "dirty": false,
    "snapshot": "git-tree:83b120c"
  },
  "scope": [
    "src/domain/memberships.py",
    "src/api/admin_members.py"
  ],
  "coverage": [
    {
      "topic": "domain-models",
      "scope": "src/domain/memberships.py, src/api/admin_members.py",
      "status": "inspected",
      "evidence_ids": [
        "E-701",
        "E-702"
      ],
      "notes": "Cross-checked all 6 callers of revoke_workspace_member and transfer_ownership.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-701",
      "dimension": "domain-models",
      "scope": "revoke_workspace_member",
      "intended": "Revocation deletes membership matching workspace_id and target member_user_id.",
      "observed": "Positional UUID strings allow admin bulk-remove caller to pass (member_id, workspace_id) in reversed order.",
      "enforcement": "untyped positional str parameters",
      "evidence_ids": [
        "E-701",
        "E-702"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-701",
      "path": "src/domain/memberships.py",
      "line_start": 14,
      "line_end": 20,
      "symbol": "revoke_workspace_member",
      "context": "Defines positional (workspace_id: str, member_user_id: str) without prefix or type distinction."
    },
    {
      "id": "E-702",
      "path": "src/api/admin_members.py",
      "line_start": 50,
      "line_end": 55,
      "symbol": "bulk_remove_members",
      "context": "Calls revoke_workspace_member(uid, ws_id) with arguments transposed."
    }
  ],
  "findings": [
    {
      "id": "F-701",
      "topic": "domain-models",
      "tags": [
        "data-integrity"
      ],
      "title": "Transposed UUID arguments in bulk_remove_members silently match zero rows and report HTTP 204",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-701"
      ],
      "evidence_ids": [
        "E-701",
        "E-702"
      ],
      "preconditions": "Administrator invokes POST /v1/admin/workspaces/{ws_id}/bulk-remove.",
      "sequence": [
        "bulk_remove_members iterates target user UUIDs.",
        "Calls revoke_workspace_member(uid, ws_id) with workspace and user UUIDs swapped.",
        "SQL DELETE matches 0 rows and returns HTTP 204 No Content while revoked users retain access."
      ],
      "expected": "Target members are removed from workspace memberships.",
      "actual": "DELETE predicates bind user UUID to workspace_id column; revoked members retain full workspace access.",
      "impact": "Silent access-revocation failure on offboarding bulk actions.",
      "root_cause": "Interchangeable raw str UUID parameters lack keyword-only enforcement or branded types.",
      "counterevidence": "Single-member DELETE route passes positional arguments in expected order.",
      "remedy": "Fix argument order in bulk_remove_members, make parameters keyword-only, and introduce NewType('WorkspaceId', str) / NewType('UserId', str) checked by mypy.",
      "acceptance": [
        "bulk_remove_members deletes targeted membership rows and mypy rejects swapped WorkspaceId/UserId arguments."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified exact parameter order mismatch between memberships.py:14 and admin_members.py:52."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```

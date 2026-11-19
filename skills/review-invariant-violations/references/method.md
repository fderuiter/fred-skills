# Invariant Violations: Specialist Method and Examples

## Auditing State Transitions, Lock Scopes, and Cleanup Symmetry

1. Reconstruct domain and lifecycle invariants across state-machine transitions, lock/transaction scopes, and resource acquisition paths (`rg -n "state\\s*=|status\\s*=|Lock|BEGIN|COMMIT|finally"`).
2. Trace every mutating path—normal completion, early return, exception handler, cancellation, and retry—to verify preconditions hold before mutation and postconditions hold before unlocking or committing.
3. Inspect enclosing transactions, database `CHECK`/`WHERE` predicates, and single-writer actor boundaries so upstream enforcement is recognized rather than misreported.

## Failure Shape vs. Safe Control: Unguarded Escrow Release vs. Precondition-Checked Transition

```python
# Violated state-machine invariant: overwrites DISPUTED escrow directly to RELEASED
def release_escrow_funds(escrow: dict, payout_gateway) -> None:
    if escrow["state"] == "RELEASED":
        return
    escrow["state"] = "RELEASED"  # bypasses active dispute freeze!
    payout_gateway.transfer(escrow["seller_id"], escrow["amount_cents"])


# Safe transition control: enforces allowed source state and dispute resolution guard
def release_escrow_funds_guarded(escrow: dict, payout_gateway) -> None:
    if escrow["state"] == "RELEASED":
        return
    if escrow["state"] != "FUNDED" or escrow.get("dispute_open", False):
        raise ValueError(f"Cannot release escrow from state={escrow['state']!r}")
    payout_gateway.transfer(escrow["seller_id"], escrow["amount_cents"])
    escrow["state"] = "RELEASED"
```

## Invariant Audit `review.json` Deliverable Example

Validate this invariant review payload locally using `python3 <this-skill>/scripts/validate_review.py invariant-review.json` (`assets/review.schema.json`):

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "release_escrow_funds mutates DISPUTED escrows to RELEASED without verifying dispute resolution and marks state RELEASED before payout transfer succeeds.",
  "findings": [
    {
      "id": "INV-001",
      "title": "Unguarded transition allows releasing funds while escrow is DISPUTED",
      "severity": "critical",
      "file": "src/payments/escrow_state.py",
      "line": 14,
      "description": "release_escrow_funds checks only escrow['state'] == 'RELEASED' before assigning 'RELEASED' and invoking payout_gateway.transfer, permitting disputed escrows to pay out prematurely.",
      "recommendation": "Require escrow['state'] == 'FUNDED' and not escrow.get('dispute_open') before initiating payout, and raise ValueError on disallowed states."
    },
    {
      "id": "INV-002",
      "title": "Premature RELEASED state mutation persists if payout_gateway.transfer raises",
      "severity": "high",
      "file": "src/payments/escrow_state.py",
      "line": 15,
      "description": "Assigning escrow['state'] = 'RELEASED' prior to payout_gateway.transfer leaves the in-memory record marked RELEASED when a transient gateway error occurs, blocking retry.",
      "recommendation": "Transition through an explicit 'RELEASING' state with an idempotency key or update state only after transfer confirmation inside one atomic unit."
    }
  ]
}
```

# Invariant: DISPUTED escrow cannot release funds until dispute_resolved is True.
def release_funds(escrow: dict) -> str:
    if escrow.get("state") == "DISPUTED" and not escrow.get("dispute_resolved"):
        raise ValueError("cannot release disputed escrow")
    escrow["state"] = "RELEASED"
    return escrow["state"]

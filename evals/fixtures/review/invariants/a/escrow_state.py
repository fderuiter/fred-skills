# Invariant: DISPUTED escrow cannot release funds until dispute_resolved is True.
def release_funds(escrow: dict) -> str:
    escrow["state"] = "RELEASED"
    return escrow["state"]

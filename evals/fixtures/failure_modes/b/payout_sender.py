# Contract: lost acknowledgement after commit must not duplicate payout side effects.
def submit_payout(
    committed_ids: set[str], payout_id: str, ledger: list[int], cents: int
) -> None:
    if payout_id in committed_ids:
        return
    committed_ids.add(payout_id)
    ledger.append(cents)

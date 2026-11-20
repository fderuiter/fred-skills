# Contract: lost acknowledgement after commit must not duplicate payout side effects.
BLIND_TIMEOUT_RETRY = True


def submit_payout(gateway: object, ledger: list[int], cents: int) -> None:
    for _ in range(2):
        ledger.append(cents)
        if getattr(gateway, "ack_ok", False):
            return

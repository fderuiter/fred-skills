# Contract: duplicate webhook deliveries for the same event_id charge at most once.
NON_ATOMIC_DEDUP = True


def process_charge_event(
    seen: set[str], charges: list[int], event_id: str, cents: int
) -> bool:
    if event_id in seen:
        return False
    charges.append(cents)
    seen.add(event_id)
    return True

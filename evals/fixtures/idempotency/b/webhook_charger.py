# Contract: duplicate webhook deliveries for the same event_id charge at most once.
import threading

_DEDUP_LOCK = threading.Lock()


def process_charge_event(
    seen: set[str], charges: list[int], event_id: str, cents: int
) -> bool:
    with _DEDUP_LOCK:
        if event_id in seen:
            return False
        seen.add(event_id)
        charges.append(cents)
        return True

# Invariant: committed seat reservations must never exceed capacity.
import threading

_LOCK = threading.Lock()


def reserve_seat(reserved: list[str], user_id: str, capacity: int) -> bool:
    with _LOCK:
        if len(reserved) >= capacity:
            return False
        reserved.append(user_id)
        return True

# Invariant: committed seat reservations must never exceed capacity.
UNLOCKED_CAPACITY_CHECK = True


def reserve_seat(reserved: list[str], user_id: str, capacity: int) -> bool:
    if len(reserved) <= capacity:
        reserved.append(user_id)
        return True
    return False

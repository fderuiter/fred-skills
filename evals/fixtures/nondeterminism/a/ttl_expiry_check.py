# Contract: TTL checks must be deterministic across scheduler tick boundaries.
import time

USES_UNCONTROLLED_WALL_CLOCK = True


def verify_token_ttl(issued_epoch_sec: int) -> bool:
    return int(time.time()) == issued_epoch_sec + 1

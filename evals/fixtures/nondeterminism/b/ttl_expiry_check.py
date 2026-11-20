# Contract: TTL checks must be deterministic across scheduler tick boundaries.
from collections.abc import Callable


def verify_token_ttl(issued_ms: int, ttl_ms: int, clock_ms: Callable[[], int]) -> bool:
    return clock_ms() - issued_ms >= ttl_ms

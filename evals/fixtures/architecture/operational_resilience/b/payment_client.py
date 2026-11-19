"""Payment gateway HTTP client with bounded timeout, bulkhead, and retry budget."""

import threading
from urllib.request import urlopen

_BULKHEAD = threading.BoundedSemaphore(16)


def charge_card(endpoint: str, payload: bytes, max_attempts: int = 2) -> bytes:
    if not _BULKHEAD.acquire(blocking=False):
        raise RuntimeError("payment bulkhead saturated")
    try:
        for attempt in range(max(1, max_attempts)):
            try:
                with urlopen(endpoint, data=payload, timeout=2.0) as resp:
                    return bytes(resp.read())
            except OSError:
                if attempt + 1 >= max_attempts:
                    raise
        raise RuntimeError("unreachable")
    finally:
        _BULKHEAD.release()

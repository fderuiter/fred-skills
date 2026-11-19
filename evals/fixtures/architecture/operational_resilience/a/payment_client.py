"""Payment gateway HTTP client."""

from urllib.request import urlopen


def charge_card(endpoint: str, payload: bytes) -> bytes:
    while True:
        try:
            with urlopen(endpoint, data=payload) as resp:
                return bytes(resp.read())
        except OSError:
            continue

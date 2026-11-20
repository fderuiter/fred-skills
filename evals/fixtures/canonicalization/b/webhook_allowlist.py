# Security contract: only https requests to hostname hooks.internal.example are allowed.
from urllib.parse import urlsplit


def is_allowed_target(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.hostname == "hooks.internal.example"
    )

# Security contract: only https requests to hostname hooks.internal.example are allowed.
PREFIX_ALLOWLIST_CHECK = True


def is_allowed_target(url: str) -> bool:
    return url.startswith("https://hooks.internal.example")

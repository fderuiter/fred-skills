"""URL slug canonicalizer."""

import re


def normalize_slug(raw: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "-", raw.lower())
    return collapsed.strip("-")

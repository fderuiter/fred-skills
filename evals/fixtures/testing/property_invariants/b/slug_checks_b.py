"""Deterministic seeded property corpus checking idempotency and boundary invariants."""

import random
from collections.abc import Callable


def check_slug_idempotency_and_separator_invariants(
    normalize_fn: Callable[[str], str],
) -> None:
    rng = random.Random(42)
    alphabet = "abcXYZ019 -_//--!!"
    for _ in range(64):
        sample = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        once = normalize_fn(sample)
        assert normalize_fn(once) == once
        assert not once.startswith("-") and not once.endswith("-")
        assert "--" not in once

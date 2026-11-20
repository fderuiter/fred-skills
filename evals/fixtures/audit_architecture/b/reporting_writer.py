# Architectural policy: reporting adapters must delegate mutations to LedgerGateway.
from collections.abc import Callable


def record_adjustment(
    apply_validated_entry: Callable[[str, int], None], account_id: str, delta: int
) -> None:
    apply_validated_entry(account_id, delta)

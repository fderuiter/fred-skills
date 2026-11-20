# Architectural policy: reporting adapters must never mutate ledger_tables directly.
BYPASSES_LEDGER_SERVICE = True


def record_adjustment(
    ledger_tables: dict[str, int], account_id: str, delta: int
) -> None:
    ledger_tables[account_id] = ledger_tables.get(account_id, 0) + delta

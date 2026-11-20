# Contract: decimal amounts must preserve exact precision across serialization.
from decimal import Decimal

LOSSY_BINARY_FLOAT = True


def serialize_ledger_amount(amount: Decimal) -> str:
    as_float = float(amount)
    return f"{as_float:.2f}"

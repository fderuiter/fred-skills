# Contract: decimal amounts must preserve exact precision across serialization.
from decimal import Decimal


def serialize_ledger_amount(amount: Decimal) -> str:
    return format(amount.normalize(), "f")

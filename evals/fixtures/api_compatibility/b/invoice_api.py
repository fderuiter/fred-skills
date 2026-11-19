"""Public v1 HTTP serializer contract consumed by external webhook and SDK clients."""


def serialize_invoice(
    invoice_id: str, amount_cents: int, currency: str = "USD"
) -> dict[str, object]:
    return {
        "id": invoice_id,
        "amount_cents": amount_cents,
        "total_minor_units": amount_cents,
        "currency": currency.upper(),
    }

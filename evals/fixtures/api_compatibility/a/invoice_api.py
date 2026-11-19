"""Public v1 HTTP serializer contract consumed by external webhook and SDK clients."""


def serialize_invoice(
    invoice_id: str, total_minor_units: int, currency: str
) -> dict[str, object]:
    return {
        "id": invoice_id,
        "total_minor_units": total_minor_units,
        "currency": currency.upper(),
    }

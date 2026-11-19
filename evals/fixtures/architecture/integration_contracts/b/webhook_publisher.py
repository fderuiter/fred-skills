"""Outbound order webhook publisher."""


def build_order_webhook(
    event_id: str, order_id: str, total_cents: int
) -> dict[str, object]:
    return {
        "headers": {"Idempotency-Key": event_id},
        "body": {
            "schema_version": "1.0",
            "order_id": order_id,
            "total_cents": total_cents,
        },
    }

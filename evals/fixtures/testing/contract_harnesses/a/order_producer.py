"""Order event producer."""


def emit_order_event(order_id: str, total_cents: int) -> dict[str, object]:
    return {"order_id": order_id, "total_cents": total_cents}

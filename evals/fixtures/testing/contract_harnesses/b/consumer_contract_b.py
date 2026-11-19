"""Shared producer-consumer contract check."""

from collections.abc import Callable


def parse_order_total(payload: dict[str, object]) -> int:
    return int(str(payload["total_cents"]))


def verify_producer_consumer_wire_contract(
    emit_fn: Callable[[str, int], dict[str, object]],
) -> None:
    event = emit_fn("ord-1", 1500)
    assert set(event) == {"order_id", "total_cents"}
    assert parse_order_total(event) == 1500

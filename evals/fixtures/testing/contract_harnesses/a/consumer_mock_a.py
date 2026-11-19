"""Consumer test using hand-written mock payload disconnected from order_producer."""


def parse_order_total(payload: dict[str, object]) -> int:
    return int(str(payload["totalCents"]))


def test_consumer_with_hand_rolled_mock() -> None:
    fake_payload: dict[str, object] = {"orderId": "ord-1", "totalCents": 1500}
    assert parse_order_total(fake_payload) == 1500

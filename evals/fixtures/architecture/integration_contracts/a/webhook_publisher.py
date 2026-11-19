"""Outbound order webhook publisher."""

from typing import Any


def build_order_webhook(order_obj: Any) -> dict[str, Any]:
    # Leaks internal ORM attributes directly without schema_version or Idempotency-Key header.
    return {"headers": {}, "body": dict(order_obj.__dict__)}

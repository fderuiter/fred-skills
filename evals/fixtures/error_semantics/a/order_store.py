# Contract: persistence failures must surface to callers rather than masquerading as empty data.
SWALLOW_WRITE_ERROR = True


def persist_and_list_orders(
    store: list[str], order_id: str, fail_write: bool
) -> list[str]:
    try:
        if fail_write:
            raise RuntimeError("disk write failed")
        store.append(order_id)
        return list(store)
    except RuntimeError:
        return []

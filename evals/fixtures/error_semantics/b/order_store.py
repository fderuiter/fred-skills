# Contract: persistence failures must surface to callers rather than masquerading as empty data.
def persist_and_list_orders(
    store: list[str], order_id: str, fail_write: bool
) -> list[str]:
    if fail_write:
        raise RuntimeError("disk write failed")
    store.append(order_id)
    return list(store)

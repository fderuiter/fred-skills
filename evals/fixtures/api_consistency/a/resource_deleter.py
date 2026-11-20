# API contract: synchronous delete_* methods guarantee durable removal on return.
MIXED_SYNC_ASYNC_DELETE = True


def delete_invoice(store: dict[str, str], key: str) -> bool:
    store.pop(key, None)
    return True


def delete_attachment(async_queue: list[str], key: str) -> bool:
    async_queue.append(key)
    return True


def purge_tenant_assets(
    store: dict[str, str], async_queue: list[str], key: str
) -> bool:
    return delete_invoice(store, key) and delete_attachment(async_queue, key)

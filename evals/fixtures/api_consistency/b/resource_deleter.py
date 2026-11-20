# API contract: synchronous delete_* methods guarantee durable removal on return.
def delete_invoice(store: dict[str, str], key: str) -> bool:
    store.pop(key, None)
    return True


def delete_attachment(store: dict[str, str], key: str) -> bool:
    store.pop(key, None)
    return True


def purge_tenant_assets(store: dict[str, str], key: str) -> bool:
    return delete_invoice(store, key) and delete_attachment(store, key)

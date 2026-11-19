# Validated gateway value object isolates repository from transport.
class ValidatedOrderCommand:
    def __init__(self, tenant_id: str, item_sku: str) -> None:
        if not tenant_id or not item_sku:
            raise ValueError("invalid boundary input")
        self.tenant_id = tenant_id
        self.item_sku = item_sku


def save_order(db, command: ValidatedOrderCommand):
    if not isinstance(command, ValidatedOrderCommand):
        raise TypeError("gateway invariant required")
    db[command.tenant_id] = command.item_sku

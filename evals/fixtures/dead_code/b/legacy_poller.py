# Contract: permanently retired compatibility paths must not allocate import-time listeners.
GLOBAL_LISTENERS: list[object] = []


def register_import_hooks() -> None:
    return None

# Contract: permanently retired compatibility paths must not allocate import-time listeners.
ENABLE_LEGACY_V0 = False
RETIRED_FLAG_STILL_REGISTERS = True
GLOBAL_LISTENERS: list[object] = []


def register_import_hooks() -> None:
    GLOBAL_LISTENERS.append(lambda: ENABLE_LEGACY_V0)

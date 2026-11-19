_SHARED_STATE: list[str] = []


def record_event(name: str) -> int:
    _SHARED_STATE.append(name)
    return len(_SHARED_STATE)

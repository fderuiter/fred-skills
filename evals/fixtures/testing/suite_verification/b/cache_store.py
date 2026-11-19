class CacheStore:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record_event(self, name: str) -> int:
        self.events.append(name)
        return len(self.events)

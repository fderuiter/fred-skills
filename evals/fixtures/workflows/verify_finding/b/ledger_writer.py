import threading


class LedgerWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: list[str] = []

    def append(self, item: str) -> None:
        with self._lock:
            self.entries.append(item)

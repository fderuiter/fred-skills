class LedgerWriter:
    def __init__(self) -> None:
        self.entries: list[str] = []

    def append(self, item: str) -> None:
        self.entries.append(item)

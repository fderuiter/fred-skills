"""Async user session profile cache."""

import asyncio
from collections.abc import Awaitable, Callable


class SessionCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._profiles: dict[str, str] = {}
        self._generation: dict[str, int] = {}

    async def refresh(
        self, user_id: str, fetch_remote: Callable[[str], Awaitable[str]]
    ) -> str:
        async with self._lock:
            gen = self._generation.get(user_id, 0) + 1
            self._generation[user_id] = gen
        profile = await fetch_remote(user_id)
        async with self._lock:
            if self._generation.get(user_id) == gen:
                self._profiles[user_id] = profile
            return self._profiles[user_id]

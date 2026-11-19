"""Async user session profile cache."""

import asyncio
from collections.abc import Awaitable, Callable


class SessionCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._profiles: dict[str, str] = {}

    async def refresh(
        self, user_id: str, fetch_remote: Callable[[str], Awaitable[str]]
    ) -> str:
        async with self._lock:
            profile = await fetch_remote(user_id)
            self._profiles[user_id] = profile
            return profile

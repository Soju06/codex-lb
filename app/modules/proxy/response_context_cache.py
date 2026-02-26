from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass

from app.core.types import JsonValue


@dataclass(slots=True)
class _CachedResponseContext:
    input_items: list[JsonValue]
    expires_at: float
    updated_at: float


class ResponseContextCache:
    def __init__(self, *, ttl_seconds: int = 6 * 60 * 60, max_entries: int = 2000) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[tuple[str, str], _CachedResponseContext] = {}
        self._lock = asyncio.Lock()

    async def get_context(self, scope_key: str, response_id: str) -> list[JsonValue] | None:
        now = time.time()
        key = (scope_key, response_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            entry.updated_at = now
            return copy.deepcopy(entry.input_items)

    async def put_context(
        self,
        scope_key: str,
        response_id: str,
        input_items: list[JsonValue],
        assistant_text: str | None,
    ) -> None:
        now = time.time()
        context_items: list[JsonValue] = copy.deepcopy(input_items)
        if assistant_text:
            context_items.append({"role": "assistant", "content": assistant_text})

        entry = _CachedResponseContext(
            input_items=context_items,
            expires_at=now + self._ttl_seconds,
            updated_at=now,
        )
        key = (scope_key, response_id)
        async with self._lock:
            self._entries[key] = entry
            self._prune_locked(now)

    async def reset(self) -> None:
        async with self._lock:
            self._entries.clear()

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, value in self._entries.items() if value.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

        overflow = len(self._entries) - self._max_entries
        if overflow <= 0:
            return

        oldest = sorted(self._entries.items(), key=lambda item: item[1].updated_at)[:overflow]
        for key, _ in oldest:
            self._entries.pop(key, None)


_RESPONSE_CONTEXT_CACHE = ResponseContextCache()


def get_response_context_cache() -> ResponseContextCache:
    return _RESPONSE_CONTEXT_CACHE

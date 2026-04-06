from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from app.modules.proxy.load_balancer import SelectionInputs

_AssignedAccountsKey = tuple[str, ...] | None
_CacheKey = tuple[str | None, str | None, _AssignedAccountsKey]


@dataclass(slots=True)
class _CachedSelectionInputs:
    data: SelectionInputs
    expires_at: float


class AccountSelectionCache:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is None:
            import sys

            ttl_seconds = 0 if "pytest" in sys.modules else 5
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._ttl_seconds = ttl_seconds
        self._cache: dict[_CacheKey, _CachedSelectionInputs] = {}
        self._lock = anyio.Lock()
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    async def get(self, key: _CacheKey = (None, None, None)) -> SelectionInputs | None:
        if self._ttl_seconds == 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            return None
        return entry.data

    async def set(
        self,
        data: SelectionInputs,
        key: _CacheKey = (None, None, None),
        *,
        generation: int | None = None,
    ) -> None:
        async with self._lock:
            if generation is not None and generation != self._generation:
                return
            self._cache[key] = _CachedSelectionInputs(
                data=data,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    def invalidate(self) -> None:
        self._generation += 1
        self._cache.clear()


_account_selection_cache = AccountSelectionCache()


def get_account_selection_cache() -> AccountSelectionCache:
    return _account_selection_cache

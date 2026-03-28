from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from app.modules.proxy.load_balancer import SelectionInputs


@dataclass(slots=True)
class _CachedSelectionInputs:
    data: SelectionInputs
    expires_at: float


class AccountSelectionCache:
    def __init__(self, ttl_seconds: int = 5) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cached: _CachedSelectionInputs | None = None
        self._lock = anyio.Lock()

    async def get(self) -> SelectionInputs | None:
        cached = self._cached
        if cached is None:
            return None
        if time.monotonic() >= cached.expires_at:
            return None
        return cached.data

    async def set(self, data: SelectionInputs) -> None:
        async with self._lock:
            self._cached = _CachedSelectionInputs(
                data=data,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    def invalidate(self) -> None:
        self._cached = None


_account_selection_cache = AccountSelectionCache()


def get_account_selection_cache() -> AccountSelectionCache:
    return _account_selection_cache

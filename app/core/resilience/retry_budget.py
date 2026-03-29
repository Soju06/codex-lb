from __future__ import annotations

import asyncio
import time
from collections import deque


class RetryBudget:
    def __init__(self, max_retries_per_window: int = 100, window_seconds: int = 60) -> None:
        self.max_retries = max_retries_per_window
        self.window_seconds = window_seconds
        self._retry_timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            while self._retry_timestamps and self._retry_timestamps[0] < cutoff:
                self._retry_timestamps.popleft()

            if len(self._retry_timestamps) >= self.max_retries:
                return False

            self._retry_timestamps.append(now)
            return True

    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        recent = sum(1 for timestamp in self._retry_timestamps if timestamp >= cutoff)
        return max(0, self.max_retries - recent)


_retry_budget: RetryBudget | None = None


def get_retry_budget() -> RetryBudget:
    global _retry_budget
    if _retry_budget is None:
        _retry_budget = RetryBudget()
    return _retry_budget


__all__ = ["RetryBudget", "get_retry_budget"]

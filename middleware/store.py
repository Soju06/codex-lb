"""In-memory LRU + TTL set of reasoning ids, for repair_followup="stateful".

Records the reasoning ids after which a synthetic continue pair was appended
during a turn's continuation, so a later turn's request can have those pairs
re-inserted by id (never by adjacency). Single-instance only.
"""
from __future__ import annotations

import time
from collections import OrderedDict


class IdStore:
    def __init__(self, maxsize: int = 10000, ttl_seconds: float = 3600.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._d: "OrderedDict[str, float]" = OrderedDict()  # id -> expiry (monotonic)

    def add(self, key: str) -> None:
        now = time.monotonic()
        self._d[key] = now + self.ttl
        self._d.move_to_end(key)
        self._purge(now)

    def __contains__(self, key: str) -> bool:
        exp = self._d.get(key)
        if exp is None:
            return False
        if exp < time.monotonic():
            del self._d[key]
            return False
        self._d.move_to_end(key)
        return True

    def _purge(self, now: float) -> None:
        # drop expired from the front, then enforce size cap (LRU = oldest first)
        while self._d:
            k, exp = next(iter(self._d.items()))
            if exp < now:
                del self._d[k]
            else:
                break
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

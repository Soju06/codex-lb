"""Short-lived proof that a foreign ChatGPT bearer is real.

``GET /backend-api/wham/usage`` answers any ChatGPT bearer that is not a pool
account after one upstream usage call. That call is cheap but not free, and the
CLI re-reads its limits periodically, so a success is remembered for a minute.
The key is a digest of the bearer and the presented ``chatgpt-account-id`` --
the raw token is never stored. Concurrent identical proofs share one in-flight
upstream call; a failed proof is never cached, so a bogus token still costs an
upstream round trip on every attempt.

TTL 60 s / 1024 entries: one minute bounds how long a revoked bearer keeps
answering from the pool, and 1024 keeps the map bounded under a token flood.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from functools import partial

from app.core.utils.shared_future import wait_on_shared_future

PROOF_TTL_SECONDS = 60.0
PROOF_MAX_ENTRIES = 1024

# Domain separation, so the digest cannot be confused with another token hash.
_PROOF_KEY_DOMAIN = b"codex-lb/wham-usage-proof/v1\x00"


def wham_usage_proof_key(token: str, account_id: str) -> str:
    """Digest the bearer and the presented account id; the raw token stays out of the cache."""
    digest = hashlib.sha256(_PROOF_KEY_DOMAIN)
    digest.update(token.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(account_id.encode("utf-8"))
    return digest.hexdigest()


class WhamUsageProofCache:
    def __init__(self, *, ttl_seconds: float = PROOF_TTL_SECONDS, max_entries: int = PROOF_MAX_ENTRIES) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._proved: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def prove(self, key: str, prove: Callable[[], Awaitable[None]]) -> None:
        """Return once ``key`` is proved, running ``prove`` at most once per key at a time.

        A success is cached; the exception ``prove`` raises reaches every caller
        waiting on that attempt and is not cached, so the next call retries.
        """
        async with self._lock:
            if self._is_fresh(key):
                return
            flight = self._inflight.get(key)
            if flight is None or flight.done():
                flight = asyncio.create_task(self._prove_once(key, prove))
                self._inflight[key] = flight
                flight.add_done_callback(partial(self._release, key))
        # The proof runs in its own task, so one caller disconnecting neither
        # cancels the shared work nor fails the other waiters.
        await wait_on_shared_future(flight)

    def clear(self) -> None:
        self._proved.clear()
        self._inflight.clear()

    async def _prove_once(self, key: str, prove: Callable[[], Awaitable[None]]) -> None:
        await prove()
        self._remember(key)

    def _release(self, key: str, flight: asyncio.Task[None]) -> None:
        if self._inflight.get(key) is flight:
            del self._inflight[key]
        if not flight.cancelled():
            # Read the outcome so a waiter-less failure never logs "never retrieved".
            flight.exception()

    def _is_fresh(self, key: str) -> bool:
        expires_at = self._proved.get(key)
        if expires_at is None:
            return False
        if expires_at > time.monotonic():
            return True
        del self._proved[key]
        return False

    def _remember(self, key: str) -> None:
        if key not in self._proved and len(self._proved) >= self._max_entries:
            del self._proved[min(self._proved, key=self._proved.__getitem__)]
        self._proved[key] = time.monotonic() + self._ttl_seconds


_wham_usage_proof_cache = WhamUsageProofCache()


def get_wham_usage_proof_cache() -> WhamUsageProofCache:
    return _wham_usage_proof_cache

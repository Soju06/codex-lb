from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Awaitable, TypeVar

import aiohttp

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 60,
        success_threshold: int = 2,
    ) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout_seconds
        ):
            return CircuitState.HALF_OPEN
        return self._state

    async def call(self, coro: Awaitable[T]) -> T:
        async with self._lock:
            current_state = self.state
            if current_state == CircuitState.OPEN:
                raise CircuitBreakerOpenError("Circuit breaker is OPEN")

        try:
            result = await coro
            await self._record_success()
            return result
        except Exception as exc:
            await self._record_failure(exc)
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self.state == CircuitState.CLOSED:
                if self._failure_count > 0:
                    self._failure_count = 0

    async def _record_failure(self, exc: Exception) -> None:
        if not _is_server_error(exc):
            return
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._success_count = 0
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN


def _is_server_error(exc: Exception) -> bool:
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status >= 500
    if isinstance(exc, (asyncio.TimeoutError, aiohttp.ServerTimeoutError)):
        return True
    # ProxyResponseError wraps upstream HTTP responses — only 5xx is a server error.
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code >= 500
    return True


_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker(settings: object | None = None) -> CircuitBreaker | None:
    global _circuit_breaker

    if settings is None:
        return _circuit_breaker

    enabled = getattr(settings, "circuit_breaker_enabled", False)
    if enabled and _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker(
            failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
            recovery_timeout_seconds=getattr(settings, "circuit_breaker_recovery_timeout_seconds", 60),
        )

    return _circuit_breaker if enabled else None

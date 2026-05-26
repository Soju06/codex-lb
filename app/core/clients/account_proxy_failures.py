"""Runtime per-account SOCKS5 proxy failure tracker.

Counts proxy-level errors in a rolling window per account. When the count
reaches the configured threshold inside the configured window, the
account is transitioned to ``DEACTIVATED`` with
``deactivation_reason="proxy_unreachable"`` and its cached
:class:`HttpClient` is evicted.

Design notes
------------

- The tracker is **process-local**. Replicas observe their own per-account
  failure stream; if the proxy is broken every replica will independently
  reach the threshold and call
  :meth:`AccountsRepository.update_status_if_current`. The predicate keeps
  repeated transitions idempotent so we never thrash the row.
- Non-proxy errors (HTTP 4xx/5xx, JSON decode failures, generic
  ``aiohttp.ClientError``) MUST NOT contribute to the counter. The
  existing per-account circuit breaker continues to handle those.
- The deque is reset whenever the cached per-account client is invalidated
  (proxy edited, proxy cleared, account reactivated, account deleted).
  That avoids a freshly-reactivated account inheriting the failure window
  of the previous configuration.
- The deactivation callback runs **outside the tracker's lock** so a slow
  DB roundtrip cannot block recording new failures for other accounts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import aiohttp
from aiohttp_socks._errors import (
    ProxyConnectionError as AiohttpSocksProxyConnectionError,
)
from aiohttp_socks._errors import (
    ProxyError as AiohttpSocksProxyError,
)
from aiohttp_socks._errors import (
    ProxyTimeoutError as AiohttpSocksProxyTimeoutError,
)
from python_socks._errors import (
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
)
from websockets.exceptions import InvalidProxy
from websockets.exceptions import ProxyError as WebsocketsProxyError

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)


# Tuple of exception types that count as "the SOCKS5 proxy is the problem".
# ``aiohttp_socks`` wraps the ``python_socks`` errors in its own classes
# (which do NOT inherit from the upstream ones), so both hierarchies must
# be enumerated explicitly. ``aiohttp.ClientProxyConnectionError`` and the
# ``websockets`` proxy exceptions cover the other account-bound transports.
_PROXY_ERRORS: tuple[type[BaseException], ...] = (
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
    AiohttpSocksProxyConnectionError,
    AiohttpSocksProxyError,
    AiohttpSocksProxyTimeoutError,
    aiohttp.ClientProxyConnectionError,
    InvalidProxy,
    WebsocketsProxyError,
)


def is_proxy_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` is one of the tracked proxy-level errors."""

    return isinstance(exc, _PROXY_ERRORS)


class AccountDeactivationCallback(Protocol):
    """Called when an account crosses the proxy-failure threshold.

    The callback MUST be idempotent: it can be invoked from different
    replicas concurrently and from the same replica multiple times for
    the same account if errors keep arriving while a previous transition
    is in flight.
    """

    async def __call__(self, account_id: str, *, count: int, window_seconds: float) -> None:  # pragma: no cover
        ...


class ProxyFailureTracker:
    """Per-instance rolling-window proxy failure counter."""

    def __init__(
        self,
        *,
        threshold: int | None = None,
        window_seconds: float | None = None,
        deactivation_callback: AccountDeactivationCallback | None = None,
    ) -> None:
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._deactivation_callback = deactivation_callback
        self._lock = asyncio.Lock()
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        # Set of account_ids whose deactivation callback is currently
        # in flight. Prevents a burst of in-flight failures (after the
        # threshold trips) from each firing their own redundant
        # deactivation callback (which would each open a new DB
        # session). The flag is cleared inside ``reset_for_account`` so
        # the next legitimate threshold-crossing fires again.
        self._deactivating: set[str] = set()

    def _effective_thresholds(self) -> tuple[int, float]:
        if self._threshold is not None and self._window_seconds is not None:
            return int(self._threshold), float(self._window_seconds)
        settings = get_settings()
        threshold = self._threshold or settings.account_proxy_failure_threshold
        window = self._window_seconds or settings.account_proxy_failure_window_seconds
        return int(threshold), float(window)

    async def record_failure(self, account_id: str, exc: BaseException) -> None:
        """Record a proxy-level failure for ``account_id``.

        On reaching the threshold within the rolling window, invokes the
        deactivation callback. The callback is invoked outside the lock.
        Subsequent failures continue to extend the deque, but while a
        deactivation callback is already in flight for this account the
        tracker suppresses duplicate callbacks until ``reset_for_account``
        clears the in-flight flag.
        """

        if not account_id:
            return
        threshold, window = self._effective_thresholds()
        now = time.monotonic()
        async with self._lock:
            timeline = self._timestamps[account_id]
            cutoff = now - window
            while timeline and timeline[0] < cutoff:
                timeline.popleft()
            timeline.append(now)
            count = len(timeline)
            if count < threshold:
                return
            if account_id in self._deactivating:
                # Another in-flight failure is already firing the
                # deactivation callback. Suppress this one — when the
                # cached client is invalidated and ``reset_for_account``
                # is called, the deque is cleared and the next crossing
                # will fire again.
                return
            self._deactivating.add(account_id)
        # Callback runs outside the lock so a slow DB roundtrip does not
        # block other accounts' failure recording.
        callback = self._deactivation_callback
        if callback is None:
            logger.warning(
                "Account %s reached %d proxy failures in %.1fs but no deactivation callback installed: %s",
                account_id,
                count,
                window,
                exc,
            )
            async with self._lock:
                self._deactivating.discard(account_id)
            return
        # Track whether the callback completed successfully. The
        # ``finally`` block clears the in-flight flag for every failure
        # mode (including ``asyncio.CancelledError``, which inherits from
        # ``BaseException`` since Python 3.8 and is therefore NOT caught
        # by ``except Exception``). Without this, a cancelled callback
        # would leave ``_deactivating`` set forever and silently
        # suppress every future deactivation attempt for the same
        # account_id.
        fired_successfully = False
        try:
            await callback(account_id, count=count, window_seconds=window)
            fired_successfully = True
        except Exception:
            logger.exception("Proxy deactivation callback raised for account_id=%s", account_id)
        finally:
            if not fired_successfully:
                # Clear the in-flight flag on failure or cancellation so
                # a retry can fire the callback again. ``CancelledError``
                # falls into this branch because we don't catch it
                # explicitly; the ``finally`` runs before the exception
                # propagates upward.
                async with self._lock:
                    self._deactivating.discard(account_id)

    async def reset_for_account(self, account_id: str) -> None:
        """Forget the failure window for ``account_id``.

        Called when the account's cached client is invalidated (proxy
        edited / cleared / account reactivated / deleted). Keeps the
        tracker from inheriting a stale window across config changes.
        Also clears the "deactivating in flight" flag so the next
        legitimate threshold-crossing can fire its own callback.
        """

        async with self._lock:
            self._timestamps.pop(account_id, None)
            self._deactivating.discard(account_id)

    async def reset_all_for_test(self) -> None:
        async with self._lock:
            self._timestamps.clear()
            self._deactivating.clear()

    def snapshot_count_for_test(self, account_id: str) -> int:
        return len(self._timestamps.get(account_id, ()))


_default_tracker: ProxyFailureTracker | None = None


def get_default_tracker() -> ProxyFailureTracker:
    """Module-level singleton tracker used by production call sites."""

    global _default_tracker
    if _default_tracker is None:
        _default_tracker = ProxyFailureTracker(deactivation_callback=_default_deactivation_callback)
    return _default_tracker


def set_default_tracker_for_test(tracker: ProxyFailureTracker | None) -> None:
    """Inject (or clear) a tracker instance for the lifetime of a test."""

    global _default_tracker
    _default_tracker = tracker


async def _default_deactivation_callback(account_id: str, *, count: int, window_seconds: float) -> None:
    """Production deactivation: idempotent ACTIVE -> DEACTIVATED transition.

    Imports lazily to avoid creating a cycle with
    :mod:`app.core.clients.account_http`, which imports the tracker via
    :func:`reset_for_account`.
    """

    from app.core.clients.account_http import invalidate_account_client  # noqa: PLC0415
    from app.db.models import AccountStatus  # noqa: PLC0415
    from app.db.session import get_background_session  # noqa: PLC0415
    from app.modules.accounts.repository import AccountsRepository  # noqa: PLC0415
    from app.modules.proxy.account_cache import get_account_selection_cache  # noqa: PLC0415

    transitioned = False
    async with get_background_session() as session:
        repo = AccountsRepository(session)
        transitioned = await repo.update_status_if_current(
            account_id,
            AccountStatus.DEACTIVATED,
            deactivation_reason="proxy_unreachable",
            expected_status=AccountStatus.ACTIVE,
        )
    await invalidate_account_client(account_id)
    if transitioned:
        get_account_selection_cache().invalidate()
        logger.warning(
            "Account %s deactivated after %d proxy failures within %.1fs (reason=proxy_unreachable)",
            account_id,
            count,
            window_seconds,
        )


@asynccontextmanager
async def record_proxy_errors_for_account(
    account_id: str,
    *,
    tracker: ProxyFailureTracker | None = None,
) -> AsyncIterator[None]:
    """Wrap an account-bound outbound call so proxy-level errors are tracked.

    Non-proxy errors pass through unchanged. The original exception is
    re-raised in all cases.
    """

    if not account_id:
        # Login bootstrap and other non-account paths land here. Tracker
        # is not interested in them.
        yield
        return
    try:
        yield
    except BaseException as exc:
        if is_proxy_error(exc):
            current = tracker or get_default_tracker()
            try:
                await current.record_failure(account_id, exc)
            except Exception:
                logger.exception("Failed to record proxy failure account_id=%s", account_id)
        raise

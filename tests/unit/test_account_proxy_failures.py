"""Unit tests for the runtime SOCKS5 proxy failure tracker."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp_socks._errors import (
    ProxyConnectionError as AiohttpSocksProxyConnectionError,
)
from aiohttp_socks._errors import (
    ProxyError as AiohttpSocksProxyError,
)
from python_socks._errors import (
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
)
from websockets.exceptions import InvalidProxy
from websockets.exceptions import ProxyError as WebsocketsProxyError

from app.core.clients import account_proxy_failures as tracker_module
from app.core.clients.account_proxy_failures import (
    ProxyFailureTracker,
    is_proxy_error,
    record_proxy_errors_for_account,
    set_default_tracker_for_test,
)

pytestmark = pytest.mark.unit


def test_is_proxy_error_recognizes_each_hierarchy() -> None:
    # python_socks
    assert is_proxy_error(ProxyConnectionError("connect refused"))
    assert is_proxy_error(ProxyError("auth fail"))
    assert is_proxy_error(ProxyTimeoutError("negotiation timed out"))
    # aiohttp_socks (separate hierarchy)
    assert is_proxy_error(AiohttpSocksProxyConnectionError("eaccess"))
    assert is_proxy_error(AiohttpSocksProxyError("rejected"))
    # aiohttp's own connector wrapper. Build a minimal instance without
    # depending on private aiohttp helpers.
    fake_key = type(
        "FakeKey",
        (),
        {"host": "h", "port": 1, "is_ssl": False, "ssl": None, "proxy": None, "proxy_auth": None},
    )()
    assert is_proxy_error(aiohttp.ClientProxyConnectionError(cast(Any, fake_key), OSError("x")))
    # websockets transport proxy failures (explicit per-account WS proxy).
    assert is_proxy_error(InvalidProxy("http://proxy.invalid", "unsupported proxy scheme"))
    assert is_proxy_error(WebsocketsProxyError("proxy handshake failed"))
    # Non-proxy errors must not match.
    assert not is_proxy_error(aiohttp.ClientResponseError(cast(Any, None), (), status=500, message="500"))
    assert not is_proxy_error(asyncio.TimeoutError("read"))
    assert not is_proxy_error(ValueError("nope"))


@pytest.mark.asyncio
async def test_tracker_does_not_fire_below_threshold() -> None:
    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=3, window_seconds=60.0, deactivation_callback=callback)

    await tracker.record_failure("acc", ProxyConnectionError("x"))
    await tracker.record_failure("acc", ProxyConnectionError("x"))

    callback.assert_not_awaited()
    assert tracker.snapshot_count_for_test("acc") == 2


@pytest.mark.asyncio
async def test_tracker_fires_callback_on_threshold() -> None:
    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=3, window_seconds=60.0, deactivation_callback=callback)

    for _ in range(3):
        await tracker.record_failure("acc", ProxyConnectionError("x"))

    callback.assert_awaited_once()
    await_args = callback.await_args
    assert await_args is not None
    args, kwargs = await_args
    assert args == ("acc",) or kwargs.get("account_id") == "acc"
    assert kwargs.get("count") == 3
    assert kwargs.get("window_seconds") == 60.0


@pytest.mark.asyncio
async def test_tracker_window_decay_drops_old_failures(monkeypatch) -> None:
    """Failures older than the window MUST not count toward the threshold."""

    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=3, window_seconds=60.0, deactivation_callback=callback)
    fake_now = [1_000.0]

    def _patched_monotonic() -> float:
        return fake_now[0]

    # Two failures inside the window.
    import time as _time

    monkeypatch.setattr(_time, "monotonic", _patched_monotonic)
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    fake_now[0] += 30.0
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    # 70s after the first failure: the first should be evicted.
    fake_now[0] += 50.0
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    # Only 2 failures in window now → callback NOT yet fired.
    callback.assert_not_awaited()
    # One more failure within the window → 3 total, threshold met.
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_tracker_resets_for_account_clears_window() -> None:
    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=3, window_seconds=60.0, deactivation_callback=callback)

    await tracker.record_failure("acc", ProxyConnectionError("x"))
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    await tracker.reset_for_account("acc")

    # Two more failures: we'd need a 3rd before triggering.
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    callback.assert_not_awaited()
    assert tracker.snapshot_count_for_test("acc") == 2


@pytest.mark.asyncio
async def test_tracker_callback_exception_does_not_propagate() -> None:
    """A failing deactivation callback must never crash request processing."""

    async def bad_callback(account_id: str, *, count: int, window_seconds: float) -> None:
        raise RuntimeError("DB exploded")

    tracker = ProxyFailureTracker(threshold=2, window_seconds=60.0, deactivation_callback=bad_callback)

    # Should swallow the exception silently (logged at ERROR via logger.exception).
    await tracker.record_failure("acc", ProxyConnectionError("x"))
    await tracker.record_failure("acc", ProxyConnectionError("x"))


@pytest.mark.asyncio
async def test_record_proxy_errors_for_account_re_raises_and_records() -> None:
    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=1, window_seconds=60.0, deactivation_callback=callback)
    set_default_tracker_for_test(tracker)
    try:
        with pytest.raises(ProxyConnectionError):
            async with record_proxy_errors_for_account("acc"):
                raise ProxyConnectionError("simulated")
        callback.assert_awaited_once()
        # Non-proxy errors pass through without recording.
        callback.reset_mock()
        with pytest.raises(ValueError):
            async with record_proxy_errors_for_account("acc"):
                raise ValueError("not a proxy error")
        callback.assert_not_awaited()
    finally:
        set_default_tracker_for_test(None)


@pytest.mark.asyncio
async def test_record_proxy_errors_for_account_skips_when_account_id_empty() -> None:
    """No account context (e.g. login bootstrap) MUST not invoke the tracker."""

    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=1, window_seconds=60.0, deactivation_callback=callback)
    set_default_tracker_for_test(tracker)
    try:
        with pytest.raises(ProxyConnectionError):
            async with record_proxy_errors_for_account(""):
                raise ProxyConnectionError("x")
        callback.assert_not_awaited()
    finally:
        set_default_tracker_for_test(None)


@pytest.mark.asyncio
async def test_default_deactivation_callback_uses_update_status_if_current(monkeypatch) -> None:
    """The production callback runs the idempotent ACTIVE→DEACTIVATED transition."""

    from app.core.clients import account_http as account_http_module
    from app.core.clients import account_proxy_failures as tracker_module_local
    from app.db.models import AccountStatus

    update_calls: list[dict[str, Any]] = []
    invalidate_calls: list[str] = []
    cache_calls: list[None] = []

    class _StubRepo:
        def __init__(self, _session) -> None:
            pass

        async def update_status_if_current(self, account_id, status, **kwargs):
            update_calls.append({"account_id": account_id, "status": status, **kwargs})
            return True

    class _StubSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return None

    def _stub_session_factory():
        return _StubSession()

    async def _fake_invalidate(account_id: str) -> None:
        invalidate_calls.append(account_id)

    class _StubCache:
        def invalidate(self) -> None:
            cache_calls.append(None)

    monkeypatch.setattr("app.modules.accounts.repository.AccountsRepository", _StubRepo)
    monkeypatch.setattr("app.db.session.get_background_session", _stub_session_factory)
    monkeypatch.setattr(account_http_module, "invalidate_account_client", _fake_invalidate)
    monkeypatch.setattr(
        "app.modules.proxy.account_cache.get_account_selection_cache",
        lambda: _StubCache(),
    )

    await tracker_module_local._default_deactivation_callback("acc_target", count=3, window_seconds=60.0)

    assert len(update_calls) == 1
    assert update_calls[0]["account_id"] == "acc_target"
    assert update_calls[0]["status"] is AccountStatus.DEACTIVATED
    assert update_calls[0]["deactivation_reason"] == "proxy_unreachable"
    assert update_calls[0]["expected_status"] is AccountStatus.ACTIVE
    assert invalidate_calls == ["acc_target"]
    assert cache_calls == [None]


@pytest.mark.asyncio
async def test_get_default_tracker_singleton() -> None:
    set_default_tracker_for_test(None)
    first = tracker_module.get_default_tracker()
    second = tracker_module.get_default_tracker()
    try:
        assert first is second
    finally:
        set_default_tracker_for_test(None)


@pytest.mark.asyncio
async def test_tracker_coalesces_concurrent_callbacks_until_reset() -> None:
    """Once a deactivation callback is in flight, subsequent failures
    in the same window MUST NOT fire additional callbacks. The next
    fire is allowed only after ``reset_for_account`` clears the
    in-flight flag — which production code calls via
    ``invalidate_account_client``.
    """

    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=3, window_seconds=60.0, deactivation_callback=callback)

    # Cross the threshold the first time → callback fires once.
    for _ in range(3):
        await tracker.record_failure("acc", ProxyConnectionError("x"))
    assert callback.await_count == 1

    # Two more failures within the window. Without coalescing, both
    # would re-fire the callback. With coalescing, neither does.
    for _ in range(2):
        await tracker.record_failure("acc", ProxyConnectionError("x"))
    assert callback.await_count == 1

    # Reset (production calls this via invalidate_account_client) clears
    # the in-flight flag. The next threshold-crossing fires again.
    await tracker.reset_for_account("acc")
    for _ in range(3):
        await tracker.record_failure("acc", ProxyConnectionError("x"))
    assert callback.await_count == 2


@pytest.mark.asyncio
async def test_tracker_reset_all_for_test_clears_in_flight_flag() -> None:
    callback = AsyncMock()
    tracker = ProxyFailureTracker(threshold=1, window_seconds=60.0, deactivation_callback=callback)

    await tracker.record_failure("acc", ProxyConnectionError("x"))
    assert callback.await_count == 1

    await tracker.reset_all_for_test()
    await tracker.record_failure("acc", ProxyConnectionError("x"))

    assert callback.await_count == 2


@pytest.mark.asyncio
async def test_tracker_clears_in_flight_flag_on_callback_cancellation() -> None:
    """``asyncio.CancelledError`` inherits from ``BaseException`` (not
    ``Exception``) since Python 3.8. If the deactivation callback is
    cancelled mid-execution and the tracker only catches ``Exception``,
    the ``_deactivating`` flag is never cleared and EVERY future
    threshold-crossing for the same account_id is silently suppressed
    until process restart. This test asserts the tracker still clears
    the flag when the callback is cancelled.
    """

    cancel_event = asyncio.Event()

    async def slow_callback(account_id: str, *, count: int, window_seconds: float) -> None:
        # Block until the test cancels us.
        await cancel_event.wait()

    tracker = ProxyFailureTracker(
        threshold=3,
        window_seconds=60.0,
        deactivation_callback=slow_callback,
    )

    async def trip_threshold() -> None:
        for _ in range(3):
            await tracker.record_failure("acc", ProxyConnectionError("x"))

    # Start the tracker; it'll trip the threshold and start the slow
    # callback, which blocks waiting for cancel_event.
    task = asyncio.create_task(trip_threshold())
    # Yield enough times for the callback to be in flight.
    for _ in range(20):
        await asyncio.sleep(0)
    # Cancel the task; the slow_callback's ``await`` will raise
    # CancelledError up through the tracker.
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # The flag MUST have been cleared so a fresh threshold-crossing can
    # re-fire. (Pre-fix behaviour: flag stuck, callback suppression
    # forever.) We use a sentinel callback to verify the next crossing
    # fires.
    fired = asyncio.Event()

    async def sentinel_callback(account_id: str, *, count: int, window_seconds: float) -> None:
        fired.set()

    tracker._deactivation_callback = sentinel_callback  # type: ignore[attr-defined]
    for _ in range(3):
        await tracker.record_failure("acc", ProxyConnectionError("x"))
    assert fired.is_set(), "tracker did not fire deactivation after cancellation; flag stuck"

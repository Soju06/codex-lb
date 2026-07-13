from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.core.usage import live_hub
from app.core.usage.live_snapshots import (
    EVENT_MARKER,
    LiveRateLimitSnapshot,
    LiveUsageWindow,
    parse_rate_limit_event_text,
    parse_rate_limit_headers,
)
from app.modules.usage import live_ingest

pytestmark = pytest.mark.unit


def _snapshot(primary_used: float = 25.0, reset_at: int = 1_700_000_300) -> LiveRateLimitSnapshot:
    return LiveRateLimitSnapshot(
        primary=LiveUsageWindow(used_percent=primary_used, window_minutes=300, reset_at=reset_at),
        secondary=LiveUsageWindow(used_percent=40.0, window_minutes=10080, reset_at=reset_at + 3600),
        credits_has=True,
        credits_unlimited=False,
        credits_balance=12.5,
    )


def test_parse_rate_limit_headers_reads_both_windows_and_credits() -> None:
    headers = {
        "X-Codex-Primary-Used-Percent": "25.5",
        "X-Codex-Primary-Window-Minutes": "300",
        "X-Codex-Primary-Reset-At": "1700000300",
        "x-codex-secondary-used-percent": "40",
        "x-codex-secondary-window-minutes": "10080",
        "x-codex-secondary-reset-at": "1700003900",
        "x-codex-credits-has-credits": "true",
        "x-codex-credits-unlimited": "false",
        "x-codex-credits-balance": "12.50",
    }

    snapshot = parse_rate_limit_headers(headers)

    assert snapshot is not None
    assert snapshot.primary == LiveUsageWindow(used_percent=25.5, window_minutes=300, reset_at=1_700_000_300)
    assert snapshot.secondary == LiveUsageWindow(used_percent=40.0, window_minutes=10080, reset_at=1_700_003_900)
    assert snapshot.credits_has is True
    assert snapshot.credits_unlimited is False
    assert snapshot.credits_balance == pytest.approx(12.5)


def test_parse_rate_limit_headers_rejects_non_finite_values() -> None:
    # Non-finite numeric strings must degrade to unparseable fields, never
    # raise on the serving path.
    snapshot = parse_rate_limit_headers(
        {
            "x-codex-primary-used-percent": "25.0",
            "x-codex-primary-window-minutes": "NaN",
            "x-codex-primary-reset-at": "Infinity",
        }
    )
    assert snapshot is not None
    assert snapshot.primary == LiveUsageWindow(used_percent=25.0, window_minutes=None, reset_at=None)

    assert parse_rate_limit_headers({"x-codex-primary-used-percent": "NaN"}) is None


def test_parse_rate_limit_headers_without_windows_returns_none() -> None:
    assert parse_rate_limit_headers({"content-type": "text/event-stream"}) is None
    assert parse_rate_limit_headers({"x-codex-credits-balance": "5"}) is None
    assert parse_rate_limit_headers(None) is None


def test_parse_rate_limit_event_text_reads_sse_block() -> None:
    payload = {
        "type": "codex.rate_limits",
        "rate_limits": {
            "primary": {"used_percent": 61, "window_minutes": 300, "reset_at": 1_700_000_300},
            "secondary": {"used_percent": 12.5, "window_minutes": 10080, "resets_at": 1_700_003_900},
        },
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    }
    block = f"event: codex.rate_limits\ndata: {json.dumps(payload)}\n\n"

    snapshot = parse_rate_limit_event_text(block)

    assert snapshot is not None
    assert snapshot.primary == LiveUsageWindow(used_percent=61.0, window_minutes=300, reset_at=1_700_000_300)
    assert snapshot.secondary == LiveUsageWindow(used_percent=12.5, window_minutes=10080, reset_at=1_700_003_900)
    assert snapshot.credits_has is False


def test_parse_rate_limit_event_rejects_non_default_limit_families() -> None:
    base = {
        "type": "codex.rate_limits",
        "rate_limits": {"primary": {"used_percent": 55, "window_minutes": 300, "reset_at": 1_700_000_300}},
    }
    from app.core.usage.live_snapshots import parse_rate_limit_event

    assert parse_rate_limit_event({**base, "metered_limit_name": "gpt-5.2-codex-sonic"}) is None
    assert parse_rate_limit_event({**base, "limit_id": "codex_other"}) is None
    assert parse_rate_limit_event({**base, "limit_name": "gpt-gated"}) is None
    assert parse_rate_limit_event({**base, "limit_id": "codex"}) is not None
    assert parse_rate_limit_event(base) is not None


def test_parse_rate_limit_event_text_ignores_other_events_and_garbage() -> None:
    assert parse_rate_limit_event_text('data: {"type":"response.completed"}\n\n') is None
    assert parse_rate_limit_event_text('data: {"type":"codex.rate_limits","rate_limits":{}}\n\n') is None
    assert parse_rate_limit_event_text('data: {"type":"codex.rate_limits" broken\n\n') is None
    assert EVENT_MARKER not in 'data: {"type":"response.completed"}'


def test_live_hub_is_inert_until_registered() -> None:
    captured: list[Any] = []
    live_hub.register_live_usage_publisher(None)
    live_hub.publish_live_usage(_snapshot(), account_id="acc-1")

    live_hub.register_live_usage_publisher(
        lambda snapshot, *, account_id=None, chatgpt_account_id=None: captured.append(
            (snapshot, account_id, chatgpt_account_id)
        )
    )
    try:
        live_hub.publish_live_usage(_snapshot(), account_id="acc-1")
        live_hub.publish_live_usage(None, account_id="acc-1")
        live_hub.publish_live_usage(_snapshot(), account_id=None, chatgpt_account_id=None)
        windowless = LiveRateLimitSnapshot(
            primary=None,
            secondary=None,
            credits_has=True,
            credits_unlimited=None,
            credits_balance=None,
        )
        live_hub.publish_live_usage(windowless, account_id="acc-1")
    finally:
        live_hub.register_live_usage_publisher(None)

    assert len(captured) == 1
    assert captured[0][1] == "acc-1"


@pytest.mark.asyncio
async def test_ingestor_throttles_identical_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    ingestor = live_ingest.LiveUsageIngestor(queue_size=8, write_min_interval_seconds=60.0)
    writes: list[tuple[str, LiveRateLimitSnapshot]] = []

    async def fake_ingest(item: live_ingest._QueuedSnapshot) -> None:
        assert item.account_id is not None
        writes.append((item.account_id, item.snapshot))
        ingestor._last_write[item.account_id] = (live_ingest._fingerprint(item.snapshot), time.monotonic())

    monkeypatch.setattr(ingestor, "_ingest", fake_ingest)
    ingestor.start()
    try:
        ingestor.publish(_snapshot(), account_id="acc-throttle")
        await asyncio.sleep(0.05)
        # Identical snapshot inside the interval is coalesced.
        ingestor.publish(_snapshot(), account_id="acc-throttle")
        await asyncio.sleep(0.05)
        # A changed snapshot writes promptly despite the interval.
        ingestor.publish(_snapshot(primary_used=90.0), account_id="acc-throttle")
        await asyncio.sleep(0.05)
    finally:
        await ingestor.stop()

    assert [snap.primary.used_percent for _, snap in writes if snap.primary is not None] == [25.0, 90.0]


@pytest.mark.asyncio
async def test_ingestor_queue_overflow_drops_oldest() -> None:
    ingestor = live_ingest.LiveUsageIngestor(queue_size=2, write_min_interval_seconds=60.0)
    # Consumer not started: queue fills up.
    ingestor.publish(_snapshot(primary_used=1.0), account_id="acc-a")
    ingestor.publish(_snapshot(primary_used=2.0), account_id="acc-b")
    ingestor.publish(_snapshot(primary_used=3.0), account_id="acc-c")

    remaining = []
    while True:
        try:
            remaining.append(ingestor._queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    assert [item.snapshot.primary.used_percent for item in remaining if item.snapshot.primary is not None] == [
        2.0,
        3.0,
    ]


@pytest.mark.asyncio
async def test_stream_responses_tap_publishes_rate_limit_events(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.clients.proxy as proxy_client_module

    payload = {
        "type": "codex.rate_limits",
        "rate_limits": {"primary": {"used_percent": 55, "window_minutes": 300, "reset_at": 1_700_000_300}},
    }
    blocks = [
        'data: {"type":"response.created","response":{"id":"resp_live"}}\n\n',
        f"data: {json.dumps(payload)}\n\n",
        'data: {"type":"response.completed","response":{"id":"resp_live"}}\n\n',
    ]

    async def fake_stream(**kwargs: Any):
        for block in blocks:
            yield block

    monkeypatch.setattr(proxy_client_module, "_stream_responses_with_session", lambda **kwargs: fake_stream(**kwargs))

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_lease(session: Any = None):
        yield session

    monkeypatch.setattr(proxy_client_module, "lease_http_session", fake_lease)

    captured: list[tuple[Any, str | None, str | None]] = []
    live_hub.register_live_usage_publisher(
        lambda snapshot, *, account_id=None, chatgpt_account_id=None: captured.append(
            (snapshot, account_id, chatgpt_account_id)
        )
    )
    try:
        request = proxy_client_module.ResponsesRequest.model_validate(
            {"model": "gpt-5.1", "instructions": "hi", "input": "live", "stream": True}
        )
        seen = [
            block
            async for block in proxy_client_module.stream_responses(
                request,
                {},
                "access-token",
                "workspace-live",
            )
        ]
        seen_internal = [
            block
            async for block in proxy_client_module.stream_responses(
                request,
                {},
                "access-token",
                "workspace-live",
                codex_lb_account_id="acc-internal",
            )
        ]
    finally:
        live_hub.register_live_usage_publisher(None)

    live_hub.register_live_usage_publisher(
        lambda snapshot, *, account_id=None, chatgpt_account_id=None: captured.append(
            (snapshot, account_id, chatgpt_account_id)
        )
    )
    try:
        suppressed = [
            block
            async for block in proxy_client_module.stream_responses(
                request,
                {},
                "access-token",
                "workspace-live",
                codex_lb_account_id="acc-internal",
                suppress_live_usage=True,
            )
        ]
    finally:
        live_hub.register_live_usage_publisher(None)

    assert seen == blocks
    assert seen_internal == blocks
    # Suppressed callers (e.g. quota-planner probes) produce no publishes.
    assert suppressed == blocks
    assert len(captured) == 2
    snapshot, account_id, chatgpt_account_id = captured[0]
    assert (account_id, chatgpt_account_id) == (None, "workspace-live")
    assert snapshot.primary is not None
    assert snapshot.primary.used_percent == pytest.approx(55.0)
    # When the caller knows the selected internal account, attribution
    # prefers it so multi-seat workspaces are not dropped as ambiguous.
    _, account_id_internal, chatgpt_internal = captured[1]
    assert (account_id_internal, chatgpt_internal) == ("acc-internal", None)

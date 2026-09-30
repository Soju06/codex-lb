import json
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from fastapi import WebSocket

from app.core.clients.proxy import ProxyResponseError
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
from app.modules.api_keys.usage_share import UsageShareEstimate
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service import api_key_usage as api_key_usage_module
from app.modules.proxy._service.websocket import mixin as websocket_mixin_module
from tests.simulation.virtual_time import VirtualClock


def _estimate(*, used: float, allowance: float) -> UsageShareEstimate:
    return UsageShareEstimate(
        configured_percent=20,
        estimated_used_credits=used,
        allowance_credits=allowance,
        capacity_credits=100.0,
        reset_at=1_800_000_000,
        account_count=2,
    )


def _api_key(
    *,
    estimate: UsageShareEstimate | None = None,
    unavailable: tuple[str, ...] = (),
) -> ApiKeyData:
    return ApiKeyData(
        id="key-share",
        name="share",
        key_prefix="sk-share",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 18),
        last_used_at=None,
        usage_share_percent=20,
        usage_share_estimate=estimate,
        usage_share_unavailable_account_ids=unavailable,
    )


def _service() -> proxy_service.ProxyService:
    return object.__new__(proxy_service.ProxyService)


def test_usage_share_denies_at_the_allocation_boundary() -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        _service()._enforce_api_key_usage_share(
            _api_key(estimate=_estimate(used=20, allowance=20)),
            "request-share",
            "responses",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.payload["error"]["code"] == "api_key_usage_share_limit_reached"
    assert exc_info.value.payload["error"]["resets_at"] == 1_800_000_000
    assert exc_info.value.local_pre_dispatch_refusal


def test_expired_usage_share_snapshot_fails_open_at_admission() -> None:
    service = _service()
    service._clock = VirtualClock(epoch_value=1_800_000_001)

    service._enforce_api_key_usage_share(
        _api_key(estimate=_estimate(used=20, allowance=20)),
        "request-share",
        "responses",
    )


def test_usage_share_allows_below_the_allocation() -> None:
    _service()._enforce_api_key_usage_share(
        _api_key(estimate=_estimate(used=19.99, allowance=20)),
        "request-share",
        "responses",
    )


def test_incomplete_evidence_fails_open_and_requests_only_the_first_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed: list[str] = []
    unavailable = tuple(f"account-{index:04d}" for index in range(1_100))
    monkeypatch.setattr(
        api_key_usage_module,
        "_request_usage_refresh",
        lambda _service, account_id: refreshed.append(account_id),
    )

    _service()._enforce_api_key_usage_share(
        _api_key(unavailable=unavailable),
        "request-share",
        "responses",
    )

    assert refreshed == [unavailable[0]]


def test_incomplete_evidence_stays_fail_open_when_refresh_scheduling_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh() -> None:
        return None

    refresh_coro = refresh()
    service = _service()
    service._schedule_cancel_safe_cleanup = Mock(side_effect=RuntimeError("scheduler unavailable"))
    monkeypatch.setattr(
        api_key_usage_module.UsageUpdater,
        "request_refresh",
        staticmethod(lambda _account_id: refresh_coro),
    )

    service._enforce_api_key_usage_share(
        _api_key(unavailable=("account-stale",)),
        "request-share",
        "responses",
    )

    assert refresh_coro.cr_frame is None


@pytest.mark.asyncio
async def test_websocket_connect_refusal_releases_the_provisional_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    api_key = _api_key(estimate=_estimate(used=25, allowance=20))
    reservation = ApiKeyUsageReservationData(
        reservation_id="reservation-websocket-share",
        key_id=api_key.id,
        model="gpt-5.4",
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="request-websocket-share",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=reservation,
        started_at=0.0,
        api_key=api_key,
    )
    release_reservation = AsyncMock()
    write_failure = AsyncMock()
    select_account = AsyncMock(side_effect=AssertionError("refusal must precede account selection"))
    send_text = AsyncMock()
    settings = proxy_service.get_settings()

    class SettingsCache:
        async def get(self):
            return settings

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(
        websocket_mixin_module,
        "responses_model_is_source_owned",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release_reservation)
    monkeypatch.setattr(service, "_write_websocket_connect_failure", write_failure)
    monkeypatch.setattr(service, "_select_websocket_connect_account", select_account)

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="gpt-5.4",
        request_state=request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace(send_text=send_text)),
    )

    assert account is None
    assert upstream is None
    select_account.assert_not_awaited()
    release_reservation.assert_awaited_once_with(request_state)
    write_failure.assert_awaited_once()
    send_args = send_text.await_args
    assert send_args is not None
    sent = json.loads(send_args.args[0])
    assert sent["status"] == 429
    assert sent["error"]["code"] == "api_key_usage_share_limit_reached"
    assert sent["error"]["resets_at"] == 1_800_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_stage", "usage_share_admitted"),
    [
        pytest.param("reattach", False, id="transparent-reattach"),
        pytest.param("first_turn", True, id="reuse-to-reconnect-race"),
    ],
)
async def test_websocket_connect_does_not_repeat_usage_share_admission(
    monkeypatch: pytest.MonkeyPatch,
    request_stage: str,
    usage_share_admitted: bool,
) -> None:
    service = _service()
    api_key = _api_key(estimate=_estimate(used=25, allowance=20))
    request_state = proxy_service._WebSocketRequestState(
        request_id="request-websocket-existing-admission",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        request_stage=request_stage,
        usage_share_admitted=usage_share_admitted,
        api_key=api_key,
    )
    guard = Mock(side_effect=AssertionError("the turn already owns its usage-share admission"))
    select_account = AsyncMock(return_value=None)
    release_account_lease = AsyncMock()
    settings = proxy_service.get_settings()

    class SettingsCache:
        async def get(self):
            return settings

    service._load_balancer = SimpleNamespace(release_account_lease=release_account_lease)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(
        websocket_mixin_module,
        "responses_model_is_source_owned",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(service, "_enforce_api_key_usage_share", guard)
    monkeypatch.setattr(service, "_select_websocket_connect_account", select_account)

    account, upstream = await service._connect_proxy_websocket(
        {},
        sticky_key=None,
        sticky_kind=None,
        prefer_earlier_reset=False,
        routing_strategy="capacity_weighted",
        model="gpt-5.4",
        request_state=request_state,
        api_key=api_key,
        client_send_lock=anyio.Lock(),
        websocket=cast(WebSocket, SimpleNamespace(send_text=AsyncMock())),
    )

    assert account is None
    assert upstream is None
    guard.assert_not_called()
    select_account.assert_awaited_once()
    release_account_lease.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_reused_websocket_usage_share_refusal_is_not_attributed_to_open_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.test_proxy_utils import (
        _make_account,
        _make_proxy_settings,
        _repo_factory,
        _RequestLogsRecorder,
        _SettingsCache,
    )
    from tests.unit.test_proxy_websocket_model_source_guard import (
        _completed_turn,
        _create_frame,
        _Downstream,
        _TurnDrivenUpstream,
    )

    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    request_logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(request_logs))
    account = _make_account("account-websocket-share-reuse")
    upstream = _TurnDrivenUpstream([_completed_turn("response-share-allowed")])
    allowed_key = _api_key(estimate=_estimate(used=19, allowance=20))
    blocked_key = _api_key(estimate=_estimate(used=20, allowance=20))
    policies = iter((allowed_key, blocked_key))

    async def refresh_policy(_api_key: ApiKeyData | None) -> ApiKeyData:
        return next(policies)

    async def connect(
        proxy,
        _headers,
        **kwargs,
    ):
        request_state = kwargs["request_state"]
        api_key = request_state.api_key or kwargs["api_key"]
        websocket_mixin_module._admit_websocket_usage_share(proxy, request_state, api_key)
        return account, upstream

    monkeypatch.setattr(
        websocket_mixin_module,
        "responses_model_is_source_owned",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", refresh_policy)
    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)

    downstream = _Downstream([_create_frame("gpt-5.4"), _create_frame("gpt-5.4")])
    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=allowed_key,
    )
    assert await service.drain_persistence_tasks(timeout_seconds=1)

    refusal = next(call for call in request_logs.calls if call.get("error_code") == "api_key_usage_share_limit_reached")
    assert refusal["account_id"] is None
    assert len(upstream.sent_text) == 1

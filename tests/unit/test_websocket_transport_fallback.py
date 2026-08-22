"""Tests for the websocket-outage HTTP transport fallback.

codex-rs activates its session-scoped HTTP transport fallback only when the
websocket *handshake* is rejected with HTTP 426 (``StatusCode::UPGRADE_REQUIRED``
on ``websocket_connection`` in ``core/src/client.rs``); in-band 5xx error
events never trigger it. Keeping a websocket-only upstream outage survivable
therefore takes three cooperating behaviors:

* the connect failover decision surfaces a connect-phase transient transport
  failure without recording an account penalty, so hard-affinity selection
  stays available for the client's HTTP retry — while account-scoped
  failures that share the ``upstream_unavailable`` envelope (for example
  OAuth refresh transport errors) keep the classify-penalize-failover path;
* the same failure arms a short-lived transport-failure marker that the
  responses websocket routes turn into an HTTP 426 handshake denial and the
  HTTP paths turn into a pinned HTTP upstream transport;
* the HTTP responses bridge bypasses or falls back to raw HTTP when the
  upstream websocket session cannot be established, replaying only failures
  that carry pre-submit session-creation provenance.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy._service.http_bridge.streaming as http_bridge_streaming_module
import app.modules.proxy._service.streaming.transport_health as transport_health
import app.modules.proxy._service.websocket.mixin as ws_mixin
import app.modules.proxy.api as proxy_api_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


def _proxy_error(
    status: int,
    code: str,
    message: str,
    *,
    failure_phase: str | None = None,
) -> ProxyResponseError:
    return ProxyResponseError(
        status,
        openai_error(code, message, error_type="server_error"),
        failure_phase=failure_phase,
    )


class _DecisionHarness(ws_mixin._WebSocketMixin):
    def __init__(self) -> None:
        self.penalty_calls: list[tuple[str, ProxyResponseError]] = []

    async def _handle_websocket_connect_error(self, account: Any, exc: ProxyResponseError) -> dict[str, str]:
        self.penalty_calls.append((account.id, exc))
        return {"failure_class": "retryable_transient"}


def _request_state() -> Any:
    return SimpleNamespace(request_log_id="req-transport-fallback", request_id="req-transport-fallback")


def _account() -> Any:
    return SimpleNamespace(id="acct-transport-fallback")


async def _decide(harness: _DecisionHarness, exc: ProxyResponseError) -> str:
    return await harness._decide_websocket_failover_action(
        account=_account(),
        exc=exc,
        request_state=_request_state(),
        attempt=1,
        max_attempts=2,
        deterministic_failover_enabled=True,
    )


@pytest.fixture(autouse=True)
def _reset_transport_failure_marker() -> Any:
    transport_health.clear_upstream_websocket_transport_failure()
    yield
    transport_health.clear_upstream_websocket_transport_failure()


@pytest.mark.asyncio
async def test_transient_connect_timeout_surfaces_without_penalty_and_arms_marker() -> None:
    harness = _DecisionHarness()

    action = await _decide(
        harness,
        _proxy_error(502, "upstream_unavailable", "Request to upstream timed out", failure_phase="connect"),
    )

    assert action == "surface"
    assert harness.penalty_calls == []
    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_server_level_handshake_failure_surfaces_without_penalty() -> None:
    harness = _DecisionHarness()

    action = await _decide(
        harness,
        _proxy_error(
            503,
            "upstream_websocket_handshake_failed",
            "Upstream websocket handshake failed with HTTP 503",
            failure_phase="connect",
        ),
    )

    assert action == "surface"
    assert harness.penalty_calls == []
    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_refresh_transport_failure_keeps_penalized_failover_path() -> None:
    # An OAuth refresh transport error is converted to a 502
    # ``upstream_unavailable`` so the connect loop applies its normal
    # account-health handling; it carries no connect failure phase and must
    # not surface, skip the penalty, or arm the instance-wide marker.
    harness = _DecisionHarness()

    action = await _decide(harness, _proxy_error(502, "upstream_unavailable", "token refresh transport error"))

    assert action == "failover_next"
    assert len(harness.penalty_calls) == 1
    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_account_scoped_connect_failure_keeps_penalized_failover_path() -> None:
    harness = _DecisionHarness()

    action = await _decide(harness, _proxy_error(401, "invalid_api_key", "bad token", failure_phase="connect"))

    assert action == "failover_next"
    assert len(harness.penalty_calls) == 1
    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_sub_5xx_transient_failure_keeps_penalized_failover_path() -> None:
    harness = _DecisionHarness()

    await _decide(harness, _proxy_error(429, "upstream_unavailable", "slow down", failure_phase="connect"))

    assert len(harness.penalty_calls) == 1
    assert transport_health.upstream_websocket_transport_recently_failed() is False


def test_transport_failure_marker_expires_and_clears() -> None:
    transport_health.mark_upstream_websocket_transport_failure()
    assert transport_health.upstream_websocket_transport_recently_failed() is True

    transport_health._upstream_ws_transport_failure_at = (
        time.monotonic() - transport_health.UPSTREAM_WS_TRANSPORT_FAILURE_TTL_SECONDS - 1.0
    )
    assert transport_health.upstream_websocket_transport_recently_failed() is False

    transport_health.mark_upstream_websocket_transport_failure()
    transport_health.clear_upstream_websocket_transport_failure()
    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_budget_exhaustion_during_websocket_open_arms_marker() -> None:
    # When the request budget expires while the websocket open is stalled,
    # the budget-exhausted emit bypasses the failover decision, so the
    # budgeted opener itself must arm the handshake-denial marker.
    class _StalledOpenHarness(ws_mixin._WebSocketMixin):
        async def _open_upstream_websocket(self, account: Any, headers: Any, *, request_state: Any = None) -> Any:
            del account, headers, request_state
            await asyncio.sleep(5.0)

    harness = _StalledOpenHarness()

    with pytest.raises(ProxyResponseError):
        await harness._open_upstream_websocket_with_budget(
            _account(),
            {},
            timeout_seconds=0.05,
        )

    assert transport_health.upstream_websocket_transport_recently_failed() is True


def _patch_transport_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dashboard_transport: str,
    base_transport: str = "auto",
) -> None:
    monkeypatch.setattr(
        proxy_api_module,
        "get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(upstream_stream_transport=dashboard_transport))
        ),
    )
    monkeypatch.setattr(
        proxy_api_module,
        "get_settings",
        lambda: SimpleNamespace(upstream_stream_transport=base_transport),
    )


@pytest.mark.asyncio
async def test_websocket_route_denies_handshake_with_426_while_marker_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport_settings(monkeypatch, dashboard_transport="default")
    transport_health.mark_upstream_websocket_transport_failure()

    denial = await proxy_api_module._websocket_upstream_transport_denial()

    assert denial is not None
    assert denial.status_code == 426


@pytest.mark.asyncio
async def test_websocket_route_accepts_handshake_when_marker_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport_settings(monkeypatch, dashboard_transport="default")

    assert await proxy_api_module._websocket_upstream_transport_denial() is None


@pytest.mark.asyncio
async def test_websocket_route_denies_handshake_when_upstream_transport_pinned_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport_settings(monkeypatch, dashboard_transport="http")

    denial = await proxy_api_module._websocket_upstream_transport_denial()

    assert denial is not None
    assert denial.status_code == 426


def _bridge_runtime_config() -> Any:
    from app.modules.proxy._service.http_bridge.helpers import _HTTPBridgeRuntimeConfig

    return _HTTPBridgeRuntimeConfig(
        enabled=True,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=1800.0,
        max_sessions=8,
        queue_limit=4,
        prompt_cache_idle_ttl_seconds=120.0,
        gateway_safe_mode=False,
    )


def _bridge_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dashboard_transport: str,
) -> Any:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_service_get_settings_cache",
        lambda: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(upstream_stream_transport=dashboard_transport))
        ),
    )
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_service_get_settings",
        lambda: SimpleNamespace(upstream_stream_transport="auto"),
    )
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_http_bridge_runtime_config",
        lambda *_args: _bridge_runtime_config(),
    )
    monkeypatch.setattr(
        service,
        "_resolve_forwarded_file_account_for_responses",
        AsyncMock(return_value=None),
    )
    return service


def _bridge_payload() -> Any:
    return proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.6-sol", "instructions": "test", "input": "hello"}
    )


def _pre_submit_error() -> ProxyResponseError:
    exc = _proxy_error(502, "upstream_unavailable", "Request to upstream timed out")
    setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
    return exc


async def _collect_bridge_stream(service: Any, *, api_key_reservation: Any = None) -> list[str]:
    return [
        chunk
        async for chunk in service._stream_http_bridge_or_retry(
            _bridge_payload(),
            {},
            codex_session_affinity=True,
            propagate_http_errors=True,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=False,
        )
    ]


@pytest.mark.asyncio
async def test_http_bridge_bypassed_when_upstream_transport_pinned_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="http")
    retry_calls: list[dict[str, Any]] = []

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    async def bridge_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("bridge must be bypassed when upstream transport is pinned to http")
        yield ""

    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)
    monkeypatch.setattr(service, "_stream_via_http_bridge", bridge_must_not_run)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert len(retry_calls) == 1
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"


@pytest.mark.asyncio
async def test_http_bridge_bypassed_while_transport_failure_marker_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After the 426 denial moves a Codex session to the downstream HTTP
    # route, the bridged and raw paths must not resolve back to the
    # unavailable websocket upstream while the marker is armed.
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    transport_health.mark_upstream_websocket_transport_failure()
    retry_calls: list[dict[str, Any]] = []

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    async def bridge_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("bridge must be bypassed while the transport-failure marker is armed")
        yield ""

    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)
    monkeypatch.setattr(service, "_stream_via_http_bridge", bridge_must_not_run)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert len(retry_calls) == 1
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"


@pytest.mark.asyncio
async def test_http_bridge_falls_back_to_http_on_pre_submit_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    retry_calls: list[dict[str, Any]] = []

    async def failing_bridge(*_args: object, **_kwargs: object):
        raise _pre_submit_error()
        yield ""

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert len(retry_calls) == 1
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"


@pytest.mark.asyncio
async def test_http_bridge_post_submit_transient_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ``upstream_unavailable`` raised after the turn may already have
    # dispatched upstream carries no pre-submit provenance; replaying it
    # over raw HTTP could run the same turn twice.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def failing_bridge_post_submit(*_args: object, **_kwargs: object):
        raise _proxy_error(502, "upstream_unavailable", "Request to upstream timed out")
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay a failure without pre-submit provenance")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge_post_submit)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)


@pytest.mark.asyncio
async def test_http_bridge_transient_failure_propagates_after_lines_streamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def failing_bridge_mid_stream(*_args: object, **_kwargs: object):
        yield 'data: {"type":"response.created"}\n\n'
        raise _pre_submit_error()

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay a partially streamed response")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge_mid_stream)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)


@pytest.mark.asyncio
async def test_http_bridge_transient_failure_propagates_for_api_key_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="resv-transport-fallback",
        key_id="key-transport-fallback",
        model="gpt-5.6-sol",
    )

    async def failing_bridge(*_args: object, **_kwargs: object):
        raise _pre_submit_error()
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not run while an API-key reservation is unsettled")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service, api_key_reservation=reservation)


@pytest.mark.asyncio
async def test_http_bridge_non_transient_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def failing_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(400, "invalid_request_error", "Invalid request payload")
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not swallow non-transient failures")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)

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
import ssl
import time
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import app.core.clients.proxy_websocket as proxy_websocket_module
import app.modules.proxy._service.http_bridge.streaming as http_bridge_streaming_module
import app.modules.proxy._service.support as transport_health
import app.modules.proxy._service.websocket.mixin as ws_mixin
import app.modules.proxy.api as proxy_api_module
from app.core.balancer.types import ClassifiedFailure
from app.core.clients.codex import CodexTransportError
from app.core.clients.proxy import ProxyResponseError, is_confirmed_pre_dispatch_transport_error
from app.core.clients.proxy_websocket import (
    UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
    connect_responses_websocket,
)
from app.core.config.settings import Settings as AppSettings
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.db.models import Account, HttpBridgeSessionState
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.websocket.mixin import _WebSocketMixin

pytestmark = pytest.mark.unit


def _proxy_error(
    status: int,
    code: str,
    message: str,
    *,
    failure_phase: str | None = None,
    failure_detail: str | None = None,
) -> ProxyResponseError:
    return ProxyResponseError(
        status,
        openai_error(code, message, error_type="server_error"),
        failure_phase=failure_phase,
        failure_detail=failure_detail,
    )


def _transport_error(status: int, code: str, message: str) -> ProxyResponseError:
    """A connect failure carrying the direct open's transport provenance."""

    return _proxy_error(
        status,
        code,
        message,
        failure_phase="connect",
        failure_detail=UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
    )


class _DecisionHarness(ws_mixin._WebSocketMixin):
    def __init__(self) -> None:
        self.penalty_calls: list[tuple[str, ProxyResponseError]] = []

    async def _handle_websocket_connect_error(self, account: Account, exc: ProxyResponseError) -> ClassifiedFailure:
        self.penalty_calls.append((account.id, exc))
        return cast(ClassifiedFailure, {"failure_class": "retryable_transient"})


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
        _transport_error(502, "upstream_unavailable", "Request to upstream timed out"),
    )

    assert action == "surface"
    assert harness.penalty_calls == []
    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_server_level_handshake_failure_surfaces_without_penalty() -> None:
    harness = _DecisionHarness()

    action = await _decide(
        harness,
        _transport_error(
            503,
            "upstream_websocket_handshake_failed",
            "Upstream websocket handshake failed with HTTP 503",
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


def test_connect_transport_failure_classifier_provenance() -> None:
    # The failover decision, the forced-surface replacement branch and the
    # bridge fallback all share this classifier, so its provenance gates
    # decide everywhere the handshake-denial marker can arm.
    qualifies = transport_health.websocket_connect_transport_failure_code(
        _transport_error(502, "upstream_unavailable", "Request to upstream timed out"),
        confirmed_pre_dispatch=False,
    )
    assert qualifies == "upstream_unavailable"

    no_connect_phase = transport_health.websocket_connect_transport_failure_code(
        _proxy_error(502, "upstream_unavailable", "token refresh transport error"),
        confirmed_pre_dispatch=False,
    )
    assert no_connect_phase is None

    pre_dispatch = transport_health.websocket_connect_transport_failure_code(
        _transport_error(502, "upstream_unavailable", "proxy route connect failed"),
        confirmed_pre_dispatch=True,
    )
    assert pre_dispatch is None

    # An account-, route-, or configuration-scoped connect failure reuses the
    # same envelope but carries no transport provenance.
    no_provenance = transport_health.websocket_connect_transport_failure_code(
        _proxy_error(502, "upstream_unavailable", "routed handshake failed", failure_phase="connect"),
        confirmed_pre_dispatch=False,
    )
    assert no_provenance is None


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
        async def _open_upstream_websocket(
            self, account: Any, headers: Any, *, request_state: Any = None, connect_progress: Any = None
        ) -> Any:
            del account, headers, request_state
            if connect_progress is not None:
                connect_progress.direct_upstream_connect_started = True
            await asyncio.sleep(5.0)

    harness = _StalledOpenHarness()

    with pytest.raises(ProxyResponseError):
        await harness._open_upstream_websocket_with_budget(
            _account(),
            {},
            timeout_seconds=0.05,
        )

    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_process_network_recovery_exhaustion_during_open_arms_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A connect-phase transport failure that waits for recovery until the
    # budget runs out reaches the same budget-exhausted emit and must arm
    # the marker like the stalled-open branch.
    class _ConnectFailingOpenHarness(ws_mixin._WebSocketMixin):
        async def _open_upstream_websocket(
            self, account: Any, headers: Any, *, request_state: Any = None, connect_progress: Any = None
        ) -> Any:
            del account, headers, request_state
            raise _transport_error(502, "upstream_unavailable", "Request to upstream timed out")

    monkeypatch.setattr(ws_mixin, "_wait_for_process_network_recovery", AsyncMock(return_value="exhausted"))
    harness = _ConnectFailingOpenHarness()

    with pytest.raises(ProxyResponseError):
        await harness._open_upstream_websocket_with_budget(
            _account(),
            {},
            timeout_seconds=0.5,
        )

    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_exhausted_route_resolution_failure_does_not_arm_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The budgeted opener also runs route resolution; its
    # ``upstream_proxy_unavailable`` failures are pre-dispatch route
    # evidence without connect provenance and must not deny handshakes.
    class _RouteFailingOpenHarness(ws_mixin._WebSocketMixin):
        async def _open_upstream_websocket(
            self, account: Any, headers: Any, *, request_state: Any = None, connect_progress: Any = None
        ) -> Any:
            del account, headers, request_state
            raise _proxy_error(
                502,
                "upstream_proxy_unavailable",
                "Unable to resolve upstream proxy route for websocket request",
            )

    monkeypatch.setattr(ws_mixin, "_wait_for_process_network_recovery", AsyncMock(return_value="exhausted"))
    harness = _RouteFailingOpenHarness()

    with pytest.raises(ProxyResponseError):
        await harness._open_upstream_websocket_with_budget(
            _account(),
            {},
            timeout_seconds=0.5,
        )

    assert transport_health.upstream_websocket_transport_recently_failed() is False


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
    exc = _transport_error(502, "upstream_unavailable", "Request to upstream timed out")
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
async def test_http_bridge_refresh_provenance_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An exhausted token-refresh loop surfaces the same pre-submit 502
    # ``upstream_unavailable`` envelope, but without connect provenance it is
    # account evidence: a raw-HTTP replay re-runs the same failing refresh
    # and buries the actionable error under ``no_accounts``.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def refresh_failing_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(502, "upstream_unavailable", "temporary refresh failure")
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay an account-scoped refresh failure")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", refresh_failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)


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


# --- Connect-site provenance, through the real client conversion ------------
#
# The sanitized error code cannot carry transport provenance in either
# direction, so these exercise the actual `_connect_upstream_websocket`
# conversion rather than hand-built envelopes.


def _connect_settings() -> Any:
    return SimpleNamespace(
        upstream_base_url="https://chatgpt.com/backend-api",
        upstream_connect_timeout_seconds=7.0,
        proxy_downstream_websocket_idle_timeout_seconds=120.0,
        max_sse_event_bytes=4321,
        upstream_websocket_trust_env=False,
    )


async def _direct_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> ProxyResponseError:
    async def fake_websocket_connect(url: str, **kwargs: Any) -> Any:
        del url, kwargs
        raise failure

    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(proxy_websocket_module, "get_settings", _connect_settings)

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )
    return exc_info.value


async def _routed_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: CodexTransportError,
) -> ProxyResponseError:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )

    class _FailingCodexClient:
        async def open_ws_with_route_metadata(self, url: str, **kwargs: Any) -> Any:
            del url, kwargs
            raise failure

        async def close(self) -> None:
            return None

    monkeypatch.setattr(proxy_websocket_module, "get_settings", _connect_settings)

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            route=route,
            codex_client=cast(Any, _FailingCodexClient()),
        )
    return exc_info.value


def _classify(exc: ProxyResponseError) -> str | None:
    return transport_health.websocket_connect_transport_failure_code(
        exc,
        confirmed_pre_dispatch=is_confirmed_pre_dispatch_transport_error(exc),
    )


@pytest.mark.asyncio
async def test_direct_5xx_handshake_is_classified_as_transport_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The responses policy preserves the upstream handshake body, so a bare
    # 503 upgrade rejection surfaces as ``upstream_error`` rather than either
    # websocket-specific code. Provenance, not the code, must classify it, or
    # the common direct outage never steers Codex clients onto HTTP.
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(Response(503, "Service Unavailable", Headers({}), b"upstream is down")),
    )

    assert exc.status_code == 503
    assert exc.payload["error"].get("code") == "upstream_error"
    assert exc.failure_detail == UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
    assert _classify(exc) == "upstream_error"


@pytest.mark.asyncio
async def test_direct_credential_handshake_rejection_stays_account_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(Response(401, "Unauthorized", Headers({}), b"")),
    )

    assert exc.failure_detail is None
    assert _classify(exc) is None


@pytest.mark.asyncio
async def test_direct_edge_challenge_handshake_is_classified_as_transport_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 403 carrying explicit Cloudflare edge-challenge evidence is the edge
    # refusing the websocket upgrade itself, not account evidence: it must
    # share the transport-failure provenance so the failover decision
    # surfaces it without an account penalty, arms the 426 handshake-denial
    # marker, and the HTTP paths pin the upstream transport to HTTP.
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers({"Content-Type": "text/html; charset=UTF-8", "cf-mitigated": "challenge"}),
                b"<html><title>Just a moment...</title></html>",
            )
        ),
    )

    assert exc.status_code == 403
    assert exc.failure_detail == UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
    assert _classify(exc) is not None


@pytest.mark.asyncio
async def test_direct_unmarked_403_html_handshake_stays_account_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ordinary reverse-proxy HTML 403 carries no challenge evidence, so it
    # keeps the fail-closed classify-penalize-failover path: treating every
    # 403 as a transport outage would let one misconfigured deny rule push
    # the whole instance onto HTTP.
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers({"Content-Type": "text/html", "Server": "nginx"}),
                b"<html><h1>403 Forbidden</h1></html>",
            )
        ),
    )

    assert exc.failure_detail is None
    assert _classify(exc) is None


def _headers_with_duplicate_cookies(pairs: dict[str, str]) -> Headers:
    headers = Headers()
    # Real Cloudflare challenge responses set multiple cookies (__cf_bm,
    # _cfuvid); ``Headers.items()`` raises ``MultipleValuesError`` for any
    # repeated name, so these fixtures guard the duplicate-safe accessor.
    headers["Set-Cookie"] = "__cf_bm=fixture; Path=/; HttpOnly"
    headers["Set-Cookie"] = "_cfuvid=fixture; Path=/; HttpOnly"
    for key, value in pairs.items():
        headers[key] = value
    return headers


@pytest.mark.asyncio
async def test_direct_edge_challenge_with_duplicate_cookies_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The flagship input: an actual Cloudflare challenge carries repeated
    # Set-Cookie headers. Classification must not choke on the duplicates.
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(
            Response(
                403,
                "Forbidden",
                _headers_with_duplicate_cookies(
                    {"Content-Type": "text/html; charset=UTF-8", "cf-mitigated": "challenge"}
                ),
                b"<html><title>Just a moment...</title></html>",
            )
        ),
    )

    assert exc.status_code == 403
    assert exc.failure_detail == UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
    assert _classify(exc) is not None


@pytest.mark.asyncio
async def test_direct_unmarked_403_with_duplicate_headers_stays_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ordinary 403 with a duplicated header must keep raising the
    # sanitized ProxyResponseError account-evidence path — never escape as an
    # unhandled MultipleValuesError from the challenge classifier.
    exc = await _direct_connect_failure(
        monkeypatch,
        InvalidStatus(
            Response(
                403,
                "Forbidden",
                _headers_with_duplicate_cookies({"Content-Type": "text/html", "Server": "nginx"}),
                b"<html><h1>403 Forbidden</h1></html>",
            )
        ),
    )

    assert exc.status_code == 403
    assert exc.failure_detail is None
    assert _classify(exc) is None


@pytest.mark.asyncio
async def test_edge_challenge_connect_failure_surfaces_without_penalty() -> None:
    # Product path for the challenge recovery: the classified 403 rides the
    # same decision as a direct 5xx handshake rejection — surface to the
    # client without an account penalty and arm the marker so the next
    # handshake is denied with 426 and Codex switches to HTTP transport.
    harness = _DecisionHarness()

    action = await _decide(
        harness,
        _transport_error(403, "upstream_error", "<html><title>Just a moment...</title></html>"),
    )

    assert action == "surface"
    assert harness.penalty_calls == []
    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_direct_connect_timeout_is_classified_as_transport_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = await _direct_connect_failure(monkeypatch, asyncio.TimeoutError())

    assert exc.failure_detail == UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
    assert _classify(exc) == "upstream_unavailable"


@pytest.mark.asyncio
async def test_direct_tls_verification_failure_stays_out_of_transport_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A certificate failure is stable endpoint configuration: retrying it over
    # raw HTTP hits the same invalid TLS configuration, so it must not deny
    # handshakes with 426.
    exc = await _direct_connect_failure(
        monkeypatch,
        ssl.SSLCertVerificationError("certificate verify failed"),
    )

    assert exc.failure_detail is None
    assert _classify(exc) is None


@pytest.mark.asyncio
async def test_routed_5xx_handshake_stays_in_account_failover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One account's exhausted proxy route proves nothing about the websocket
    # transport other accounts reach. Classifying it as a global outage would
    # skip healthy-account selection and force unrelated clients onto HTTP.
    exc = await _routed_connect_failure(
        monkeypatch,
        CodexTransportError(
            "Codex upstream websocket failed via proxy endpoint ep_1: HTTP 503",
            status_code=503,
        ),
    )

    assert exc.failure_phase == "connect"
    assert exc.payload["error"].get("code") == "upstream_unavailable"
    assert is_confirmed_pre_dispatch_transport_error(exc) is False
    assert _classify(exc) is None


@pytest.mark.asyncio
async def test_routed_edge_challenge_handshake_stays_out_of_instance_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A challenge on one account's routed egress IP proves nothing about the
    # direct upstream other accounts reach: it keeps the account-scoped
    # failover path here, and its preserved classification is consumed by the
    # raw streaming path's in-request HTTP retry instead of the marker.
    exc = await _routed_connect_failure(
        monkeypatch,
        CodexTransportError(
            "Codex upstream websocket failed via proxy endpoint ep_1: HTTP 403",
            status_code=403,
            handshake_headers={"cf-mitigated": "challenge", "content-type": "text/html"},
            handshake_message="Invalid response status",
        ),
    )

    assert exc.failure_phase == "connect"
    assert _classify(exc) is None


@pytest.mark.asyncio
async def test_routed_tls_verification_failure_stays_out_of_transport_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = await _routed_connect_failure(
        monkeypatch,
        CodexTransportError(
            "Codex upstream websocket failed via proxy endpoint ep_1: ClientConnectorCertificateError",
            failure_phase="connect",
            retryable_same_contract=True,
            is_tls_verification_failure=True,
        ),
    )

    assert _classify(exc) is None


# --- Bridge fallback continuity and marker arming ---------------------------


def _durable_anchor_lookup() -> Any:
    return proxy_service.DurableBridgeLookup(
        session_id="sess-transport-fallback",
        canonical_kind="turn_state_header",
        canonical_key="http_turn_fresh",
        api_key_scope="__anonymous__",
        account_id="acc-1",
        owner_instance_id=None,
        owner_epoch=1,
        lease_expires_at=None,
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="http_turn_fresh",
        latest_response_id="resp_latest",
        latest_input_item_count=1,
        latest_input_full_fingerprint="a" * 64,
    )


@pytest.mark.asyncio
async def test_bridge_connect_failure_records_prepared_anchor_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bridge injects the durable anchor into its own prepared payload
    # before session creation runs. When creation then fails pre-submit, the
    # surfaced error must say so: the raw path never injects a response
    # anchor, so replaying the incoming payload would drop prior context.
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_service_get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        upstream_stream_transport="default",
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(
        http_bridge_streaming_module,
        "_service_get_settings",
        lambda: AppSettings(
            http_responses_session_bridge_enabled=True,
            http_responses_session_bridge_instance_id="instance-transport-fallback",
        ),
    )
    monkeypatch.setattr(
        service._durable_bridge,
        "lookup_request_targets",
        AsyncMock(return_value=_durable_anchor_lookup()),
    )
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value="acc-1"))

    async def failing_session_creation(*_args: object, **_kwargs: object) -> Any:
        raise _transport_error(502, "upstream_unavailable", "Request to upstream timed out")

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", failing_session_creation)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _chunk in service._stream_via_http_bridge(
            proxy_service.ResponsesRequest.model_validate(
                {
                    "model": "gpt-5.6-sol",
                    "instructions": "test",
                    "input": [{"type": "message", "role": "user", "content": "next turn"}],
                }
            ),
            headers={"x-codex-turn-state": "http_turn_fresh"},
            codex_session_affinity=True,
            propagate_http_errors=False,
            openai_cache_affinity=False,
            api_key=None,
            api_key_reservation=None,
            suppress_text_done_events=False,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=1800.0,
            max_sessions=8,
            queue_limit=4,
        ):
            pass

    assert getattr(exc_info.value, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, False) is True
    assert getattr(exc_info.value, http_bridge_streaming_module._HTTP_BRIDGE_PREPARED_ANCHOR_ATTR, False) is True


@pytest.mark.asyncio
async def test_http_bridge_prepared_anchor_failure_is_not_replayed_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def anchor_losing_bridge(*_args: object, **_kwargs: object):
        exc = _pre_submit_error()
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PREPARED_ANCHOR_ATTR, True)
        raise exc
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay a payload missing the bridge-prepared anchor")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", anchor_losing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)


@pytest.mark.asyncio
async def test_http_bridge_connect_fallback_arms_transport_failure_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bridge session creation runs its own pre-dispatch failover and never
    # reaches the websocket failover decision, so bridge-only traffic would
    # otherwise leave the marker clear: every later request would re-attempt
    # the dead websocket bridge, and the next handshake would be accepted.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def failing_bridge(*_args: object, **_kwargs: object):
        raise _pre_submit_error()
        yield ""

    async def record_stream_with_retry(*_args: object, **_kwargs: object):
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)

    await _collect_bridge_stream(service)

    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_http_bridge_refresh_provenance_failure_does_not_arm_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def refresh_failing_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(502, "upstream_unavailable", "temporary refresh failure")
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", refresh_failing_bridge)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)

    assert transport_health.upstream_websocket_transport_recently_failed() is False


# --- Budget exhaustion before the connector runs ----------------------------


class _StallingOpenService(_WebSocketMixin):
    """Real ``_open_upstream_websocket`` that stalls at a chosen stage."""

    def __init__(self, *, stall_admission: bool, route: Any = None) -> None:
        self._encryptor = TokenEncryptor()
        self._route = route

        class _ConnectLease:
            def release(self) -> None:
                return None

        class _WorkAdmission:
            async def acquire_websocket_connect(self) -> Any:
                if stall_admission:
                    await asyncio.sleep(5.0)
                return _ConnectLease()

        self._work_admission = _WorkAdmission()

    def _get_work_admission(self) -> Any:
        return self._work_admission

    async def _resolve_upstream_route_for_account(self, _account: object, *, operation: str) -> Any:
        del operation
        return self._route


class _StallingConnectorFacade:
    """Stalls the connector call; everything else defers to the real facade."""

    def __getattr__(self, name: str) -> Any:
        return getattr(proxy_service, name)

    async def _call_with_supported_optional_kwargs(
        self,
        function: object,
        *args: object,
        optional_kwargs: dict[str, object],
    ) -> object:
        del function, args, optional_kwargs
        await asyncio.sleep(5.0)

    async def connect_responses_websocket(self, *_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(5.0)


def _stalling_account(service: Any) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            access_token_encrypted=service._encryptor.encrypt("access-token"),
            chatgpt_account_id="account-123",
            codex_installation_id=None,
            id="acct-transport-fallback",
        ),
    )


@pytest.mark.asyncio
async def test_budget_exhausted_in_local_admission_does_not_arm_marker() -> None:
    # A request budget shorter than the local websocket-connect admission wait
    # expires before any upstream socket is attempted. Denying handshakes then
    # answers local contention by forcing every client onto HTTP, amplifying
    # the overload it came from.
    service = _StallingOpenService(stall_admission=True)

    with pytest.raises(ProxyResponseError):
        await service._open_upstream_websocket_with_budget(_stalling_account(service), {}, timeout_seconds=0.05)

    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_budget_exhausted_in_direct_connector_arms_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _StallingOpenService(stall_admission=False)
    monkeypatch.setattr(ws_mixin, "_facade", lambda: _StallingConnectorFacade())

    with pytest.raises(ProxyResponseError):
        await service._open_upstream_websocket_with_budget(_stalling_account(service), {}, timeout_seconds=0.05)

    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_budget_exhausted_in_routed_connector_does_not_arm_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stalled routed open shows one account's proxy endpoint unhealthy, the
    # same scope as a routed handshake failure. The cancellation raises no
    # ProxyResponseError for the routed exclusion to act on, so the progress
    # flag itself must stay confined to the direct connector.
    service = _StallingOpenService(
        stall_admission=False,
        route=ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="pool_1",
            endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
        ),
    )
    monkeypatch.setattr(ws_mixin, "_facade", lambda: _StallingConnectorFacade())

    with pytest.raises(ProxyResponseError):
        await service._open_upstream_websocket_with_budget(_stalling_account(service), {}, timeout_seconds=0.05)

    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_http_bridge_falls_back_on_direct_5xx_connect_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A direct 5xx bridge connect preserves the upstream envelope, so the
    # sanitized code is `upstream_error`, not `upstream_unavailable`. Gating
    # the fallback on the code left the exact outage this PR targets stuck on
    # the websocket bridge; the transport provenance must decide instead.
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    retry_calls: list[dict[str, Any]] = []

    async def failing_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(
            503,
            "upstream_error",
            "Service Unavailable",
            failure_phase="connect",
            failure_detail=UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
        )
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(service, "_stream_via_http_bridge", failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"
    assert transport_health.upstream_websocket_transport_recently_failed() is True


@pytest.mark.asyncio
async def test_http_bridge_routed_connect_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A routed bridge connect carries no transport provenance, so it keeps the
    # account/route failover path instead of degrading the whole instance.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def routed_failing_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(502, "upstream_unavailable", "routed handshake failed", failure_phase="connect")
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay a route-scoped connect failure")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", routed_failing_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)

    assert transport_health.upstream_websocket_transport_recently_failed() is False


class _SucceedingConnectorFacade(_StallingConnectorFacade):
    async def _call_with_supported_optional_kwargs(
        self,
        function: object,
        *args: object,
        optional_kwargs: dict[str, object],
    ) -> object:
        del function, args, optional_kwargs
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_direct_open_success_clears_transport_failure_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _StallingOpenService(stall_admission=False)
    monkeypatch.setattr(ws_mixin, "_facade", lambda: _SucceedingConnectorFacade())
    transport_health.mark_upstream_websocket_transport_failure()

    await service._open_upstream_websocket(_stalling_account(service), {})

    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_routed_open_success_does_not_clear_transport_failure_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Clearing must be as route-scoped as arming: a healthy proxy endpoint for
    # one account says nothing about the direct upstream the marker denied on,
    # so it must not readmit handshakes mid-outage.
    service = _StallingOpenService(
        stall_admission=False,
        route=ResolvedUpstreamRoute(
            mode="account_bound",
            pool_id="pool_1",
            endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
        ),
    )
    monkeypatch.setattr(ws_mixin, "_facade", lambda: _SucceedingConnectorFacade())
    transport_health.mark_upstream_websocket_transport_failure()

    await service._open_upstream_websocket(_stalling_account(service), {})

    assert transport_health.upstream_websocket_transport_recently_failed() is True


def _cooldown_request_state(**overrides: Any) -> Any:
    state = SimpleNamespace(
        previous_response_id=None,
        hard_continuity_anchor=False,
        proxy_injected_previous_response_id=False,
        file_required_preferred_account=False,
        response_id=None,
        response_event_count=0,
        replay_count=0,
        last_downstream_sequence_number=None,
        downstream_visible=False,
        payload_conversation_bound=False,
        response_create_attempt_count=0,
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def test_cooldown_suppression_replay_safety_predicate() -> None:
    from app.modules.proxy._service.http_bridge.request_submit import (
        _http_bridge_cooldown_suppression_is_replay_safe,
    )

    assert _http_bridge_cooldown_suppression_is_replay_safe(_cooldown_request_state()) is True

    ambiguous_states = [
        _cooldown_request_state(previous_response_id="resp_1"),
        _cooldown_request_state(hard_continuity_anchor=True),
        _cooldown_request_state(proxy_injected_previous_response_id=True),
        _cooldown_request_state(file_required_preferred_account=True),
        _cooldown_request_state(response_id="resp_2"),
        _cooldown_request_state(response_event_count=1),
        _cooldown_request_state(replay_count=1),
        _cooldown_request_state(last_downstream_sequence_number=3),
        _cooldown_request_state(downstream_visible=True),
        _cooldown_request_state(response_create_attempt_count=1),
        _cooldown_request_state(payload_conversation_bound=True),
    ]
    for state in ambiguous_states:
        assert _http_bridge_cooldown_suppression_is_replay_safe(state) is False


@pytest.mark.asyncio
async def test_http_bridge_falls_back_on_replay_safe_cooldown_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cooldown-suppressed submission tagged replay-safe (fresh turn,
    # provably undispatched) degrades to raw HTTP instead of a bounded 503.
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    retry_calls: list[dict[str, Any]] = []

    async def cooling_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(
            503,
            "upstream_request_timeout",
            "HTTP responses session bridge is cooling down after repeated upstream timeouts; retry shortly.",
        )
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_COOLDOWN_SUPPRESSION_ATTR, True)
        raise exc
        yield ""

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(service, "_stream_via_http_bridge", cooling_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert len(retry_calls) == 1
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"


@pytest.mark.asyncio
async def test_http_bridge_untagged_cooldown_suppression_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cooldown 503 for an ambiguous continuation carries no replay-safe
    # provenance and keeps the bounded 503.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def cooling_bridge(*_args: object, **_kwargs: object):
        raise _proxy_error(
            503,
            "upstream_request_timeout",
            "HTTP responses session bridge is cooling down after repeated upstream timeouts; retry shortly.",
        )
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("fallback must not replay an ambiguous cooldown suppression")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", cooling_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)


@pytest.mark.asyncio
async def test_real_bridge_request_state_is_replay_safe_before_any_send() -> None:
    # The predicate gates the cooldown fallback, so it must hold for a state
    # built by the real bridge request-preparation path. An optimistic marker
    # set at construction would make it false for every fresh turn and leave
    # the fallback unreachable.
    from app.modules.proxy._service.http_bridge.request_submit import (
        _http_bridge_cooldown_suppression_is_replay_safe,
    )

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    request_state, _text = service._prepare_response_bridge_request_state(
        _bridge_payload(),
        api_key=None,
        api_key_reservation=None,
        include_type_field=True,
        attach_event_queue=True,
        transport="http",
        client_metadata=None,
        headers={},
        request_id="req-cooldown-replay-safe",
    )

    assert request_state.awaiting_response_created is True
    assert request_state.response_create_attempt_count == 0
    assert _http_bridge_cooldown_suppression_is_replay_safe(request_state) is True


@pytest.mark.asyncio
async def test_http_bridge_budget_exhaustion_does_not_enter_cooldown_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_raise_proxy_budget_exhausted` emits the same `upstream_request_timeout`
    # the cooldown suppression uses, and session creation attaches the same
    # pre-submit provenance to both. Admitting it would double every request
    # exactly when the admission queue is saturated, and would feed a doomed
    # raw-HTTP attempt into its own process-network recovery wait.
    service = _bridge_service(monkeypatch, dashboard_transport="default")

    async def budget_exhausted_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(502, "upstream_request_timeout", "Proxy request budget exhausted")
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        raise exc
        yield ""

    async def fallback_must_not_run(*_args: object, **_kwargs: object):
        raise AssertionError("budget exhaustion is overload evidence, not a replay-safe cooldown suppression")
        yield ""

    monkeypatch.setattr(service, "_stream_via_http_bridge", budget_exhausted_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", fallback_must_not_run)

    with pytest.raises(ProxyResponseError):
        await _collect_bridge_stream(service)

    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_http_bridge_marked_cooldown_suppression_still_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _bridge_service(monkeypatch, dashboard_transport="default")
    retry_calls: list[dict[str, Any]] = []

    async def cooldown_bridge(*_args: object, **_kwargs: object):
        exc = _proxy_error(
            503,
            "upstream_request_timeout",
            "HTTP responses session bridge is cooling down after repeated upstream timeouts; retry shortly.",
        )
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_PRE_SUBMIT_FAILURE_ATTR, True)
        setattr(exc, http_bridge_streaming_module._HTTP_BRIDGE_COOLDOWN_SUPPRESSION_ATTR, True)
        raise exc
        yield ""

    async def record_stream_with_retry(*_args: object, **kwargs: object):
        retry_calls.append(cast(dict[str, Any], kwargs))
        yield 'data: {"type":"response.completed"}\n\n'

    monkeypatch.setattr(service, "_stream_via_http_bridge", cooldown_bridge)
    monkeypatch.setattr(service, "_stream_with_retry", record_stream_with_retry)

    chunks = await _collect_bridge_stream(service)

    assert chunks == ['data: {"type":"response.completed"}\n\n']
    assert retry_calls[0]["upstream_stream_transport_override"] == "http"
    # A bridge cooldown is not websocket-transport evidence.
    assert transport_health.upstream_websocket_transport_recently_failed() is False


@pytest.mark.asyncio
async def test_conversation_scoped_request_state_is_not_replay_safe() -> None:
    # A payload `conversation` binds the turn to the bridge session's account
    # but has no owner index, so a raw-HTTP replay cannot prove that owner in a
    # multi-account pool and would fail the turn closed rather than letting the
    # cooldown expire. It must count as continuation identity.
    from app.modules.proxy._service.http_bridge.request_submit import (
        _http_bridge_cooldown_suppression_is_replay_safe,
    )

    service = proxy_service.ProxyService(cast(Any, nullcontext()))

    def _state(payload: Any) -> Any:
        state, _text = service._prepare_response_bridge_request_state(
            payload,
            api_key=None,
            api_key_reservation=None,
            include_type_field=True,
            attach_event_queue=True,
            transport="http",
            client_metadata=None,
            headers={},
            request_id="req-conversation-scoped",
        )
        return state

    fresh = _state(_bridge_payload())
    assert fresh.payload_conversation_bound is False
    assert _http_bridge_cooldown_suppression_is_replay_safe(fresh) is True

    conversation_bound = _state(
        proxy_service.ResponsesRequest.model_validate(
            {
                "model": "gpt-5.6-sol",
                "instructions": "test",
                "input": "hello",
                "conversation": "conv_abc123",
            }
        )
    )
    assert conversation_bound.payload_conversation_bound is True
    assert _http_bridge_cooldown_suppression_is_replay_safe(conversation_bound) is False

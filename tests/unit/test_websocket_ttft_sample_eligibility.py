"""WebSocket-path regressions for the TTFT cohort sample eligibility.

The sampler unit tests (``test_ttft_cohort_weighting.py``) cover the filter
itself; these tests pin the two WebSocket seams that feed it: the finalizer's
``upstream_retried`` flag (transparent direct-WebSocket replays bump
``replay_count``, not the bridge's attempt count) and the global
response-create admission wait, which a direct WebSocket turn spends with no
bridge-queue measurement and which must therefore land in the gate wait.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.clock import REAL_SCHEDULER
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIEvent
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.request_log import _RequestLogMixin
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket import mixin as websocket_mixin_module
from app.modules.proxy._service.websocket.mixin import _WebSocketMixin
from app.modules.proxy.work_admission import WorkAdmissionController
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_request_log_virtual_time import _RepoContext, _RequestLogsRepo
from tests.unit.test_websocket_upstream_transport_observability import _DummyFacade, _no_op_release_gate

pytestmark = pytest.mark.unit

_NOW = 2_000_000_000.0


@pytest.fixture(autouse=True)
def _patch_websocket_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websocket_mixin_module, "_facade", lambda: _DummyFacade())
    monkeypatch.setattr(websocket_mixin_module, "_release_websocket_response_create_gate", _no_op_release_gate)


class _FunnelWebSocketService(_RequestLogMixin, _WebSocketMixin):
    """Finalizer double that runs the real request-log funnel into a balancer runtime."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock
        self._scheduler = REAL_SCHEDULER
        self._encryptor = TokenEncryptor()
        self._background_cleanup_tasks: set[asyncio.Task[None]] = set()
        self._request_log_tasks: set[asyncio.Task[None]] = set()
        self.request_logs = _RequestLogsRepo()
        repos = SimpleNamespace(request_logs=self.request_logs)
        self._repo_factory = lambda: _RepoContext(repos)
        self.runtime: dict[str, Any] = {}
        self._load_balancer = SimpleNamespace(_runtime=self.runtime, _clock=clock, record_success=AsyncMock())

    def _cancel_request_state_api_key_reservation_heartbeat(self, _request_state: _WebSocketRequestState) -> None:
        return None

    async def _settle_stream_api_key_usage(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def _release_websocket_request_state_reservation(self, _request_state: _WebSocketRequestState) -> None:
        return None

    def _remember_websocket_previous_response_owner(self, **_kwargs: object) -> None:
        return None


def _direct_turn(request_id: str, **overrides: Any) -> _WebSocketRequestState:
    kwargs: dict[str, Any] = {
        "request_id": request_id,
        "response_id": f"resp_{request_id}",
        "model": "gpt-5.5",
        "service_tier": None,
        "reasoning_effort": "low",
        "api_key_reservation": None,
        "started_at": 0.0,
        "transport": _REQUEST_TRANSPORT_WEBSOCKET,
        "upstream_transport": _REQUEST_TRANSPORT_WEBSOCKET,
        "latency_first_token_ms": 1_700,
    }
    kwargs.update(overrides)
    return _WebSocketRequestState(**kwargs)


async def _finalize(service: _FunnelWebSocketService, request_state: _WebSocketRequestState) -> None:
    event = OpenAIEvent.model_validate(
        {
            "type": "response.completed",
            "response": {"id": request_state.response_id, "usage": {"input_tokens": 3_000, "output_tokens": 40}},
        }
    )
    await service._finalize_websocket_request_state(
        request_state,
        account=cast(Any, object()),
        account_id_value="acc-ws",
        event=event,
        event_type="response.completed",
        payload={},
        api_key=None,
        upstream_control=_WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )


@pytest.mark.asyncio
async def test_direct_websocket_replay_and_admission_wait_are_kept_out_of_the_ttft_window() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    service = _FunnelWebSocketService(clock)

    await _finalize(service, _direct_turn("clean"))
    # Transparent direct-WebSocket replay: the failed attempt and the
    # reconnect sit inside the TTFT, and the replacement account would be
    # charged for them. Only ``replay_count`` records it on this path.
    await _finalize(service, _direct_turn("replayed", replay_count=1))
    # Global admission wait with no bridge-queue measurement: recorded into
    # the gate wait by ``_acquire_request_state_response_create_admission``.
    await _finalize(service, _direct_turn("admission_queued", latency_response_create_gate_wait_ms=2_500))
    await service.drain_persistence_tasks(1.0)

    assert [row["request_id"] for row in service.request_logs.rows] == [
        "resp_clean",
        "resp_replayed",
        "resp_admission_queued",
    ]
    assert service.runtime["acc-ws"].ttft_samples == [(_NOW, 1_700)]


@pytest.mark.asyncio
async def test_direct_websocket_global_admission_wait_is_recorded_into_the_gate_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = VirtualClock(epoch_value=_NOW)
    service = proxy_service.ProxyService(cast(Any, lambda: _RepoContext(SimpleNamespace())), clock=clock)
    service._work_admission = WorkAdmissionController(
        token_refresh_limit=1,
        websocket_connect_limit=1,
        response_create_limit=1,
        compact_response_create_limit=1,
        admission_wait_timeout_seconds=5.0,
    )
    monkeypatch.setattr(
        proxy_service, "get_settings", lambda: SimpleNamespace(proxy_admission_wait_timeout_seconds=5.0)
    )
    request_state = _direct_turn("admission_wait")
    response_create_gate = asyncio.Semaphore(1)

    saturating_lease = await service._get_work_admission().acquire_response_create()
    task = asyncio.create_task(
        service._acquire_request_state_response_create_admission(
            request_state, response_create_gate=response_create_gate
        )
    )
    try:
        for _ in range(50):
            if request_state.response_create_gate_acquired:
                break
            await asyncio.sleep(0)
        # The session gate was free: only the global limit is holding the turn.
        assert request_state.response_create_gate_acquired is True
        assert request_state.response_create_admission is None
        assert request_state.latency_response_create_gate_wait_ms == 0
        clock.advance(2.5)
    finally:
        saturating_lease.release()
    await task

    assert request_state.response_create_admission is not None
    assert request_state.latency_response_create_gate_wait_ms == 2_500
    request_state.response_create_admission.release()
    response_create_gate.release()

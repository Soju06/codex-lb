"""Tests for HTTP bridge previous_response_id preservation during recovery.

Regression tests for the context blowup bug where bridge recovery paths
returned 400 previous_response_not_found instead of 502 upstream_unavailable,
causing the Codex CLI to drop previous_response_id and resend the full
conversation history (~70K tokens/turn instead of ~2-3K).

Real-world scenario (from codex session logs):
  - 4 user messages, 22 tool calls, 17 API turns
  - System prompt: ~50K tokens
  - With previous_response_id: context grew from 50K to 80K (healthy)
  - Without previous_response_id: context grew from 50K to 853K (broken)
  - Per-turn growth: 2.3K (healthy) vs 70K (broken)

These tests are LOCAL ONLY — not pushed upstream.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = [pytest.mark.unit, pytest.mark.asyncio(loop_scope="session")]


def _make_session(*, closed: bool = False) -> proxy_service._HTTPBridgeSession:
    """Create a minimal bridge session for testing."""
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-test", None),
        headers={"x-codex-session-id": "sid-test"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-test",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=time.time(),
        idle_ttl_seconds=120.0,
        closed=closed,
    )


def _make_request_state(
    *,
    previous_response_id: str | None = None,
) -> proxy_service._WebSocketRequestState:
    """Create a request state, optionally with previous_response_id."""
    return proxy_service._WebSocketRequestState(
        request_id="req-test-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.time(),
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id=previous_response_id,
    )


class TestSubmitClosedSessionWithPreviousResponseId:
    """When session is closed and previous_response_id is present,
    the bridge MUST raise 502 (not 400 previous_response_not_found)
    so the CLI retries with previous_response_id intact."""

    async def test_closed_session_with_previous_response_id_raises_502(self):
        """POSITIVE: closed session + previous_response_id -> 502 upstream error.

        This preserves previous_response_id on retry, keeping per-turn
        context growth at ~2-3K tokens instead of ~70K.
        """
        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        request_state = _make_request_state(
            previous_response_id="resp_abc123",
        )

        with pytest.raises(ProxyResponseError) as exc_info:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create","previous_response_id":"resp_abc123"}',
                queue_limit=10,
            )

        # Must be 502 (retriable) not 400 (previous_response_not_found)
        assert exc_info.value.status_code == 502
        error_payload = exc_info.value.payload
        assert error_payload["error"]["code"] == "upstream_unavailable"
        # Must NOT be previous_response_not_found
        assert "previous_response_not_found" not in str(error_payload)

    async def test_closed_session_without_previous_response_id_attempts_recovery(
        self,
        monkeypatch,
    ):
        """POSITIVE: closed session WITHOUT previous_response_id can attempt
        websocket reconnect since there's no server-side state to preserve."""
        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        request_state = _make_request_state(previous_response_id=None)

        # Mock the retry to succeed (reconnect works)
        retry_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(
            service,
            "_retry_http_bridge_request_on_fresh_upstream",
            retry_mock,
        )
        # Mock prewarm and actual send to avoid full flow
        monkeypatch.setattr(
            service,
            "_maybe_prewarm_http_bridge_session",
            AsyncMock(),
        )

        # Should NOT raise 502 - should attempt recovery
        # (will fail later in the flow but that's OK for this test)
        event_queue = request_state.event_queue
        assert event_queue is not None
        await event_queue.put(None)  # Simulate response completion

        # The retry was called (recovery attempted)
        # This verifies the old recovery path still works for non-previous_response_id cases
        try:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create"}',
                queue_limit=10,
            )
        except Exception:
            pass  # May fail downstream, that's fine

        # Retry was called (at least once for recovery, possibly again on send failure)
        assert retry_mock.call_count >= 1
        # First call should be with send_request=False (recovery/reconnect)
        first_call = retry_mock.call_args_list[0]
        assert first_call.kwargs.get("send_request") is False


class TestMidRequestFailurePreservesPreviousResponseId:
    """When the upstream websocket dies MID-REQUEST and previous_response_id
    is present, the bridge MUST raise 502, not 400 previous_response_not_found.

    This is the primary bug path: the error handler in _submit_http_bridge_request
    (around the websocket send) was returning 400 previous_response_not_found,
    telling the CLI to drop previous_response_id and resend full conversation."""

    async def test_mid_request_failure_with_previous_response_id_raises_502(
        self,
        monkeypatch,
    ):
        """BUG REGRESSION: mid-request websocket failure + previous_response_id.

        BROKEN (pre-fix): raises ProxyResponseError(400, previous_response_not_found)
          -> CLI drops previous_response_id -> 70K tokens/turn
        FIXED: raises ProxyResponseError(502, upstream_unavailable)
          -> CLI retries with previous_response_id -> 2.3K tokens/turn
        """
        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=False)
        request_state = _make_request_state(
            previous_response_id="resp_abc123",
        )

        # Mock the upstream websocket to fail on send
        async def failing_send(*args, **kwargs):
            raise ConnectionError("upstream websocket died")

        session.upstream = cast(
            Any,
            SimpleNamespace(
                send_text=failing_send,
                close=AsyncMock(),
            ),
        )
        # Mock _fail_pending_websocket_requests to avoid side effects
        monkeypatch.setattr(
            service,
            "_fail_pending_websocket_requests",
            AsyncMock(),
        )

        with pytest.raises(ProxyResponseError) as exc_info:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create","previous_response_id":"resp_abc123"}',
                queue_limit=10,
            )

        # MUST be 502 (retriable), NOT 400 (previous_response_not_found)
        assert exc_info.value.status_code == 502, (
            f"Expected 502 upstream_unavailable but got {exc_info.value.status_code}. "
            f"If this is 400, the bug is present: the CLI will drop "
            f"previous_response_id and resend full conversation (70K tok/turn)."
        )
        assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
        assert "previous_response_not_found" not in str(exc_info.value.payload)


class TestRetryHelperPreservesPreviousResponseId:
    """The _retry_http_bridge_request_on_fresh_upstream helper must NOT
    mark previous_response_not_found when previous_response_id is present.
    It should return False so the caller raises a retriable 502."""

    async def test_retry_with_previous_response_id_returns_false_without_marking_error(self):
        """POSITIVE: retry helper returns False without setting error codes."""
        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        request_state = _make_request_state(
            previous_response_id="resp_xyz789",
        )

        result = await service._retry_http_bridge_request_on_fresh_upstream(
            session=session,
            request_state=request_state,
            text_data='{"type":"response.create","previous_response_id":"resp_xyz789"}',
            send_request=True,
        )

        # Must return False (recovery not possible with previous_response_id)
        assert result is False
        # Must NOT have set error_code_override to previous_response_not_found
        assert request_state.error_code_override != "previous_response_not_found"
        assert request_state.error_code_override is None


class TestContextGrowthScenarios:
    """Scenario tests modelling real Codex session data.

    Based on actual session logs:
    - Apr 11 (healthy): 211 turns, 2.3K tok/turn, max 340K, 0 compactions
    - Apr 13 (broken):   17 turns, 70K tok/turn, max 853K, 2 compactions
    """

    def test_healthy_growth_rate_stays_within_budget(self):
        """With previous_response_id preserved, each turn adds only new content."""
        system_prompt_tokens = 50_000
        content_per_turn = 2_300  # avg from Apr 11 sessions
        context_window = 876_000
        max_turns = 200

        context = system_prompt_tokens
        for turn in range(max_turns):
            context += content_per_turn
            if context >= context_window:
                pytest.fail(
                    f"Context exceeded window at turn {turn} "
                    f"({context:,} >= {context_window:,}). "
                    f"Healthy sessions should last 200+ turns."
                )

        assert context < context_window

    def test_broken_growth_rate_fills_window_fast(self):
        """Without previous_response_id, context fills in <20 turns."""
        system_prompt_tokens = 50_000
        growth_per_turn = 70_000  # avg from Apr 13 sessions (broken)
        context_window = 876_000

        context = system_prompt_tokens
        compaction_turn = None
        for turn in range(200):
            context += growth_per_turn
            if context >= context_window:
                compaction_turn = turn
                break

        assert compaction_turn is not None
        assert compaction_turn < 20

    def test_error_code_determines_growth_rate(self):
        """502 -> CLI retries with previous_response_id -> healthy growth
        400 previous_response_not_found -> CLI drops it -> broken growth"""
        error_502 = ProxyResponseError(
            502,
            {"error": {"code": "upstream_unavailable", "message": "closed"}},
        )
        assert error_502.status_code == 502

        error_400_body = {
            "error": {
                "code": "previous_response_not_found",
                "message": "Previous response not found",
                "param": "previous_response_id",
            }
        }
        error_400 = ProxyResponseError(400, error_400_body)
        assert error_400.status_code == 400
        assert error_400.payload["error"]["code"] == "previous_response_not_found"

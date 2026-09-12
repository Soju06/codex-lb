"""Tests for transient stream retry logic.

Covers:
- Streaming: SSE-level transient errors → same-account retry → failover
- Streaming: HTTP-level 500 → same-account retry → failover
- Compact: HTTP 500 → same-account retry with backoff → account failover
- Budget exhaustion during inner retry
- Non-500 errors are not intercepted by transient retry
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from unittest.mock import MagicMock

import aiohttp
import pytest

import app.modules.proxy.account_cache as account_cache_module
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.clock import RealScheduler
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy._service import observability as proxy_observability_module
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _server_error_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "server_error",
                    "message": "An error occurred while processing your request.",
                },
            },
        }
    )


def _overload_sse_event(error_code: str) -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": error_code,
                    "message": "Our servers are currently overloaded. Please try again later.",
                },
            },
        }
    )


def _stream_timeout_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "upstream_request_timeout",
                    "message": "Proxy request budget exhausted",
                },
            },
        }
    )


def _model_capacity_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "invalid_request_error",
                    "message": "Selected model is at capacity. Please try a different model.",
                },
            },
        }
    )


def _success_sse_event(response_id: str = "resp_ok") -> str:
    return _sse_event(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )


def _extract_events(lines: list[str]) -> list[dict]:
    events = []
    for line in lines:
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                continue
            events.append(json.loads(data))
    return events


# ===========================================================================
# Streaming — SSE-level server_error
# ===========================================================================


@pytest.mark.asyncio
async def test_stream_server_error_surfaces_without_replay(async_client, monkeypatch):
    """An upstream terminal server error cannot prove that retrying the POST is safe."""
    await _import_account(async_client, "acc_trans_1", "trans1@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "server_error"
    assert seen_account_ids == ["acc_trans_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["overloaded_error", "server_is_overloaded"])
async def test_stream_overload_alias_surfaces_without_replay(async_client, monkeypatch, error_code):
    await _import_account(async_client, f"acc_{error_code}", f"{error_code}@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _overload_sse_event(error_code)
            return
        yield _success_sse_event("resp_server_overloaded_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == error_code
    assert seen_account_ids == [f"acc_{error_code}"]


@pytest.mark.asyncio
async def test_stream_timeout_surfaces_without_replay(async_client, monkeypatch):
    """An upstream terminal timeout is not proven pre-dispatch work."""
    await _import_account(async_client, "acc_trans_timeout", "timeout@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _stream_timeout_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "upstream_request_timeout"
    assert seen_account_ids == ["acc_trans_timeout"]


@pytest.mark.asyncio
async def test_stream_model_capacity_without_response_id_surfaces_without_replay(async_client, monkeypatch):
    """An upstream terminal event is accepted work even when it omits a response id."""
    await _import_account(async_client, "acc_model_capacity_retry", "model-capacity@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _model_capacity_sse_event()
            return

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "invalid_request_error"
    assert seen_account_ids == ["acc_model_capacity_retry"]


@pytest.mark.asyncio
async def test_stream_raw_capacity_error_with_proxy_request_id_surfaces_without_replay(async_client, monkeypatch):
    """A terminal upstream error remains terminal even with a proxy-generated id."""
    await _import_account(async_client, "acc_raw_model_capacity_retry", "raw-model-capacity@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _sse_event(
                {
                    "type": "error",
                    "code": "invalid_request_error",
                    "message": "Selected model is at capacity. Please try a different model.",
                }
            )
            return

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    errors = [event for event in events if event.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "invalid_request_error"
    assert seen_account_ids == ["acc_raw_model_capacity_retry"]


@pytest.mark.asyncio
async def test_stream_model_capacity_top_level_response_id_surfaces_without_replay(async_client, monkeypatch):
    """A top-level upstream response_id proves dispatch, so capacity errors must not be replayed."""
    await _import_account(async_client, "acc_model_capacity_accepted", "model-capacity-accepted@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        yield _sse_event(
            {
                "type": "response.failed",
                "response_id": "resp_model_capacity_accepted",
                "response": {
                    "error": {
                        "code": "invalid_request_error",
                        "message": "Selected model is at capacity. Please try a different model.",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "invalid_request_error"
    assert seen_account_ids == ["acc_model_capacity_accepted"]


@pytest.mark.asyncio
async def test_stream_empty_upstream_body_surfaces_without_replay(async_client, monkeypatch):
    """An untyped empty upstream stream may be post-dispatch, so it is not replayed."""
    await _import_account(async_client, "acc_empty_body_no_replay", "empty-body-no-replay@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            if False:
                yield ""
            return

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    error_codes = [event["response"]["error"]["code"] for event in events if event.get("type") == "response.failed"]
    assert error_codes[-1] == "stream_incomplete"
    assert seen_account_ids == ["acc_empty_body_no_replay"]


@pytest.mark.asyncio
async def test_stream_body_read_client_error_surfaces_without_replay(async_client, monkeypatch):
    """Post-connect body-read errors are not replayed because upstream delivery is uncertain."""
    await _import_account(async_client, "acc_previsible_disconnect_a", "previsible-disconnect-a@example.com")
    await _import_account(async_client, "acc_previsible_disconnect_b", "previsible-disconnect-b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if False:
            yield ""
        raise aiohttp.ServerDisconnectedError("Server disconnected")

    async def fake_sleep(delay: float, result: None = None) -> None:
        pass

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.asyncio, "sleep", fake_sleep)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    error_codes = [event["response"]["error"]["code"] for event in events if event.get("type") == "response.failed"]
    assert error_codes[-1] == "upstream_unavailable"
    assert "no_accounts" not in error_codes
    assert seen_account_ids == ["acc_previsible_disconnect_a"]


@pytest.mark.asyncio
async def test_stream_serialized_body_read_disconnect_with_response_id_surfaces_without_replay(
    async_client, monkeypatch
):
    """Serialized post-dispatch body-read failures with response_id are not safe to replay."""
    await _import_account(async_client, "acc_serialized_disconnect_a", "serialized-disconnect-a@example.com")
    await _import_account(async_client, "acc_serialized_disconnect_b", "serialized-disconnect-b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        yield _sse_event(
            {
                "type": "response.failed",
                "response_id": "resp_serialized_disconnect",
                "response": {
                    "error": {
                        "code": "upstream_unavailable",
                        "message": "Server disconnected",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "upstream_unavailable"
    assert seen_account_ids == ["acc_serialized_disconnect_a"]


@pytest.mark.asyncio
async def test_stream_serialized_body_read_disconnect_with_request_id_surfaces_without_replay(
    async_client, monkeypatch
):
    """A response.failed using the proxy request id still represents an unsafe post-dispatch failure."""
    await _import_account(async_client, "acc_serialized_request_id_disconnect_a", "serialized-request-a@example.com")
    await _import_account(async_client, "acc_serialized_request_id_disconnect_b", "serialized-request-b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del payload, headers, access_token, base_url, raise_for_status
        seen_account_ids.append(account_id)
        request_id = get_request_id()
        assert request_id is not None
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "id": request_id,
                    "error": {
                        "code": "upstream_unavailable",
                        "message": "Server disconnected",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "upstream_unavailable"
    assert seen_account_ids == ["acc_serialized_request_id_disconnect_a"]


@pytest.mark.asyncio
async def test_stream_pinned_previsible_close_exhaustion_surfaces_stream_incomplete(async_client, monkeypatch):
    """Pinned previous-response EOF cannot fail over, so preserve the stream failure for the client."""
    upstream_account_id = "acc_pinned_previsible_close"
    owner_account_id = await _import_account(
        async_client,
        upstream_account_id,
        "pinned-previsible-close@example.com",
    )

    seen_account_ids: list[str | None] = []

    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if False:
            yield ""
        return

    async def fake_sleep(delay: float, result: None = None) -> None:
        pass

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.asyncio, "sleep", fake_sleep)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
        "previous_response_id": "resp_pinned_previsible_close",
    }
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    error_codes = [event["response"]["error"]["code"] for event in events if event.get("type") == "response.failed"]
    assert error_codes[-1] == "stream_incomplete"
    assert "previous_response_owner_unavailable" not in error_codes
    assert set(seen_account_ids) == {upstream_account_id}


@pytest.mark.asyncio
async def test_stream_server_error_does_not_fail_over_after_accepted_terminal_event(async_client, monkeypatch):
    """A terminal event stays on its first account even when another account is eligible."""
    await _import_account(async_client, "acc_trans_fo_a", "fo_a@example.com")
    await _import_account(async_client, "acc_trans_fo_b", "fo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_trans_fo_a":
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "server_error"
    assert seen_account_ids == ["acc_trans_fo_a"]


@pytest.mark.asyncio
async def test_stream_server_error_all_accounts_exhausted(async_client, monkeypatch):
    """server_error on all accounts → eventually returns error to client."""
    await _import_account(async_client, "acc_trans_all_a", "all_a@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _server_error_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    # Should end with an error event (either response.failed or no_accounts)
    last_event = events[-1] if events else {}
    assert last_event.get("type") in ("response.failed", "error")


@pytest.mark.asyncio
async def test_stream_server_error_does_not_make_a_third_post(async_client, monkeypatch):
    """An accepted terminal event prevents follow-up same-account POSTs."""
    await _import_account(async_client, "acc_trans_3rd", "trans3@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count <= 2:
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert len(failed) == 1
    assert failed[0]["response"]["error"]["code"] == "server_error"
    assert seen_account_ids == ["acc_trans_3rd"]


# ===========================================================================
# Streaming — HTTP-level 500 (ProxyResponseError)
# ===========================================================================


@pytest.mark.asyncio
async def test_stream_http_500_retries_same_account_then_succeeds(async_client, monkeypatch):
    """HTTP 500 ProxyResponseError → inner retry on same account → success."""
    await _import_account(async_client, "acc_http500_1", "http500@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "An error occurred while processing your request."),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_http_500_exhausts_then_failover(async_client, monkeypatch):
    """HTTP 500 x3 on account A → failover to account B → success."""
    await _import_account(async_client, "acc_h5fo_a", "h5fo_a@example.com")
    await _import_account(async_client, "acc_h5fo_b", "h5fo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_h5fo_a":
            raise ProxyResponseError(
                500,
                openai_error("server_error", "Internal server error"),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    a_calls = [aid for aid in seen_account_ids if aid == "acc_h5fo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_h5fo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_stream_connect_phase_429_usage_limit_transparent_failover(async_client, monkeypatch):
    """Connect-phase 429/usage_limit_reached on A should fail over to B before any downstream event."""
    account_a_id = await _import_account(async_client, "acc_stream_429_a", "stream429a@example.com")
    await _import_account(async_client, "acc_stream_429_b", "stream429b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_stream_429_a":
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="status",
            )
        yield _success_sse_event("resp_stream_429_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert len(failed) == 0
    assert seen_account_ids[:2] == ["acc_stream_429_a", "acc_stream_429_b"]

    async with SessionLocal() as session:
        exhausted_account = await session.get(Account, account_a_id)
        assert exhausted_account is not None
        assert exhausted_account.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_stream_compacted_input_429_usage_limit_transparent_failover(async_client, monkeypatch):
    """A pre-visible 429 must not bind retained input to the rejected account."""
    await _import_account(async_client, "acc_stream_compact_429_a", "streamcompact429a@example.com")
    await _import_account(async_client, "acc_stream_compact_429_b", "streamcompact429b@example.com")

    seen_account_ids: list[str | None] = []
    rejected_account_id: str | None = None

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal rejected_account_id
        seen_account_ids.append(account_id)
        if rejected_account_id is None:
            rejected_account_id = account_id
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="status",
            )
        assert account_id != rejected_account_id
        yield _success_sse_event("resp_stream_compact_429_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "describe the image",
        "input": [
            {"type": "compaction", "encrypted_content": "opaque-compacted-history"},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="},
        ],
        "prompt_cache_key": "cache_compacted_image_429",
        "stream": True,
    }
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert len(failed) == 0
    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] != seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_encrypted_reasoning_429_cross_account_failover_is_unchanged(async_client, monkeypatch):
    """A rejected pending owner may fail over without rewriting ciphertext."""
    await _import_account(async_client, "acc_stream_reasoning_429_a", "reasoning429a@example.com")
    await _import_account(async_client, "acc_stream_reasoning_429_b", "reasoning429b@example.com")

    encrypted_content = "opaque-authenticated-encrypted-reasoning"
    seen_account_ids: list[str | None] = []
    seen_ciphertexts: list[str] = []
    rejected_account_id: str | None = None

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal rejected_account_id
        seen_account_ids.append(account_id)
        assert isinstance(payload.input, list)
        reasoning_item = payload.input[0]
        assert isinstance(reasoning_item, dict)
        assert reasoning_item["type"] == "reasoning"
        seen_ciphertexts.append(reasoning_item["encrypted_content"])
        if rejected_account_id is None:
            rejected_account_id = account_id
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="status",
            )
        assert account_id != rejected_account_id
        yield _success_sse_event("resp_stream_reasoning_429_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {
                "type": "reasoning",
                "id": "rs_cross_account",
                "summary": [],
                "encrypted_content": encrypted_content,
            },
            {"role": "user", "content": "continue"},
        ],
        "prompt_cache_key": "cache_encrypted_reasoning_429",
        "stream": True,
    }
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    assert len([e for e in events if e.get("type") == "response.completed"]) == 1
    assert not [e for e in events if e.get("type") == "response.failed"]
    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] != seen_account_ids[1]
    assert seen_ciphertexts == [encrypted_content, encrypted_content]


@pytest.mark.asyncio
async def test_stream_compacted_input_first_event_usage_limit_transparent_failover(async_client, monkeypatch):
    """A first-event quota rejection must not bind retained input to the rejected account."""
    await _import_account(async_client, "acc_stream_event_limit_a", "streameventlimita@example.com")
    await _import_account(async_client, "acc_stream_event_limit_b", "streameventlimitb@example.com")

    seen_account_ids: list[str | None] = []
    rejected_account_id: str | None = None

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal rejected_account_id
        seen_account_ids.append(account_id)
        if rejected_account_id is None:
            rejected_account_id = account_id
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {
                        "error": {
                            "code": "usage_limit_reached",
                            "message": "usage limit reached",
                        },
                    },
                }
            )
            return
        assert account_id != rejected_account_id
        yield _success_sse_event("resp_stream_event_limit_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "describe the image",
        "input": [
            {"type": "compaction", "encrypted_content": "opaque-compacted-history"},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="},
        ],
        "prompt_cache_key": "cache_compacted_image_event_limit",
        "stream": True,
    }
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert len(failed) == 0
    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] != seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_code_less_429_retries_same_account_then_succeeds(async_client, monkeypatch):
    """Code-less 429 (upstream burst) on an owner-bound payload backs off and retries the owner.

    Contrast with the coded-429 test above: the account is never marked
    RATE_LIMITED and the request never crosses accounts (``reasoning`` input
    items bind the dispatched payload to the first account).
    """
    account_a_id = await _import_account(async_client, "acc_stream_burst_a", "streambursta@example.com")
    await _import_account(async_client, "acc_stream_burst_b", "streamburstb@example.com")

    seen_account_ids: list[str | None] = []
    slept = _record_burst_backoff_sleeps(monkeypatch)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if len(seen_account_ids) == 1:
            raise ProxyResponseError(
                429,
                {"error": {"message": "Rate limit exceeded"}},
                failure_phase="status",
            )
        yield _success_sse_event("resp_stream_burst_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [{"type": "reasoning", "id": "rs_stream_burst", "encrypted_content": "owner-bound"}],
        "stream": True,
    }
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert len(failed) == 0
    assert seen_account_ids == ["acc_stream_burst_a", "acc_stream_burst_a"]
    assert 1.0 in slept

    async with SessionLocal() as session:
        burst_account = await session.get(Account, account_a_id)
        assert burst_account is not None
        assert burst_account.status == AccountStatus.ACTIVE


def _record_burst_backoff_sleeps(monkeypatch) -> list[float]:
    """Intercept the bounded burst backoff through the scheduler seam it actually uses.

    The wait runs through ``RealScheduler.sleep`` (``scheduler_for(proxy)``), so
    patching that seam observes exactly the backoff schedule. Patching the
    process-wide ``asyncio.sleep`` instead would also reach the lifespan
    ``run_event_loop_lag_monitor`` task, whose ``asyncio.sleep(1.0)`` loop then
    spins without yielding for the rest of the test and floods the recording.
    The fake still yields once so the loop keeps turning.
    """
    slept: list[float] = []
    real_sleep = asyncio.sleep

    def fake_sleep(self, delay: float, result: None = None):
        if delay > 0:
            slept.append(delay)
        return real_sleep(0, result)

    monkeypatch.setattr(RealScheduler, "sleep", fake_sleep)
    return slept


_NATIVE_CODEX_HEADERS = {"originator": "codex_cli_rs"}
_BURST_OWNER_BOUND_PAYLOAD = {
    "model": "gpt-5.1",
    "instructions": "hi",
    "input": [{"type": "reasoning", "id": "rs_stream_burst_native", "encrypted_content": "owner-bound"}],
    "stream": True,
}


def _install_always_burst_upstream(monkeypatch, *, retry_after_seconds=None, retry_after_header=None):
    seen_account_ids: list[str | None] = []
    slept = _record_burst_backoff_sleeps(monkeypatch)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        raise ProxyResponseError(
            429,
            {"error": {"message": "Rate limit exceeded"}},
            failure_phase="status",
            retry_after_seconds=retry_after_seconds,
            retry_after_header=retry_after_header,
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    return seen_account_ids, slept


@pytest.mark.asyncio
async def test_native_codex_stream_code_less_429_exhaustion_surfaces_http_429_with_retry_after(
    async_client, monkeypatch, caplog
):
    """Codex CLI (native headers, propagate_http_errors, no SDK contract): the bounded same-account
    backoff must hold the HTTP headers -- no keepalive frame commits a 200 -- so the exhausted
    burst is still a real HTTP 429 carrying ``Retry-After: 5``."""
    await _import_account(async_client, "acc_stream_burst_native_a", "streamburstnativea@example.com")
    await _import_account(async_client, "acc_stream_burst_native_b", "streamburstnativeb@example.com")
    seen_account_ids, slept = _install_always_burst_upstream(monkeypatch)
    caplog.set_level("INFO", logger="app.modules.proxy.service")

    async with async_client.stream(
        "POST", "/backend-api/codex/responses", json=_BURST_OWNER_BOUND_PAYLOAD, headers=_NATIVE_CODEX_HEADERS
    ) as resp:
        body = json.loads(await resp.aread())
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "5"

    assert body == {"error": {"message": "Rate limit exceeded"}}
    # Never crossed accounts; three backoffs then the original rejection.
    assert len(seen_account_ids) == 4
    assert len(set(seen_account_ids)) == 1
    assert slept == [1.0, 2.0, 4.0]
    assert caplog.text.count("action=retry_same_account") == 3
    assert "failure_class=retryable_transient action=surface" in caplog.text
    assert "action=failover_next" not in caplog.text


@pytest.mark.asyncio
async def test_native_codex_stream_code_less_429_exhaustion_preserves_upstream_retry_after(async_client, monkeypatch):
    await _import_account(async_client, "acc_stream_burst_native_ra", "streamburstnativera@example.com")
    _seen, slept = _install_always_burst_upstream(monkeypatch, retry_after_seconds=3, retry_after_header="3")

    async with async_client.stream(
        "POST", "/backend-api/codex/responses", json=_BURST_OWNER_BOUND_PAYLOAD, headers=_NATIVE_CODEX_HEADERS
    ) as resp:
        await resp.aread()
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "3"

    # Retry-After floors the schedule: 3, 3, then the exponential 4.
    assert slept == [3.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_native_codex_stream_code_less_429_retry_success_emits_no_capacity_keepalive(async_client, monkeypatch):
    """The held-header wait must not leak a ``waiting_for_account_capacity`` keepalive into the stream."""
    await _import_account(async_client, "acc_stream_burst_native_ok", "streamburstnativeok@example.com")
    seen_account_ids: list[str | None] = []
    slept = _record_burst_backoff_sleeps(monkeypatch)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if len(seen_account_ids) == 1:
            raise ProxyResponseError(429, {"error": {"message": "Rate limit exceeded"}}, failure_phase="status")
        yield _success_sse_event("resp_stream_burst_native_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST", "/backend-api/codex/responses", json=_BURST_OWNER_BOUND_PAYLOAD, headers=_NATIVE_CODEX_HEADERS
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    assert [e["type"] for e in events if e.get("type") == "response.completed"] == ["response.completed"]
    assert all(e.get("status") != "waiting_for_account_capacity" for e in events)
    assert seen_account_ids == ["acc_stream_burst_native_ok", "acc_stream_burst_native_ok"]
    assert 1.0 in slept


@pytest.mark.asyncio
async def test_stream_http_502_unknown_code_fails_over_to_second_account(async_client, monkeypatch):
    await _import_account(async_client, "acc_h502_a", "h502_a@example.com")
    await _import_account(async_client, "acc_h502_b", "h502_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_h502_a":
            raise ProxyResponseError(
                502,
                openai_error("bad_gateway", "Bad gateway"),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1
    assert seen_account_ids[:2] == ["acc_h502_a", "acc_h502_b"]


# ===========================================================================
# Streaming — Model-entitlement rejection is health neutral through the route
# ===========================================================================


_MODEL_ENTITLEMENT_REJECTION_MESSAGE = "The 'gpt-5.1' model is not supported when using Codex with a ChatGPT account."


@pytest.mark.asyncio
async def test_stream_model_entitlement_rejection_keeps_account_health_after_failover(async_client, monkeypatch):
    """Routed regression: the entitlement 400 must not move any account's health.

    The unit tests call ``_handle_stream_error`` directly; this drives the whole
    HTTP route through selection, dispatch and failover with the exact upstream
    rejection observed in the incident — HTTP 400, message only, neither
    ``code`` nor ``type`` — and asserts the accounts that served the attempts
    keep ``error_count`` 0 and no error backoff. It fails if any transport path
    loses the message before the neutrality check or writes account health
    somewhere ``_handle_stream_error`` is not consulted.
    """
    account_id_1 = await _import_account(async_client, "acc_entitle_a", "entitle_a@example.com")
    account_id_2 = await _import_account(async_client, "acc_entitle_b", "entitle_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        raise ProxyResponseError(
            400,
            {"error": {"message": _MODEL_ENTITLEMENT_REJECTION_MESSAGE}},
            failure_phase="status",
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        lines = [line async for line in resp.aiter_lines() if line]

    # Failover is untouched: another account may hold a different entitlement,
    # so the rejection must fan out before it surfaces to the client.
    assert set(seen_account_ids) == {"acc_entitle_a", "acc_entitle_b"}
    events = _extract_events(lines)
    assert not any(e.get("type") == "response.completed" for e in events)

    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    for imported_account_id in (account_id_1, account_id_2):
        runtime = service._load_balancer._runtime.get(imported_account_id)
        assert runtime is None or runtime.error_count == 0, (
            f"model-scoped rejection must not move error_count for {imported_account_id}"
        )
        assert runtime is None or runtime.last_error_at is None, (
            f"model-scoped rejection must not arm error backoff for {imported_account_id}"
        )


@pytest.mark.asyncio
async def test_stream_genuine_400_failover_still_records_error_control(async_client, monkeypatch):
    """Control for the neutrality regression above: the same route and status
    with a non-entitlement message must keep penalizing, proving the runtime
    assertions would catch a health write."""
    account_id_1 = await _import_account(async_client, "acc_entitle_ctl_a", "entitle_ctl_a@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise ProxyResponseError(
            400,
            {"error": {"message": "Upstream request failed"}},
            failure_phase="status",
        )
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        [line async for line in resp.aiter_lines() if line]

    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    runtime = service._load_balancer._runtime.get(account_id_1)
    assert runtime is not None and runtime.error_count >= 1, (
        "a genuine upstream 400 on the identical path must keep recording an account error"
    )


# ===========================================================================
# Streaming — Non-server_error is NOT retried via transient path
# ===========================================================================


@pytest.mark.asyncio
async def test_stream_non_server_error_not_retried_as_transient(async_client, monkeypatch):
    """A 400-class error should NOT be caught by transient retry logic."""
    await _import_account(async_client, "acc_no_trans", "notrans@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "invalid_request_error",
                        "message": "bad request",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(failed) == 1

    # Should NOT retry — only 1 call
    assert call_count == 1


@pytest.mark.asyncio
async def test_stream_rate_limit_on_last_attempt_returns_actual_error(async_client, monkeypatch):
    """rate_limit_exceeded on the final outer attempt must yield the real error event,
    not a generic no_accounts message. Regression test for allow_retry flag separation.

    Needs 3 accounts so each outer attempt (max_attempts=3) reaches _stream_once:
    - Attempt 0: account A → rate_limit → mark A RATE_LIMITED → continue
    - Attempt 1: account B → rate_limit → mark B RATE_LIMITED → continue
    - Attempt 2 (last): account C → rate_limit → allow_retry=False →
      error event yielded to client → _TerminalStreamError → return
    """
    await _import_account(async_client, "acc_rl_a", "rla@example.com")
    await _import_account(async_client, "acc_rl_b", "rlb@example.com")
    await _import_account(async_client, "acc_rl_c", "rlc@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "slow down",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    # The last event the client sees should contain the actual rate_limit error
    last_event = events[-1] if events else {}
    error = last_event.get("response", {}).get("error", {})
    assert error.get("code") != "no_accounts", "Client received generic no_accounts instead of actual error"


@pytest.mark.asyncio
async def test_stream_mid_stream_error_is_surfaced_without_failover(async_client, monkeypatch):
    """Once bytes/events are emitted downstream, failover is forbidden; surface the mid-stream error."""
    await _import_account(async_client, "acc_midstream_a", "midstreama@example.com")
    await _import_account(async_client, "acc_midstream_b", "midstreamb@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_midstream_a":
            yield _sse_event({"type": "response.in_progress", "response": {"id": "resp_midstream"}})
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "rate_limit_exceeded", "message": "mid-stream limit"}},
                }
            )
            return
        yield _success_sse_event("resp_should_not_happen")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "rate_limit_exceeded"
    assert len(completed) == 0
    assert seen_account_ids == ["acc_midstream_a"]


@pytest.mark.asyncio
async def test_stream_http_500_after_text_is_surfaced_without_same_account_replay(async_client, monkeypatch):
    """A transport/status exception after visible text must not restart the answer on retry."""
    await _import_account(async_client, "acc_midtext_500", "midtext500@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        yield _sse_event({"type": "response.output_text.delta", "delta": "partial answer"})
        raise ProxyResponseError(
            500,
            openai_error("server_error", "late server error"),
            failure_phase="body",
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    deltas = [e for e in events if e.get("type") == "response.output_text.delta"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert [e.get("delta") for e in deltas] == ["partial answer"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "server_error"
    assert completed == []
    assert call_count == 1


@pytest.mark.asyncio
async def test_stream_http_429_after_text_is_surfaced_without_account_failover(async_client, monkeypatch):
    """A quota/rate-limit exception after visible text cannot be hidden by replaying on another account."""
    await _import_account(async_client, "acc_midtext_429_a", "midtext429a@example.com")
    await _import_account(async_client, "acc_midtext_429_b", "midtext429b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_midtext_429_a":
            yield _sse_event({"type": "response.output_text.delta", "delta": "visible"})
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="body",
            )
        yield _success_sse_event("resp_should_not_replay")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    deltas = [e for e in events if e.get("type") == "response.output_text.delta"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert [e.get("delta") for e in deltas] == ["visible"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "usage_limit_reached"
    assert completed == []
    assert seen_account_ids == ["acc_midtext_429_a"]


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_500_preserves_http_status(async_client, monkeypatch):
    """Non-streaming /v1/responses uses propagate_http_errors=True.
    After exhausting transient retries, the HTTP 500 status must be preserved
    (not swallowed into a generic SSE error)."""
    await _import_account(async_client, "acc_prop_500", "prop500@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        raise ProxyResponseError(
            500,
            openai_error("server_error", "An error occurred while processing your request."),
            failure_phase="status",
        )
        yield ""  # make it a generator  # pragma: no cover

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi"}
    response = await async_client.post("/v1/responses", json=payload)
    # Must preserve the upstream 500, not 503/502
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"
    # Should have retried on same account before giving up
    assert call_count == 3


# ===========================================================================
# Compact — HTTP 500 retry
# ===========================================================================


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_second_try_same_account(async_client, monkeypatch):
    """Compact 500 on 1st call → backoff retry → success on 2nd, same account."""
    await _import_account(async_client, "acc_c500_1", "c500@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "An error occurred while processing your request."),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_third_try(async_client, monkeypatch):
    """Compact 500 x2, success on 3rd — all same account."""
    await _import_account(async_client, "acc_c500_3", "c500_3@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count <= 2:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    assert len(seen_account_ids) == 3
    assert len(set(seen_account_ids)) == 1


@pytest.mark.asyncio
async def test_compact_500_exhausts_retries_then_failover(async_client, monkeypatch):
    """Compact 500 x3 on account A → failover → success on account B."""
    await _import_account(async_client, "acc_cfo_a", "cfo_a@example.com")
    await _import_account(async_client, "acc_cfo_b", "cfo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_cfo_a":
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    a_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_compact_quota_exceeded_transparent_failover(async_client, monkeypatch):
    """quota_exceeded on A should fail over to B before response write."""
    await _import_account(async_client, "acc_compact_quota_a", "compactquotaa@example.com")
    await _import_account(async_client, "acc_compact_quota_b", "compactquotab@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_compact_quota_a":
            raise ProxyResponseError(
                429,
                openai_error("quota_exceeded", "quota exceeded"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_compact_quota_a", "acc_compact_quota_b"]

    async with SessionLocal() as session:
        account_id = generate_unique_account_id("acc_compact_quota_a", "compactquotaa@example.com")
        account_a = await session.get(Account, account_id)
        assert account_a is not None
        await session.refresh(account_a)
        assert account_a.status == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_compact_500_all_accounts_exhausted(async_client, monkeypatch):
    """500 on all accounts → error returned to client."""
    await _import_account(async_client, "acc_call_a", "call_a@example.com")

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            500,
            openai_error("server_error", "persistent error"),
            failure_phase="status",
            retryable_same_contract=True,
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    # After exhausting all accounts, the load balancer returns 503 no_accounts
    assert response.status_code in (500, 503)


@pytest.mark.asyncio
async def test_compact_non_500_error_not_retried_as_transient(async_client, monkeypatch):
    """A 400 error should NOT be retried via the transient retry path."""
    await _import_account(async_client, "acc_c400", "c400@example.com")

    call_count = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        raise ProxyResponseError(
            400,
            openai_error("invalid_request_error", "bad request"),
            failure_phase="status",
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 400

    # Should NOT retry — only 1 call
    assert call_count == 1


@pytest.mark.asyncio
async def test_compact_502_still_uses_safe_retry_budget(async_client, monkeypatch):
    """502 errors should still use the existing safe_retry_budget (not transient path)."""
    await _import_account(async_client, "acc_c502", "c502@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "bad gateway"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    # safe_retry_budget=1 → retried once, same account
    assert call_count == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_sticky_503_unknown_code_excludes_failing_account_on_failover(async_client, monkeypatch):
    await _import_account(async_client, "acc_sticky_503_a", "sticky503a@example.com")
    await _import_account(async_client, "acc_sticky_503_b", "sticky503b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_sticky_503_a":
            raise ProxyResponseError(
                503,
                openai_error("bad_gateway", "Bad gateway"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=payload,
        headers={"x-codex-session-id": "sticky-session-503"},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_sticky_503_a", "acc_sticky_503_b"]


# ===========================================================================
# Streaming — usage_limit_reached requests an immediate usage refresh (#2123 WP-F)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.usage_refresh_request_path
async def test_stream_usage_limit_requests_immediate_refresh_so_pool_reports_exhaustion(
    async_client, app_instance, monkeypatch
):
    """A streamed usage_limit_reached marks the account rate limited *and* writes the >= 100 %
    usage row seconds later without a scheduler tick, so the next selection reports the
    structured pool exhaustion with the row's reset time instead of waiting for the next
    refresh interval.

    Runs with the production selection-cache TTL (pytest defaults it to 0): ``mark_rate_limit``
    invalidates the cache before the row exists, and a selection in between repopulates it
    without usage evidence, so the refresh must invalidate again once the row is written.
    The cross-replica invalidation poller is detached so its echo of the earlier
    ``mark_rate_limit`` bump cannot stand in for the refresh's own invalidation.
    """
    usage_updater_module._clear_usage_refresh_state()
    monkeypatch.setattr(account_cache_module, "get_cache_invalidation_poller", lambda: None)
    selection_cache = get_account_selection_cache()
    monkeypatch.setattr(selection_cache, "_ttl_seconds", 5)
    selection_cache.invalidate()
    raw_account_id = "acc_usage_limit_refresh"
    account_id = await _import_account(async_client, raw_account_id, "usage-limit-refresh@example.com")
    reset_at = int(time.time()) + 1800

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise ProxyResponseError(
            429,
            openai_error("usage_limit_reached", "usage limit reached"),
            failure_phase="status",
        )
        yield  # pragma: no cover - makes this an async generator

    fetched: list[str | None] = []
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def fake_fetch_usage(*, access_token, account_id, route=None, allow_direct_egress=True):
        fetched.append(account_id)
        fetch_started.set()
        await release_fetch.wait()
        return UsagePayload(
            plan_type="plus",
            rate_limit=RateLimitPayload(
                primary_window=UsageWindow(used_percent=100.0, reset_at=reset_at, limit_window_seconds=18000),
                secondary_window=UsageWindow(
                    used_percent=35.0,
                    reset_at=reset_at + 6 * 86400,
                    limit_window_seconds=604800,
                ),
            ),
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)
    monkeypatch.setattr(proxy_api_module, "_STREAM_STARTUP_ERROR_PROBE_SECONDS", 30.0)
    monkeypatch.setattr(usage_updater_module, "fetch_usage", fake_fetch_usage)

    async def latest_primary_row():
        async with SessionLocal() as session:
            return await UsageRepository(session).latest_entry_for_account(account_id, window="primary")

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    try:
        assert await latest_primary_row() is None
        first = await async_client.post("/backend-api/codex/responses", json=payload)
        assert first.status_code == 429
        assert first.json()["error"]["code"] == "usage_limit_reached"
        await asyncio.wait_for(fetch_started.wait(), timeout=5)
        assert fetched == [raw_account_id]
        assert await latest_primary_row() is None

        # Production race: a selection between ``mark_rate_limit`` and the refresh's row write
        # repopulates the cache from a row set that carries no usage evidence yet.
        load_balancer = app_instance.state.proxy_service._load_balancer
        await load_balancer._load_selection_inputs(model=payload["model"])
        assert selection_cache._cache, "the stale selection must be cached before the row lands"
        stale_generation = selection_cache.generation

        release_fetch.set()
        # The refresh is a tracked background task: poll briefly for its row instead of a
        # scheduler tick (USAGE_REFRESH_INTERVAL_SECONDS).
        latest = None
        deadline = time.monotonic() + 5.0
        while latest is None and time.monotonic() < deadline:
            latest = await latest_primary_row()
            if latest is None:
                await asyncio.sleep(0.02)
        assert latest is not None, "a streamed usage_limit_reached must request an immediate usage refresh"
        assert latest.used_percent == 100.0
        assert latest.reset_at == reset_at

        deadline = time.monotonic() + 5.0
        while selection_cache.generation == stale_generation and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert selection_cache.generation > stale_generation, "the written row must invalidate the selection cache"
        assert selection_cache._cache == {}

        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            assert account is not None
            assert account.status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)

        # With the >= 100 % row written and the stale selection dropped, the pool is
        # usage-proven exhausted on the very next selection and surfaces the structured
        # failure with the row's reset time -- without waiting out the cache TTL.
        second = await async_client.post("/backend-api/codex/responses", json=payload)
        assert second.status_code == 429
        error = second.json()["error"]
        assert error["code"] == "usage_limit_reached"
        assert error["type"] == "usage_limit_reached"
        assert error["resets_at"] == reset_at
        assert fetched == [raw_account_id], "the second selection failure must not fetch upstream again"
    finally:
        release_fetch.set()
        usage_updater_module._clear_usage_refresh_state()
        selection_cache.invalidate()


# ===========================================================================
# Streaming — upstream reasoning-replay rejections are counted once per failure (#2123 CP-15)
# ===========================================================================


_REASONING_REPLAY_MESSAGE = (
    "Item with id 'rs_0f3a' of type 'reasoning' was provided without its required following item."
)


@pytest.mark.parametrize(
    ("upstream_shape", "message", "expected_increments"),
    [
        ("status_400", _REASONING_REPLAY_MESSAGE, 1),
        ("error_frame", _REASONING_REPLAY_MESSAGE, 1),
        ("error_frame_enveloped", _REASONING_REPLAY_MESSAGE, 1),
        ("response_failed_frame", _REASONING_REPLAY_MESSAGE, 1),
        ("error_frame", "Selected model is at capacity. Please try a different model.", 0),
        ("response_failed_frame", "No tool output found for function call call_abc.", 0),
    ],
)
@pytest.mark.asyncio
async def test_stream_reasoning_replay_rejection_counted_once_for_status_and_terminal_frames(
    async_client, monkeypatch, upstream_shape: str, message: str, expected_increments: int
):
    """invalid_request_error is never penalized, so terminal ``error``/``response.failed`` frames
    never reach ``_handle_stream_error``; the counter must fire at frame classification for them
    and exactly once for the HTTP-400 status path."""
    counter = MagicMock()
    monkeypatch.setattr(proxy_observability_module, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(proxy_observability_module, "upstream_reasoning_replay_400_total", counter)
    await _import_account(async_client, "acc_reasoning_replay", "reasoning-replay@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        if upstream_shape == "status_400":
            raise ProxyResponseError(
                400,
                openai_error("invalid_request_error", message),
                failure_phase="status",
            )
        if upstream_shape == "error_frame":
            yield _sse_event({"type": "error", "code": "invalid_request_error", "message": message})
        elif upstream_shape == "error_frame_enveloped":
            yield _sse_event({"type": "error", "error": {"code": "invalid_request_error", "message": message}})
        else:
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "invalid_request_error", "message": message}},
                }
            )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    if upstream_shape == "status_400":
        response = await async_client.post("/backend-api/codex/responses", json=payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request_error"
    else:
        async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
            assert resp.status_code == 200
            lines = [line async for line in resp.aiter_lines() if line]
        events = _extract_events(lines)
        terminal = [event for event in events if event.get("type") in {"error", "response.failed"}]
        assert len(terminal) == 1

    assert counter.inc.call_count == expected_increments

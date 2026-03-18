"""Tests for transient (500 server_error) retry logic.

Covers:
- Streaming: SSE-level server_error → same-account retry → failover
- Streaming: HTTP-level 500 → same-account retry → failover
- Compact: HTTP 500 → same-account retry with backoff → account failover
- Budget exhaustion during inner retry
- Non-500 errors are not intercepted by transient retry
"""

from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload

pytestmark = pytest.mark.integration


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
            events.append(json.loads(line[6:]))
    return events


# ===========================================================================
# Streaming — SSE-level server_error
# ===========================================================================


@pytest.mark.asyncio
async def test_stream_server_error_succeeds_on_second_try_same_account(async_client, monkeypatch):
    """server_error on 1st SSE event → inner retry on same account → success on 2nd try."""
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
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    # Both calls should be to the same account
    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_server_error_exhausts_inner_retries_then_failover(async_client, monkeypatch):
    """server_error x3 on account A → penalize A → failover to account B → success."""
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
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    # 3 retries on account A + 1 success on account B
    a_calls = [aid for aid in seen_account_ids if aid == "acc_trans_fo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_trans_fo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


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
async def test_stream_server_error_succeeds_on_third_try(async_client, monkeypatch):
    """server_error on tries 1 and 2, success on try 3 — all same account."""
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
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    # All 3 calls to the same account
    assert len(seen_account_ids) == 3
    assert len(set(seen_account_ids)) == 1


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

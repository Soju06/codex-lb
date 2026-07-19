"""Integration tests for ``POST /backend-api/files`` and the finalize endpoint.

These tests stub the upstream client functions
(``proxy_module.core_create_file`` / ``core_finalize_file``) so we
exercise the full FastAPI route -> service -> account-selection ->
upstream-client chain without hitting the real ChatGPT backend. The
``async_client`` fixture lives in ``tests/conftest.py`` and gives us a
fully-wired httpx client against the FastAPI app.
"""

from __future__ import annotations

import base64
import json
from typing import cast

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth.refresh import RefreshError
from app.core.clients.files import FileProxyError
from app.core.clients.proxy import ProxyResponseError

pytestmark = pytest.mark.integration


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


async def _import_account(async_client, account_id: str, email: str) -> None:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_backend_files_create_forwards_payload_and_returns_upstream_json(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_create", "files-create@example.com")

    captured: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["payload"] = payload
        captured["access_token"] = access_token
        captured["account_id"] = account_id
        return {"file_id": "file_xyz", "upload_url": "https://blob.example/sas?token=abc"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "page.pdf", "file_size": 1024, "use_case": "codex"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"file_id": "file_xyz", "upload_url": "https://blob.example/sas?token=abc"}
    assert captured["payload"] == {"file_name": "page.pdf", "file_size": 1024, "use_case": "codex"}
    assert captured["access_token"] == "access-token"
    assert captured["account_id"] == "acc_files_create"


@pytest.mark.asyncio
async def test_backend_files_create_defaults_use_case_to_codex(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_default_uc", "files-default-uc@example.com")
    captured: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["payload"] = payload
        return {"file_id": "f", "upload_url": "https://blob.example/sas"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 200
    captured_payload = cast(dict[str, object], captured["payload"])
    assert captured_payload["use_case"] == "codex"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_zero_file_size(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 0},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_oversized_file(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "huge.bin", "file_size": 512 * 1024 * 1024 + 1},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_rejects_missing_file_name(async_client):
    response = await async_client.post(
        "/backend-api/files",
        json={"file_size": 100},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_backend_files_create_maps_upstream_error(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_upstream_err", "files-upstream-err@example.com")

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        raise FileProxyError(
            413,
            {"error": {"message": "file too large", "type": "invalid_request_error", "code": "file_too_large"}},
        )

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "file_too_large"


@pytest.mark.asyncio
async def test_backend_files_create_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_invalidated_a", "files-invalidated-a@example.com")
    await _import_account(async_client, "acc_files_invalidated_b", "files-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del payload, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"file_id": "file_recovered", "upload_url": "https://blob.example/recovered"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["file_id"] == "file_recovered"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_backend_files_create_post_401_forced_refresh_claim_timeout_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Regression (P2 forced-refresh surfaces): when the file-upload post-401
    forced refresh on the failover account hits a transient cross-replica
    refresh-CLAIM-CONTENTION timeout, the surface routes through
    ``_ensure_fresh_with_budget_or_auth_error``, which MUST surface a retryable
    ``upstream_unavailable`` (502) rather than a bogus 401 ``invalid_api_key``."""
    await _import_account(async_client, "acc_files_claim_a", "files-claim-a@example.com")
    await _import_account(async_client, "acc_files_claim_b", "files-claim-b@example.com")

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del payload, headers, access_token, base_url, session, account_id, kwargs
        # Always 401 so the surface fails over and forces a refresh on the peer.
        raise FileProxyError(
            401,
            {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
        )

    first_fresh_account: dict[str, str | None] = {"id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_fresh_account["id"] is None:
            first_fresh_account["id"] = account.id
        if account.id != first_fresh_account["id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_backend_files_finalize_returns_upstream_payload(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize", "files-finalize@example.com")

    captured: dict[str, object] = {}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        captured["file_id"] = file_id
        captured["account_id"] = account_id
        return {
            "status": "success",
            "download_url": "https://download.example/file_done",
            "file_name": "page.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 1024,
        }

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/file_done/uploaded")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["download_url"] == "https://download.example/file_done"
    assert captured["file_id"] == "file_done"
    assert captured["account_id"] == "acc_files_finalize"


@pytest.mark.asyncio
async def test_backend_files_finalize_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize_invalidated_a", "files-finalize-invalidated-a@example.com")
    await _import_account(async_client, "acc_files_finalize_invalidated_b", "files-finalize-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del file_id, headers, access_token, base_url, session
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"status": "success", "download_url": "https://download.example/recovered"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post("/backend-api/files/file_recovered/uploaded")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_backend_files_finalize_propagates_retry_status(async_client, monkeypatch):
    """Once the finalize loop in the upstream client gives up with a
    final ``retry`` status, we return that payload verbatim so the
    caller can decide what to do (mirrors upstream Codex CLI behaviour)."""
    await _import_account(async_client, "acc_files_finalize_retry", "files-finalize-retry@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        return {"status": "retry"}

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/file_pending/uploaded")
    assert response.status_code == 200
    assert response.json() == {"status": "retry"}


@pytest.mark.asyncio
async def test_backend_files_finalize_maps_upstream_404(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize_missing", "files-finalize-missing@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        raise FileProxyError(
            404,
            {"error": {"message": "file not found", "type": "invalid_request_error", "code": "not_found"}},
        )

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/missing_id/uploaded")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,method,payload",
    [
        ("/backend-api/files", "post", {"file_name": "x.png", "file_size": 1}),
        ("/backend-api/files/file_x/uploaded", "post", None),
    ],
)
async def test_backend_files_routes_require_api_key_when_enabled(async_client, endpoint, method, payload):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    if payload is None:
        response = await async_client.post(endpoint)
    else:
        response = await async_client.post(endpoint, json=payload)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_backend_files_finalize_pins_to_create_account(async_client, monkeypatch):
    """Regression for cross-account finalize routing.

    Two accounts are imported and the upstream contract is
    account-scoped (``chatgpt-account-id``). After ``create_file``
    routes through ``acc_pin_a``, the matching ``finalize_file`` for
    the same ``file_id`` must be routed to ``acc_pin_a`` even if the
    load balancer would otherwise pick a different account.
    """
    await _import_account(async_client, "acc_pin_a", "pin-a@example.com")
    await _import_account(async_client, "acc_pin_b", "pin-b@example.com")

    create_seen: dict[str, object] = {}
    finalize_seen: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_seen["account_id"] = account_id
        return {"file_id": "file_pinned", "upload_url": "https://blob.example/sas?token=p"}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        finalize_seen["account_id"] = account_id
        finalize_seen["file_id"] = file_id
        return {"status": "success", "download_url": "https://blob.example/dl/p"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "a.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200
    creating_account = create_seen["account_id"]
    assert creating_account in {"acc_pin_a", "acc_pin_b"}

    finalize_resp = await async_client.post("/backend-api/files/file_pinned/uploaded")
    assert finalize_resp.status_code == 200
    assert finalize_seen["file_id"] == "file_pinned"
    # The pin from create_file must drive finalize routing to the same
    # upstream chatgpt-account-id, regardless of which account the
    # load balancer would have picked otherwise.
    assert finalize_seen["account_id"] == creating_account


@pytest.mark.asyncio
async def test_backend_files_finalize_pinned_401_retry_fails_closed(async_client, monkeypatch):
    """Pinned finalize must not fail over to a different account after auth failure."""

    await _import_account(async_client, "acc_pin_401_a", "pin-401-a@example.com")
    await _import_account(async_client, "acc_pin_401_b", "pin-401-b@example.com")

    create_seen: dict[str, str | None] = {}
    finalize_account_ids: list[str | None] = []

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_seen["account_id"] = account_id
        return {"file_id": "file_pinned_401", "upload_url": "https://blob.example/sas?token=pin401"}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del file_id, headers, access_token, base_url, session
        finalize_account_ids.append(account_id)
        if account_id == create_seen["account_id"]:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"status": "success", "download_url": "https://blob.example/wrong-account"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "a.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200
    creating_account = create_seen["account_id"]
    assert creating_account in {"acc_pin_401_a", "acc_pin_401_b"}

    finalize_resp = await async_client.post("/backend-api/files/file_pinned_401/uploaded")
    assert finalize_resp.status_code == 401
    assert finalize_resp.json()["error"]["code"] == "invalid_api_key"
    assert finalize_account_ids == [creating_account, creating_account]


@pytest.mark.asyncio
async def test_resolve_file_account_for_responses_returns_pin_when_no_other_affinity(async_client):
    """Regression for cross-account ``input_file.file_id`` routing.

    The upstream file API is account-scoped (``chatgpt-account-id``),
    so a ``/v1/responses`` request that references a previously-uploaded
    ``file_id`` must land on the same account that registered the file;
    otherwise upstream rejects it with not-found / 401. The contract
    is exercised at two layers: the standalone resolver helper
    (used by HTTP / compact paths) and the websocket-prep code path
    that mirrors the same lookup into ``request_state.preferred_account_id``.
    """
    await _import_account(async_client, "acc_resp_a", "resp-a@example.com")
    await _import_account(async_client, "acc_resp_b", "resp-b@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    # Simulate a successful POST /backend-api/files completing under
    # acc_resp_a -- the pin table is the contract verified here.
    await service._pin_file_account("file_response_pin", "acc_resp_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Summarize the file."},
                        {"type": "input_file", "file_id": "file_response_pin"},
                    ],
                }
            ],
            "prompt_cache_key": "file-response-soft-cache",
        }
    )
    soft_headers = {"session_id": "file-response-soft-session"}
    resolved = await service._resolve_file_account_for_responses(payload, soft_headers)
    assert resolved == "acc_resp_a"
    # The websocket prep path also surfaces the pin via the same helper.
    ws_payload = dict(payload.to_payload())
    ws_payload["type"] = "response.create"
    prepared = await service._prepare_websocket_response_create_request(
        ws_payload,
        headers=soft_headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=None,
    )
    assert prepared.request_state.preferred_account_id == "acc_resp_a"
    assert prepared.affinity_policy.codex_session_source == "session_header"


@pytest.mark.asyncio
async def test_v1_responses_file_id_pin_overrides_prompt_cache_key(async_client, monkeypatch):
    """A prompt-cache key is locality; an account-scoped file is ownership."""
    await _import_account(async_client, "acc_pck_a", "pck-a@example.com")
    await _import_account(async_client, "acc_pck_b", "pck-b@example.com")

    create_account_holder: dict[str, str] = {}
    stream_account_ids: list[str] = []

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_account_holder["account_id"] = account_id
        return {"file_id": "file_pck", "upload_url": "https://blob.example/sas?t=p"}

    async def fake_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **kwargs,
    ):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        stream_account_ids.append(account_id)
        yield (
            "event: response.completed\ndata: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_pck",
                        "object": "response",
                        "status": "completed",
                        "created_at": 0,
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                }
            )
            + "\n\n"
        )

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200

    response = await async_client.post(
        "/v1/responses",
        headers={"session_id": "file-soft-session"},
        json={
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Continue."},
                        {"type": "input_file", "file_id": "file_pck"},
                    ],
                }
            ],
            "prompt_cache_key": "thread-123",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert stream_account_ids == [create_account_holder["account_id"]]

    # Keep the resolver assertion as a narrow diagnostic for precedence drift.
    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Continue."},
                        {"type": "input_file", "file_id": "file_pck"},
                    ],
                }
            ],
            "prompt_cache_key": "thread-123",
        }
    )
    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved is not None


@pytest.mark.asyncio
async def test_derived_prompt_cache_key_does_not_block_file_id_pin(async_client):
    """Regression: a ``prompt_cache_key`` that the proxy itself derived
    (via ``_sticky_key_for_responses_request`` when openai cache
    affinity is on) must NOT block file-pin routing -- only an
    *explicitly client-supplied* key should win.

    We simulate the derivation by setting the field on the model
    *programmatically* (without including it in ``model_fields_set``).
    The helper must still return the file_id pin.
    """
    await _import_account(async_client, "acc_derived_a", "derived-a@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_derived", "acc_derived_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "What's in this?"},
                        {"type": "input_file", "file_id": "file_derived"},
                    ],
                }
            ],
        }
    )
    # Simulate the affinity helper deriving a cache key onto the
    # payload without it being part of the original client input.
    # The proxy's affinity-helper assigns to the attribute directly,
    # but it does so without marking the field as explicitly set
    # (the tracker is used to distinguish client-supplied keys
    # from derived ones). Reproduce that contract by removing the
    # field from ``model_fields_set`` after the assignment.
    payload.prompt_cache_key = "derived-key-load-balancer-set"
    payload.model_fields_set.discard("prompt_cache_key")
    assert "prompt_cache_key" not in payload.model_fields_set

    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved == "acc_derived_a"


@pytest.mark.asyncio
async def test_turn_state_does_not_hide_file_id_pin_resolution(async_client):
    """Every file owner is resolved before hard-source consistency checks."""
    await _import_account(async_client, "acc_ts_a", "ts-a@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.affinity import ensure_downstream_turn_state

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_synth_ts", "acc_ts_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe."},
                        {"type": "input_file", "file_id": "file_synth_ts"},
                    ],
                }
            ],
        }
    )

    # Synthesizer-generated turn state does not hide the pin.
    synth_turn_state = ensure_downstream_turn_state({})
    assert synth_turn_state.startswith("turn_")
    headers_synth = {"x-codex-turn-state": synth_turn_state}
    resolved = await service._resolve_file_account_for_responses(payload, headers_synth)
    assert resolved == "acc_ts_a"

    # Client turn state also cannot hide the pin. The product path later
    # verifies that both hard sources agree instead of assigning precedence.
    headers_client = {"x-codex-turn-state": "client-conversation-handle-42"}
    resolved_with_client_turn_state = await service._resolve_file_account_for_responses(payload, headers_client)
    assert resolved_with_client_turn_state == "acc_ts_a"


@pytest.mark.asyncio
async def test_file_owner_resolution_preserves_unpinned_and_rejects_partial_or_cross_account_pins(async_client):
    await _import_account(async_client, "acc_file_strict_a", "file-strict-a@example.com")
    await _import_account(async_client, "acc_file_strict_b", "file-strict-b@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_strict_a", "acc_file_strict_a")
    await service._pin_file_account("file_strict_b", "acc_file_strict_b")

    def payload_for(*file_ids: str) -> ResponsesRequest:
        return ResponsesRequest.model_validate(
            {
                "model": "gpt-5.2",
                "instructions": "Read the files.",
                "input": [{"type": "input_file", "file_id": file_id} for file_id in file_ids],
            }
        )

    assert await service._resolve_file_account_for_responses(payload_for("file_missing"), {}) is None

    with pytest.raises(ProxyResponseError) as partial_exc:
        await service._resolve_file_account_for_responses(payload_for("file_strict_a", "file_missing"), {})
    assert partial_exc.value.payload["error"]["code"] == "file_owner_unavailable"

    with pytest.raises(ProxyResponseError) as conflict_exc:
        await service._resolve_file_account_for_responses(payload_for("file_strict_a", "file_strict_b"), {})
    assert conflict_exc.value.payload["error"]["code"] == "continuity_owner_conflict"


@pytest.mark.asyncio
async def test_file_id_pin_overrides_bare_session_header_aliases(async_client):
    """Bare process-session aliases cannot hide an account-scoped file owner."""
    await _import_account(async_client, "acc_session_file_a", "session-file-a@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_session_alias", "acc_session_file_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe."},
                        {"type": "input_file", "file_id": "file_session_alias"},
                    ],
                }
            ],
        }
    )

    for headers in (
        {"session-id": "codex-session-123"},
        {"thread-id": "codex-thread-123"},
        {"x-codex-session-id": "codex-session-123"},
    ):
        resolved = await service._resolve_file_account_for_responses(payload, headers)
        assert resolved == "acc_session_file_a"

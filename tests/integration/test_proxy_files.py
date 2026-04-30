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

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.files import FileProxyError

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

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None):
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

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None):
        captured["payload"] = payload
        return {"file_id": "f", "upload_url": "https://blob.example/sas"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)

    response = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 1},
    )

    assert response.status_code == 200
    assert captured["payload"]["use_case"] == "codex"  # type: ignore[index]


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

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None):
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
async def test_backend_files_finalize_returns_upstream_payload(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize", "files-finalize@example.com")

    captured: dict[str, object] = {}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None):
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
async def test_backend_files_finalize_propagates_retry_status(async_client, monkeypatch):
    """Once the finalize loop in the upstream client gives up with a
    final ``retry`` status, we return that payload verbatim so the
    caller can decide what to do (mirrors upstream Codex CLI behaviour)."""
    await _import_account(async_client, "acc_files_finalize_retry", "files-finalize-retry@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None):
        return {"status": "retry"}

    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    response = await async_client.post("/backend-api/files/file_pending/uploaded")
    assert response.status_code == 200
    assert response.json() == {"status": "retry"}


@pytest.mark.asyncio
async def test_backend_files_finalize_maps_upstream_404(async_client, monkeypatch):
    await _import_account(async_client, "acc_files_finalize_missing", "files-finalize-missing@example.com")

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None):
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

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None):
        create_seen["account_id"] = account_id
        return {"file_id": "file_pinned", "upload_url": "https://blob.example/sas?token=p"}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None):
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
        }
    )
    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved == "acc_resp_a"
    # The websocket prep path also surfaces the pin via the same helper:
    prepared = await service._prepare_websocket_response_create_request(
        payload.to_payload() | {"type": "response.create"},
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=None,
    )
    assert prepared.request_state.preferred_account_id == "acc_resp_a"


@pytest.mark.asyncio
async def test_v1_responses_prompt_cache_key_overrides_file_id_pin(async_client, monkeypatch):
    """An explicit ``prompt_cache_key`` is a stronger affinity signal
    than the file_id pin and must continue to drive routing.

    The pin still gets recorded by ``create_file``, but the
    ``/v1/responses`` request with a ``prompt_cache_key`` is allowed to
    land on a different account because a cache key implies an
    existing conversation continuation.
    """
    await _import_account(async_client, "acc_pck_a", "pck-a@example.com")
    await _import_account(async_client, "acc_pck_b", "pck-b@example.com")

    create_account_holder: dict[str, str] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None):
        create_account_holder["account_id"] = account_id
        return {"file_id": "file_pck", "upload_url": "https://blob.example/sas?t=p"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
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

    # Use the helper directly to verify the precedence rule rather than
    # inspecting the load-balancer's choice (which depends on capacity
    # weights / sticky tables that are repo-scoped). The contract:
    # ``_resolve_file_account_for_responses`` must return ``None`` when
    # a stronger affinity signal is set.
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
    assert resolved is None

from __future__ import annotations

import base64
import json
from dataclasses import replace

import bcrypt
import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth.dashboard_access import (
    PRESET_ROLE_IDS,
    DashboardAuthMode,
    Permission,
    PresetRoleSlug,
    Scope,
    admin_principal,
    guest_principal,
)
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.dependencies import validate_dashboard_session
from app.core.openai.models import OpenAIResponsePayload
from app.core.types import JsonValue
from app.db.models import DashboardUser
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_no_account_failure_records_resolved_affinity(async_client):
    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "synthetic prompt", "prompt_cache_key": "abc", "stream": True},
        headers={"x-request-id": "affinity-no-account"},
    )
    assert "no_accounts" in response.text
    logs = await async_client.get("/api/request-logs")
    assert logs.status_code == 200
    row = next(row for row in logs.json()["requests"] if row["requestId"] == "affinity-no-account")
    assert row["stickyKeySource"] == "payload"
    assert row["stickyKind"] == "prompt_cache"
    assert row["stickyKeyHash"] == "ba7816bf8f01cfea"


async def _import_synthetic_account(client):
    claims = {
        "email": "affinity@example.invalid",
        "chatgpt_account_id": "affinity-test-account",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    auth = {"tokens": {"idToken": f"header.{encoded}.sig", "accessToken": "synthetic", "refreshToken": "synthetic"}}
    response = await client.post("/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth))})
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses", "/v1/responses/compact"])
async def test_success_records_stable_and_distinct_resolved_keys(async_client, monkeypatch, path):
    await _import_synthetic_account(async_client)
    sent_keys = []

    async def stream(payload, *args, **kwargs):
        sent_keys.append(payload.prompt_cache_key)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_affinity","status":"completed","output":[]}}\n\n'
        )

    async def compact(payload, *args, **kwargs):
        sent_keys.append(payload.prompt_cache_key)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", compact)
    for index, key in enumerate(["abc", "abc", "hello"]):
        payload: dict[str, JsonValue] = {
            "model": "gpt-5.4",
            "instructions": "synthetic prompt",
            "input": [],
            "prompt_cache_key": key,
        }
        if not path.endswith("compact"):
            payload["stream"] = True
        response = await async_client.post(path, json=payload, headers={"x-request-id": f"affinity-success-{index}"})
        assert response.status_code == 200
        assert "response.failed" not in response.text

    assert sent_keys == ["abc", "abc", "hello"]
    logs = await async_client.get("/api/request-logs")
    rows = logs.json()["requests"]
    assert len(rows) == 3
    assert [row["stickyKeyHash"] for row in rows].count("ba7816bf8f01cfea") == 2
    assert [row["stickyKeyHash"] for row in rows].count("2cf24dba5fb0a30e") == 1
    assert all(row["stickyKeySource"] == "payload" and row["stickyKind"] == "prompt_cache" for row in rows)
    assert "synthetic prompt" not in logs.text


@pytest.mark.asyncio
async def test_compact_failure_records_resolved_affinity(async_client):
    response = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.4", "input": [], "prompt_cache_key": "abc"},
        headers={"x-request-id": "affinity-compact-no-account"},
    )
    assert response.status_code == 503
    logs = await async_client.get("/api/request-logs")
    row = next(row for row in logs.json()["requests"] if row["requestId"] == "affinity-compact-no-account")
    assert row["stickyKeySource"] == "payload"
    assert row["stickyKind"] == "prompt_cache"
    assert row["stickyKeyHash"] == "ba7816bf8f01cfea"


@pytest.mark.asyncio
async def test_derived_key_is_observed_without_persisting_prompt(async_client, monkeypatch):
    await _import_synthetic_account(async_client)
    sent_keys = []

    async def stream(payload, *args, **kwargs):
        sent_keys.append(payload.prompt_cache_key)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_derived","status":"completed","output":[]}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    for _ in range(2):
        response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": "hello", "stream": True})
        assert "response.completed" in response.text
    # The derived key is anchored to the thread rather than to a hash of its
    # text, so pin the properties the observation depends on -- one key reused
    # across both turns, in the anchored ``v2t-`` namespace -- instead of the
    # literal, which moves with the derivation.
    assert len(sent_keys) == 2
    assert sent_keys[0] == sent_keys[1]
    derived_key = sent_keys[0]
    assert derived_key is not None and derived_key.startswith("v2t-")
    logs = await async_client.get("/api/request-logs")
    rows = logs.json()["requests"]
    assert len(rows) == 2
    hashes = {row["stickyKeyHash"] for row in rows}
    assert len(hashes) == 1
    for row in rows:
        assert row["stickyKeySource"] == "derived"
        assert row["stickyKeyHash"]
    assert "hello" not in logs.text
    assert derived_key not in logs.text


@pytest.mark.asyncio
async def test_listing_distinguishes_no_affinity_from_unavailable_metadata(async_client):
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        for source in (None, "none"):
            await repository.add_log(
                account_id=None,
                request_id=f"affinity-{source}",
                model="gpt-5.4",
                input_tokens=None,
                output_tokens=None,
                latency_ms=0,
                status="error",
                error_code="no_accounts",
                sticky_key_source=source,
            )
    logs = await async_client.get("/api/request-logs")
    rows = {row["requestId"]: row for row in logs.json()["requests"]}
    assert rows["affinity-none"]["stickyKeySource"] == "none"
    assert rows["affinity-None"]["stickyKeySource"] is None
    assert all(row["stickyKind"] is None and row["stickyKeyHash"] is None for row in rows.values())


@pytest.mark.asyncio
async def test_keyed_upstream_failure_keeps_affinity_on_settled_row(async_client, monkeypatch):
    await _import_synthetic_account(async_client)
    key_response = await async_client.post("/api/api-keys", json={"name": "synthetic-affinity-key"})
    assert key_response.status_code == 200
    key = key_response.json()
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200

    async def stream(payload, *args, **kwargs):
        assert payload.prompt_cache_key == "abc"
        yield 'data: {"type":"response.created","response":{"id":"resp_affinity_failed","status":"in_progress"}}\n\n'
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_affinity_failed","status":"failed",'
            '"error":{"code":"invalid_prompt","type":"invalid_request_error","message":"Synthetic error"}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "synthetic", "prompt_cache_key": "abc", "stream": True},
        headers={"authorization": f"Bearer {key['key']}"},
    )
    assert "invalid_prompt" in response.text
    logs = await async_client.get("/api/request-logs")
    rows = logs.json()["requests"]
    assert len(rows) == 1
    row = rows[0]
    assert row["apiKeyId"] == key["id"]
    assert row["errorCode"] == "invalid_prompt"
    assert row["stickyKeySource"] == "payload"
    assert row["stickyKind"] == "prompt_cache"
    assert row["stickyKeyHash"] == "ba7816bf8f01cfea"
    assert key["key"] not in logs.text


@pytest.mark.asyncio
async def test_guest_listing_hides_affinity_correlation_metadata(async_client, app_instance):
    await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "synthetic", "prompt_cache_key": "abc", "stream": True},
    )
    app_instance.dependency_overrides[validate_dashboard_session] = guest_principal
    try:
        guest = await async_client.get("/api/request-logs")
    finally:
        app_instance.dependency_overrides.pop(validate_dashboard_session)
    assert guest.status_code == 200
    row = guest.json()["requests"][0]
    assert row["stickyKeySource"] is None
    assert row["stickyKind"] is None
    assert row["stickyKeyHash"] is None
    admin = await async_client.get("/api/request-logs")
    assert admin.json()["requests"][0]["stickyKeyHash"] == "ba7816bf8f01cfea"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/backend-api/codex/responses/compact"])
async def test_session_selection_key_is_logged_instead_of_payload_cache_key(async_client, monkeypatch, path):
    await _import_synthetic_account(async_client)

    async def stream(payload, *args, **kwargs):
        assert payload.prompt_cache_key == "hello"
        yield 'data: {"type":"response.completed","response":{"id":"resp_session_observation","output":[]}}\n\n'

    async def compact(payload, *args, **kwargs):
        assert payload.prompt_cache_key == "hello"
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", compact)
    payload: dict[str, JsonValue] = {"model": "gpt-5.4", "instructions": "", "input": [], "prompt_cache_key": "hello"}
    if not path.endswith("compact"):
        payload["stream"] = True
    response = await async_client.post(
        path,
        json=payload,
        headers={"session_id": "abc"},
    )
    assert response.status_code == 200
    assert "response.failed" not in response.text
    sessions = await async_client.get("/api/sticky-sessions")
    assert sessions.status_code == 200
    assert [entry["key"] for entry in sessions.json()["entries"]] == [
        "\ncodex-lb-affinity-v1:session_header:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    ]
    logs = await async_client.get("/api/request-logs")
    row = logs.json()["requests"][0]
    assert row["stickyKeySource"] == "session_header"
    assert row["stickyKind"] == "codex_session"
    assert row["stickyKeyHash"] == "4850c055e8bd28c5"


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_affinity_metadata_follows_permission_instead_of_role(async_client, app_instance, allowed):
    await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "synthetic", "prompt_cache_key": "abc", "stream": True},
    )
    principal = guest_principal() if allowed else admin_principal(auth_mode=DashboardAuthMode.STANDARD)
    grants = dict(principal.grants)
    if allowed:
        grants[Permission.CONVERSATIONS_READ] = Scope.ALL
    else:
        grants.pop(Permission.CONVERSATIONS_READ)
    principal = replace(principal, grants=grants)
    app_instance.dependency_overrides[validate_dashboard_session] = lambda: principal
    try:
        response = await async_client.get("/api/request-logs")
    finally:
        app_instance.dependency_overrides.pop(validate_dashboard_session)
    assert response.status_code == 200
    row = response.json()["requests"][0]
    assert row["stickyKeySource"] == ("payload" if allowed else None)
    assert row["stickyKind"] == ("prompt_cache" if allowed else None)
    assert row["stickyKeyHash"] == ("ba7816bf8f01cfea" if allowed else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [PresetRoleSlug.ADMIN, PresetRoleSlug.OPERATOR])
async def test_stored_user_session_gates_affinity_metadata(async_client, role):
    await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.4", "input": "synthetic", "prompt_cache_key": "abc", "stream": True},
    )
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "synthetic-admin-password"})
    assert setup.status_code == 200
    async with SessionLocal() as session:
        session.add(
            DashboardUser(
                id="affinity-observer",
                username="observer",
                role_id=PRESET_ROLE_IDS[role],
                status="active",
                password_hash=bcrypt.hashpw(b"synthetic-observer-password", bcrypt.gensalt(4)).decode(),
            )
        )
        await session.commit()
    await get_dashboard_users_cache().invalidate()
    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200
    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": "observer", "password": "synthetic-observer-password"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"]["slug"] == role.value
    response = await async_client.get("/api/request-logs")
    assert response.status_code == 200
    row = response.json()["requests"][0]
    allowed = role == PresetRoleSlug.ADMIN
    assert row["stickyKeySource"] == ("payload" if allowed else None)
    assert row["stickyKind"] == ("prompt_cache" if allowed else None)
    assert row["stickyKeyHash"] == ("ba7816bf8f01cfea" if allowed else None)

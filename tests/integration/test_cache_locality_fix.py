from __future__ import annotations

import base64
import json
from datetime import timezone

import pytest

import app.modules.proxy.provider_adapters as proxy_transport_module
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository

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


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return response.json()["accountId"]


def test_mini_and_large_requests_use_different_cache_keys():
    """Verify that gpt-5.4-mini and gpt-5.3-codex requests produce different prompt_cache_keys
    due to model-class prefix separation."""
    from app.core.openai.requests import ResponsesRequest
    from app.modules.proxy.service import _derive_prompt_cache_key

    mini_payload = ResponsesRequest(
        model="gpt-5.4-mini",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    large_payload = ResponsesRequest(
        model="gpt-5.3-codex",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    mini_key = _derive_prompt_cache_key(mini_payload, None)
    large_key = _derive_prompt_cache_key(large_payload, None)

    assert mini_key != large_key, "Mini and large models should produce different cache keys"
    assert mini_key.startswith("mini-"), f"Mini key should start with 'mini-', got {mini_key}"
    assert large_key.startswith("codex-"), f"Large key should start with 'codex-', got {large_key}"


def test_same_model_class_produces_same_cache_key():
    """Verify that two requests with the same model class produce the same cache key."""
    from app.core.openai.requests import ResponsesRequest
    from app.modules.proxy.service import _derive_prompt_cache_key

    payload = ResponsesRequest(
        model="gpt-5.3-codex",
        instructions="You are helpful.",
        input=[{"role": "user", "content": "Hello"}],
    )

    key1 = _derive_prompt_cache_key(payload, None)
    key2 = _derive_prompt_cache_key(payload, None)

    assert key1 == key2, "Same model class should produce identical cache keys"


def test_prompt_cache_bridge_idle_ttl_from_settings():
    """Verify that PROMPT_CACHE bridge idle TTL is read from dashboard settings."""
    from app.db.models import StickySessionKind
    from app.modules.proxy.service import _AffinityPolicy, _effective_http_bridge_idle_ttl_seconds

    affinity = _AffinityPolicy(
        key="cache-key-123",
        kind=StickySessionKind.PROMPT_CACHE,
    )

    result = _effective_http_bridge_idle_ttl_seconds(
        affinity=affinity,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        prompt_cache_idle_ttl_seconds=3600.0,
    )

    assert result == 3600.0, "PROMPT_CACHE should use prompt_cache_idle_ttl_seconds"


def test_codex_session_idle_ttl_unchanged():
    """Verify that CODEX_SESSION idle TTL behavior is unchanged."""
    from app.db.models import StickySessionKind
    from app.modules.proxy.service import _AffinityPolicy, _effective_http_bridge_idle_ttl_seconds

    affinity = _AffinityPolicy(
        key="session-123",
        kind=StickySessionKind.CODEX_SESSION,
    )

    result = _effective_http_bridge_idle_ttl_seconds(
        affinity=affinity,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        prompt_cache_idle_ttl_seconds=3600.0,
    )

    assert result == 900.0, "CODEX_SESSION should use max(idle_ttl_seconds, codex_idle_ttl_seconds)"


def test_model_class_extraction_for_all_model_types():
    """Verify model class extraction works correctly for mini, codex, and standard models."""
    from app.modules.proxy.service import _extract_model_class

    assert _extract_model_class("gpt-5.4-mini") == "mini"
    assert _extract_model_class("gpt-4-mini") == "mini"

    assert _extract_model_class("gpt-5.3-codex") == "codex"
    assert _extract_model_class("gpt-5.3-codex-spark") == "codex"
    assert _extract_model_class("gpt-5.1-codex-mini") == "codex"

    # Standard models (default)
    assert _extract_model_class("gpt-5.4") == "std"
    assert _extract_model_class("gpt-4") == "std"
    assert _extract_model_class("gpt-4-turbo") == "std"


@pytest.mark.asyncio
async def test_prompt_cache_reallocates_when_usage_exceeds_configured_budget_threshold(async_client, monkeypatch):
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "stickyReallocationBudgetThresholdPct": 80.0,
            "preferEarlierResetAccounts": False,
            "routingStrategy": "usage_weighted",
        },
    )
    assert settings_response.status_code == 200

    acc_a_id = await _import_account(async_client, "acc_budget_a", "budget_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_budget_b", "budget_b@example.com")

    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_budget"}}\n\n'

    monkeypatch.setattr(proxy_transport_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
        "prompt_cache_key": "budget-threshold-key",
    }

    first = await async_client.post("/backend-api/codex/responses", json=payload)
    assert first.status_code == 200
    assert seen == ["acc_budget_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=85.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    second = await async_client.post("/backend-api/codex/responses", json=payload)
    assert second.status_code == 200
    assert seen == ["acc_budget_a", "acc_budget_b"]


def test_owner_mismatch_raises_409_for_retry() -> None:
    """Verify that the bridge mismatch block raises ProxyResponseError(409).

    When a request lands on a non-owner replica, the mismatch block must raise
    a 409 so the client retries instead of silently creating a duplicate local
    bridge that breaks turn-state continuity.
    """
    import inspect

    from app.modules.proxy import service as proxy_service_module

    source = inspect.getsource(proxy_service_module)
    assert "owner_mismatch_retry" in source, (
        "Expected 'owner_mismatch_retry' log event in service.py. "
        "The mismatch block should raise 409 for retry, not fall through."
    )

"""Native admission keeps rejection scope for later operator recovery."""

import time

import pytest

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from tests.integration.test_proxy_transient_retry import _import_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [None, "priority"])
async def test_native_rejection_persists_requested_scope(async_client, monkeypatch, tier):
    account_id = await _import_account(async_client, "scoped-rejection", "scope@example.invalid")

    async def reject(payload, headers, access_token, account_id, **kwargs):
        assert payload.model == "gpt-5.1"
        assert payload.service_tier == tier
        raise ProxyResponseError(
            429,
            openai_error("usage_limit_reached", "The requested model is exhausted"),
            failure_phase="status",
        )
        yield  # pragma: no cover

    monkeypatch.setattr(proxy_module, "core_stream_responses", reject)
    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)
    monkeypatch.setattr(proxy_api, "_STREAM_STARTUP_ERROR_PROBE_SECONDS", 30.0)
    payload = {"model": "gpt-5.1", "instructions": "Reply OK.", "input": [], "stream": True}
    if tier is not None:
        payload["service_tier"] = tier
    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "usage_limit_reached"

    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED
        assert account.blocked_at is not None and account.blocked_at <= time.time()
        assert account.block_generation > 0
        assert account.rejected_model == "gpt-5.1"
        assert account.rejected_service_tier == tier

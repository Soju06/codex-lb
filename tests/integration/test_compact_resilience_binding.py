from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from httpx import AsyncClient

import app.modules.proxy.service as proxy_module
from app.core.openai.models import CompactResponsePayload
from app.core.openai.requests import ResponsesCompactRequest
from app.core.resilience.toggles import current_resilience_toggles
from app.db.models import Account
from tests.integration.compact_test_helpers import _make_auth_json

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
async def test_compact_binds_each_dashboard_snapshot_after_owner_resolution(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    imported = await async_client.post(
        "/api/accounts/import",
        files={
            "auth_json": (
                "auth.json",
                json.dumps(_make_auth_json("acc_compact_resilience", "compact-resilience@example.com")),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200
    observed: list[bool] = []

    async def ensure_fresh(self: proxy_module.ProxyService, account: Account, **kwargs: object) -> Account:
        return account

    async def compact(
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        access_token: str,
        account_id: str | None,
        **kwargs: object,
    ) -> CompactResponsePayload:
        assert account_id == "acc_compact_resilience"
        observed.append(current_resilience_toggles().deterministic_failover_enabled)
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", compact)
    for enabled in (False, True):
        updated = await async_client.put("/api/settings", json={"deterministicFailoverEnabled": enabled})
        assert updated.status_code == 200
        response = await async_client.post(path, json={"model": "gpt-5.1", "instructions": "hi", "input": []})
        assert response.status_code == 200, response.text
        assert response.json()["object"] == "response.compaction"

    assert observed == [False, True]

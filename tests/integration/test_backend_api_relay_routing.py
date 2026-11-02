"""Route precedence for `/backend-api/*`: the relay never shadows a pool route.

These run through the ASGI app because the defect they guard against is in routing
(which handler answers), not in either handler's body.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.clients import backend_relay
from app.core.usage.models import UsagePayload

pytestmark = pytest.mark.integration


class _ForbiddenLease:
    """Fail loudly if anything reaches the relay's upstream transport."""

    async def __aenter__(self) -> Any:
        raise AssertionError("the backend relay reached the upstream backend")

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@pytest.fixture
def forbid_relay_transport(monkeypatch):
    monkeypatch.setattr(backend_relay, "lease_http_session", lambda: _ForbiddenLease())


@pytest.mark.asyncio
async def test_wham_usage_reads_keep_the_pool_handler(async_client, db_setup, forbid_relay_transport, monkeypatch):
    async def stub_fetch_usage(**_: object) -> UsagePayload:
        return UsagePayload.model_validate({"plan_type": "free"})

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)

    for path in ("/backend-api/wham/usage", "/backend-api/wham/usage/"):
        response = await async_client.get(
            path,
            headers={
                "Authorization": "Bearer personal-chatgpt-token",
                "chatgpt-account-id": "personal-free-account",
            },
        )

        assert response.status_code == 200, path
        # The empty pool answers `guest`; the caller's personal login says `free`.
        assert response.json()["plan_type"] == "guest", path


@pytest.mark.asyncio
async def test_codex_models_keeps_its_handler(async_client, db_setup, forbid_relay_transport):
    response = await async_client.get("/backend-api/codex/models")

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/backend-api/codex/models/"])
async def test_pool_owned_namespace_is_not_relayed(async_client, db_setup, forbid_relay_transport, path: str) -> None:
    response = await async_client.get(path)

    # The relay declines the namespace outright instead of answering a GET that
    # only it could serve; upstream stays untouched.
    assert response.status_code == 404

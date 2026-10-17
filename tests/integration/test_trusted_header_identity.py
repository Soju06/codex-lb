from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "headers",
    [
        [("Remote-User", "attacker@example.com"), ("Remote-User", "alice@example.com")],
        [("Remote-User", "alice@example.com"), ("Remote-User", "attacker@example.com")],
        [("Remote-User", "alice@example.com"), ("Remote-User", "alice@example.com")],
        [("remote-user", "alice@example.com"), ("Remote-User", "")],
    ],
    ids=["attacker-first", "attacker-last", "equal-values", "empty-and-mixed-case"],
)
@pytest.mark.asyncio
async def test_duplicate_trusted_identity_is_rejected(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    headers: list[tuple[str, str]],
) -> None:
    # Given trusted-header auth behind an allowlisted raw proxy peer
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    get_settings.cache_clear()

    # When the peer forwards ambiguous identity evidence
    response = await async_client.get("/api/settings", headers=headers)

    # Then neither value authenticates an admin principal
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "proxy_auth_required"

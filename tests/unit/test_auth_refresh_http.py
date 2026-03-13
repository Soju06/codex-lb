from __future__ import annotations

import pytest

from app.core.auth import refresh as refresh_module
from app.core.auth.refresh import (
    DEFAULT_REFRESH_TOKEN_URL,
    REFRESH_TOKEN_URL_OVERRIDE_ENV_VAR,
    refresh_access_token,
    refresh_token_endpoint,
)

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, *, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def json(self, content_type=None):
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.captured_url: str | None = None
        self.captured_json: dict[str, object] | None = None
        self.captured_proxy: str | None = None

    def post(self, url: str, *, json, headers, timeout, proxy=None):  # noqa: ANN001
        self.captured_url = url
        self.captured_json = json
        self.captured_proxy = proxy
        return self._response


class _FakeSettingsRow:
    http_proxy_url = "http://dashboard.proxy:3128"


class _FakeSettingsCache:
    async def get(self) -> _FakeSettingsRow:
        return _FakeSettingsRow()


def test_refresh_token_endpoint_uses_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_URL_OVERRIDE_ENV_VAR, raising=False)
    assert refresh_token_endpoint() == DEFAULT_REFRESH_TOKEN_URL


def test_refresh_token_endpoint_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    override_url = "https://example.test/custom/token"
    monkeypatch.setenv(REFRESH_TOKEN_URL_OVERRIDE_ENV_VAR, f"  {override_url}  ")
    assert refresh_token_endpoint() == override_url


@pytest.mark.asyncio
async def test_refresh_access_token_posts_refresh_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REFRESH_TOKEN_URL_OVERRIDE_ENV_VAR, raising=False)
    refresh_module.get_settings.cache_clear()
    fake = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "id_token": "new-id-token",
            },
        )
    )

    result = await refresh_access_token("old-refresh-token", session=fake)

    assert result.access_token == "new-access"
    assert result.refresh_token == "new-refresh"
    assert result.id_token == "new-id-token"
    assert fake.captured_url == DEFAULT_REFRESH_TOKEN_URL
    assert fake.captured_json is not None
    assert fake.captured_json["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert fake.captured_json["grant_type"] == "refresh_token"
    assert fake.captured_json["refresh_token"] == "old-refresh-token"
    assert fake.captured_json["scope"] == "openid profile email"
    assert fake.captured_proxy is None


@pytest.mark.asyncio
async def test_refresh_access_token_uses_env_http_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_HTTP_PROXY_URL", "http://env.proxy:8080")
    refresh_module.get_settings.cache_clear()
    fake = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "id_token": "new-id-token",
            },
        )
    )

    await refresh_access_token("old-refresh-token", session=fake)

    assert fake.captured_proxy == "http://env.proxy:8080"


@pytest.mark.asyncio
async def test_refresh_access_token_uses_dashboard_http_proxy_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_LB_HTTP_PROXY_URL", raising=False)
    refresh_module.get_settings.cache_clear()
    monkeypatch.setattr("app.core.clients.http.get_settings_cache", lambda: _FakeSettingsCache())
    fake = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "id_token": "new-id-token",
            },
        )
    )

    await refresh_access_token("old-refresh-token", session=fake)

    assert fake.captured_proxy == "http://dashboard.proxy:3128"

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.config.settings import get_settings
from app.core.middleware.dashboard_auth_proxy import add_dashboard_auth_proxy_middleware


class _StaticJwksClient:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, _assertion: str) -> SimpleNamespace:
        return SimpleNamespace(key=self.key)


class _FailingJwksClient:
    def get_signing_key_from_jwt(self, _assertion: str) -> SimpleNamespace:
        raise RuntimeError("JWKS unavailable")


def _configure_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", "trusted_header")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_ISSUER", "https://onda.cloudflareaccess.com")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_AUDIENCES", "onda-dashboard-aud")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_ALLOWED_EMAIL_DOMAINS", "onda.lol")
    get_settings.cache_clear()


def _token(private_key: RSAPrivateKey, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://onda.cloudflareaccess.com",
        "aud": "onda-dashboard-aud",
        "email": "Person@ONDA.LOL",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


async def _request_actor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    assertion: str | None,
    validation_key: object,
    jwks_failure: bool = False,
) -> str | None:
    _configure_access(monkeypatch)
    jwks_client = _FailingJwksClient() if jwks_failure else _StaticJwksClient(validation_key)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: jwks_client)
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.get("/actor")
    async def actor(request: Request) -> dict[str, str | None]:
        return {"actor": request.headers.get("Remote-User")}

    headers = {"Remote-User": "forged@onda.lol"}
    if assertion is not None:
        headers["Cf-Access-Jwt-Assertion"] = assertion
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/actor", headers=headers)
    get_settings.cache_clear()
    assert response.status_code == 200
    return response.json()["actor"]


@pytest.mark.asyncio
async def test_access_jwt_derives_actor_from_validated_email(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    actor = await _request_actor(
        monkeypatch,
        assertion=_token(private_key),
        validation_key=private_key.public_key(),
    )
    assert actor == "person@onda.lol"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"iss": "https://attacker.cloudflareaccess.com"},
        {"aud": "wrong-audience"},
        {"email": "person@example.com"},
    ],
)
async def test_access_jwt_rejects_invalid_claims(
    monkeypatch: pytest.MonkeyPatch,
    claim_overrides: dict[str, object],
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    actor = await _request_actor(
        monkeypatch,
        assertion=_token(private_key, **claim_overrides),
        validation_key=private_key.public_key(),
    )
    assert actor is None


@pytest.mark.asyncio
async def test_access_jwt_rejects_missing_forged_and_jwks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    assert await _request_actor(monkeypatch, assertion=None, validation_key=private_key.public_key()) is None
    assert (
        await _request_actor(
            monkeypatch,
            assertion=_token(other_key),
            validation_key=private_key.public_key(),
        )
        is None
    )
    assert (
        await _request_actor(
            monkeypatch,
            assertion=_token(private_key),
            validation_key=private_key.public_key(),
            jwks_failure=True,
        )
        is None
    )

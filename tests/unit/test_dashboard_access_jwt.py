from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.auth.dashboard_mode import get_dashboard_request_auth
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
    client_host: str = "127.0.0.1",
    required: bool = False,
) -> str | None:
    _configure_access(monkeypatch)
    if required:
        monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED", "true")
        get_settings.cache_clear()
    jwks_client = _FailingJwksClient() if jwks_failure else _StaticJwksClient(validation_key)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: jwks_client)
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.get("/actor")
    async def actor(request: Request) -> dict[str, str | None]:
        authentication = get_dashboard_request_auth(request)
        return {
            "actor": authentication.actor if authentication is not None else None,
            "forwarded_header": request.headers.get("Remote-User"),
        }

    headers = {"Remote-User": "forged@onda.lol"}
    if assertion is not None:
        headers["Cf-Access-Jwt-Assertion"] = assertion
    transport = ASGITransport(app=app, client=(client_host, 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/actor", headers=headers)
    get_settings.cache_clear()
    if required and response.status_code == 401:
        return None
    assert response.status_code == 200
    payload = response.json()
    assert payload["forwarded_header"] is None
    return payload["actor"]


async def _request_path_with_required_access(
    monkeypatch: pytest.MonkeyPatch,
    *,
    path: str,
    assertion: str | None = None,
) -> dict[str, object]:
    _configure_access(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED", "true")
    get_settings.cache_clear()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: _StaticJwksClient(private_key.public_key()))
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.get("/health/ready")
    @app.get("/internal/drain/status")
    async def probe(request: Request) -> dict[str, str | None]:
        authentication = get_dashboard_request_auth(request)
        return {
            "actor": authentication.actor if authentication is not None else None,
            "forwarded_header": request.headers.get("Remote-User"),
        }

    headers = {"Remote-User": "forged@onda.lol"}
    if assertion is not None:
        headers["Cf-Access-Jwt-Assertion"] = assertion
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers=headers)
    get_settings.cache_clear()
    return {"status": response.status_code, "payload": response.json()}


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
async def test_access_jwt_survives_forwarded_client_address_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    actor = await _request_actor(
        monkeypatch,
        assertion=_token(private_key),
        validation_key=private_key.public_key(),
        client_host="203.0.113.24",
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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health/ready", "/internal/drain/status"])
async def test_required_access_jwt_exempts_health_and_internal_probes(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    result = await _request_path_with_required_access(monkeypatch, path=path)

    assert result["status"] == 200
    assert result["payload"] == {"actor": None, "forwarded_header": None}


@pytest.mark.asyncio
async def test_required_access_jwt_blocks_missing_assertion_before_fallback_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    assert (
        await _request_actor(
            monkeypatch,
            assertion=None,
            validation_key=private_key.public_key(),
            required=True,
        )
        is None
    )
    assert (
        await _request_actor(
            monkeypatch,
            assertion=_token(private_key),
            validation_key=private_key.public_key(),
            jwks_failure=True,
            required=True,
        )
        is None
    )

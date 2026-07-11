from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Scope

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
    method: str = "GET",
    assertion: str | None = None,
) -> dict[str, object]:
    _configure_access(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED", "true")
    get_settings.cache_clear()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: _StaticJwksClient(private_key.public_key()))
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.api_route("/health/ready", methods=["GET", "POST"])
    @app.api_route("/api/accounts", methods=["GET"])
    @app.api_route("/api/fleet/summary", methods=["GET"])
    @app.api_route("/backend-api/codex/responses", methods=["POST"])
    @app.api_route("/internal/bridge/responses", methods=["POST"])
    @app.api_route("/internal/drain/start", methods=["POST"])
    @app.api_route("/internal/drain/status", methods=["GET"])
    @app.api_route("/internal/drain/stop", methods=["POST"])
    @app.api_route("/v1/responses", methods=["POST"])
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
        response = await client.request(method, path, headers=headers)
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
@pytest.mark.parametrize(("method", "path"), [("GET", "/health/ready"), ("GET", "/internal/drain/status")])
async def test_required_access_jwt_exempts_health_and_read_only_internal_probes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    result = await _request_path_with_required_access(monkeypatch, method=method, path=path)

    assert result["status"] == 200
    assert result["payload"] == {"actor": None, "forwarded_header": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/internal/drain/start"),
        ("POST", "/internal/drain/stop"),
    ],
)
async def test_required_access_jwt_blocks_mutating_or_non_probe_internal_paths(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    result = await _request_path_with_required_access(monkeypatch, method=method, path=path)

    assert result["status"] == 401
    assert result["payload"] == {"detail": "A valid Cloudflare Access assertion is required"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/fleet/summary"),
        ("POST", "/backend-api/codex/responses"),
        ("POST", "/internal/bridge/responses"),
        ("POST", "/v1/responses"),
    ],
)
async def test_required_access_jwt_preserves_api_key_protected_traffic_without_assertion(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    result = await _request_path_with_required_access(monkeypatch, method=method, path=path)

    assert result["status"] == 200
    assert result["payload"] == {"actor": None, "forwarded_header": None}


@pytest.mark.asyncio
async def test_required_access_jwt_blocks_dashboard_api_without_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _request_path_with_required_access(monkeypatch, path="/api/accounts")

    assert result["status"] == 401
    assert result["payload"] == {"detail": "A valid Cloudflare Access assertion is required"}


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


@pytest.mark.asyncio
async def test_required_access_jwt_rejects_dashboard_websocket_handshake_without_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_access(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED", "true")
    get_settings.cache_clear()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: _StaticJwksClient(private_key.public_key()))
    app_called = False

    async def app(_scope: Scope, _receive, _send) -> None:
        nonlocal app_called
        app_called = True

    from app.core.middleware.dashboard_auth_proxy import DashboardAuthProxyHeaderSanitizerMiddleware

    middleware = DashboardAuthProxyHeaderSanitizerMiddleware(app)
    sent: list[Message] = []
    receive_messages: list[Message] = [{"type": "websocket.connect"}]

    async def receive() -> Message:
        return receive_messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/api/status/socket",
            "raw_path": b"/api/status/socket",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        },
        receive,
        send,
    )

    assert app_called is False
    assert sent[0] == {
        "type": "websocket.http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"application/json")],
    }
    assert sent[1]["type"] == "websocket.http.response.body"
    get_settings.cache_clear()
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


@pytest.mark.asyncio
async def test_required_access_jwt_allows_api_key_websocket_handshake_without_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_access(monkeypatch)
    monkeypatch.setenv("CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED", "true")
    get_settings.cache_clear()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *_args, **_kwargs: _StaticJwksClient(private_key.public_key()))
    seen_scope: Scope | None = None

    async def app(scope: Scope, _receive, _send) -> None:
        nonlocal seen_scope
        seen_scope = scope

    from app.core.middleware.dashboard_auth_proxy import DashboardAuthProxyHeaderSanitizerMiddleware

    middleware = DashboardAuthProxyHeaderSanitizerMiddleware(app)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/backend-api/codex/responses",
            "raw_path": b"/backend-api/codex/responses",
            "query_string": b"",
            "headers": [(b"remote-user", b"forged@onda.lol")],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        },
        receive,
        send,
    )

    assert seen_scope is not None
    assert seen_scope["headers"] == []
    assert sent == []
    get_settings.cache_clear()

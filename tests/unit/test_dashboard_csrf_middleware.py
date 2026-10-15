from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Scope

from app.core.middleware.dashboard_csrf import (
    CROSS_SITE_REQUEST_REJECTED_CODE,
    CROSS_SITE_REQUEST_REJECTED_MESSAGE,
    DashboardCsrfMiddleware,
    add_dashboard_csrf_middleware,
)

pytestmark = pytest.mark.unit

REJECTED_ENVELOPE = {
    "error": {
        "code": CROSS_SITE_REQUEST_REJECTED_CODE,
        "message": CROSS_SITE_REQUEST_REJECTED_MESSAGE,
    }
}


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.api_route("/api/{rest:path}", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
    async def api_echo(rest: str) -> dict[str, str]:
        return {"path": rest}

    @app.post("/v1/{rest:path}")
    async def data_plane_echo(rest: str) -> dict[str, str]:
        return {"path": rest}

    @app.post("/internal/{rest:path}")
    async def internal_echo(rest: str) -> dict[str, str]:
        return {"path": rest}

    add_dashboard_csrf_middleware(app)
    return app


def _client(base_url: str = "http://dashboard.example") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_build_app()), base_url=base_url)


def _assert_rejected(response) -> None:
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == REJECTED_ENVELOPE


async def test_cross_site_fetch_metadata_is_rejected_with_dashboard_envelope() -> None:
    async with _client() as client:
        response = await client.post("/api/settings", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    _assert_rejected(response)


@pytest.mark.parametrize("site", ["same-origin", "none", " Same-Origin ", "NONE"])
async def test_same_origin_and_none_fetch_sites_pass(site: str) -> None:
    async with _client() as client:
        response = await client.post("/api/settings", json={}, headers={"Sec-Fetch-Site": site})
    assert response.status_code == 200
    assert response.json() == {"path": "settings"}


@pytest.mark.parametrize("site", ["same-site", "cross-site", ""])
async def test_other_fetch_sites_are_rejected(site: str) -> None:
    async with _client() as client:
        response = await client.post("/api/settings", json={}, headers={"Sec-Fetch-Site": site})
    _assert_rejected(response)


async def test_fetch_metadata_wins_over_matching_origin() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/settings",
            json={},
            headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://dashboard.example"},
        )
    _assert_rejected(response)


async def test_origin_mismatch_is_rejected() -> None:
    async with _client() as client:
        response = await client.post("/api/settings", json={}, headers={"Origin": "http://evil.example"})
    _assert_rejected(response)


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("http://dashboard.example", "http://dashboard.example"),
        ("http://dashboard.example", "http://DASHBOARD.example"),
        ("http://dashboard.example", "http://dashboard.example:80"),
        ("http://dashboard.example:8080", "http://dashboard.example:8080"),
        ("https://dashboard.example", "https://dashboard.example:443"),
        ("https://dashboard.example:443", "https://Dashboard.example"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("https://[2001:db8::1]:8443", "https://[2001:DB8::1]:8443"),
        ("http://dashboard.example", "http://dashboard.example/some/path?q=1"),
    ],
)
async def test_matching_origin_passes(base_url: str, origin: str) -> None:
    async with _client(base_url) as client:
        response = await client.post("/api/settings", json={}, headers={"Origin": origin})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("http://dashboard.example", "https://dashboard.example"),
        ("http://dashboard.example", "http://dashboard.example:8080"),
        ("http://dashboard.example:8080", "http://dashboard.example"),
        ("https://dashboard.example", "https://dashboard.example.evil"),
        ("http://dashboard.example", "not a url"),
        ("http://dashboard.example", "ftp://dashboard.example"),
    ],
)
async def test_origin_scheme_host_or_port_mismatch_is_rejected(base_url: str, origin: str) -> None:
    async with _client(base_url) as client:
        response = await client.post("/api/settings", json={}, headers={"Origin": origin})
    _assert_rejected(response)


async def test_null_origin_is_rejected() -> None:
    async with _client() as client:
        response = await client.post("/api/settings", json={}, headers={"Origin": "null"})
    _assert_rejected(response)


async def test_request_without_browser_headers_passes() -> None:
    async with _client() as client:
        response = await client.put("/api/settings", json={})
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_ignore_cross_site_headers(method: str) -> None:
    async with _client() as client:
        response = await client.request(
            method,
            "/api/settings",
            headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"},
        )
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_every_unsafe_method_is_protected(method: str) -> None:
    async with _client() as client:
        response = await client.request(method, "/api/settings", headers={"Sec-Fetch-Site": "cross-site"})
    _assert_rejected(response)


@pytest.mark.parametrize("path", ["/v1/responses", "/internal/drain/start"])
async def test_non_dashboard_paths_are_not_inspected(path: str) -> None:
    async with _client() as client:
        response = await client.post(path, json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/fleet/refresh", "/api/codex/rate-limit-reset-credits/consume"])
async def test_bearer_authenticated_prefixes_are_exempt(path: str) -> None:
    async with _client() as client:
        response = await client.post(path, json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


@pytest.mark.parametrize("authorization", ["Bearer x", "bearer x", "BEARER token-value"])
async def test_bearer_authorization_is_exempt(authorization: str) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/settings",
            json={},
            headers={"Sec-Fetch-Site": "cross-site", "Authorization": authorization},
        )
    assert response.status_code == 200


@pytest.mark.parametrize("authorization", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer "])
async def test_non_bearer_authorization_does_not_exempt(authorization: str) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/settings",
            json={},
            headers={"Sec-Fetch-Site": "cross-site", "Authorization": authorization},
        )
    _assert_rejected(response)


async def test_header_names_are_matched_case_insensitively() -> None:
    """Hand-built scopes may carry mixed-case header names; the check must still fire."""

    downstream_called = False

    async def downstream(scope: Scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/settings",
        "raw_path": b"/api/settings",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"Host", b"dashboard.example"), (b"SEC-FETCH-SITE", b"cross-site")],
        "client": ("203.0.113.9", 1234),
        "server": ("dashboard.example", 80),
    }
    await DashboardCsrfMiddleware(downstream)(scope, receive, send)

    assert downstream_called is False
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 403


async def test_origin_without_host_header_is_rejected() -> None:
    """h11 rejects Host-less HTTP/1.1 before ASGI, so this only occurs with hand-built scopes; fail closed anyway."""

    downstream_called = False

    async def downstream(scope: Scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/settings",
        "raw_path": b"/api/settings",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"origin", b"http://dashboard.example")],
        "client": ("203.0.113.9", 1234),
        "server": ("dashboard.example", 80),
    }
    await DashboardCsrfMiddleware(downstream)(scope, receive, send)

    assert downstream_called is False
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 403


async def test_websocket_scope_is_untouched() -> None:
    forwarded: list[Scope] = []

    async def downstream(scope: Scope, receive, send) -> None:
        forwarded.append(scope)

    async def noop_send(message: Message) -> None:
        del message

    async def noop_receive() -> Message:
        return {"type": "websocket.connect"}

    scope: Scope = {
        "type": "websocket",
        "path": "/api/events",
        "headers": [(b"origin", b"http://evil.example"), (b"sec-fetch-site", b"cross-site")],
    }
    await DashboardCsrfMiddleware(downstream)(scope, noop_receive, noop_send)

    assert forwarded == [scope]


async def test_lifespan_scope_is_untouched() -> None:
    forwarded: list[Scope] = []

    async def downstream(scope: Scope, receive, send) -> None:
        forwarded.append(scope)

    async def noop_send(message: Message) -> None:
        del message

    async def noop_receive() -> Message:
        return {"type": "lifespan.startup"}

    scope: Scope = {"type": "lifespan"}
    await DashboardCsrfMiddleware(downstream)(scope, noop_receive, noop_send)

    assert forwarded == [scope]


async def test_rejection_logs_method_path_and_reason_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.core.middleware.dashboard_csrf"):
        async with _client() as client:
            await client.post(
                "/api/dashboard-auth/logout",
                headers={"Origin": "http://evil.example/secret-path?token=abc", "Cookie": "session=secret"},
            )
    records = [record.getMessage() for record in caplog.records]
    assert len(records) == 1
    assert "method=POST" in records[0]
    assert "path=/api/dashboard-auth/logout" in records[0]
    assert "origin=http://evil.example:80 expected=http://dashboard.example:80" in records[0]
    assert "secret" not in records[0]
    assert "token" not in records[0]


async def test_rejection_log_marks_unparsable_origin_and_null(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.core.middleware.dashboard_csrf"):
        async with _client("https://dashboard.example:8443") as client:
            await client.post("/api/settings", headers={"Origin": "not a url"})
            await client.post("/api/settings", headers={"Origin": "null"})
    records = [record.getMessage() for record in caplog.records]
    assert len(records) == 2
    assert "origin=- expected=https://dashboard.example:8443" in records[0]
    assert "origin=null expected=https://dashboard.example:8443" in records[1]

"""Codex ChatGPT-backend passthrough over real HTTP.

The app runs under uvicorn on loopback and a real aiohttp server stands in for
upstream ChatGPT (or for the account's HTTP forward proxy), so streaming,
disconnect, and egress behavior are observed on the wire rather than through
httpx's buffering ASGI transport.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
from aiohttp import web
from yarl import URL

import app.core.clients.codex as codex_module
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus, ProxyEndpoint, ProxyPool, ProxyPoolMember
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

_POOL_TOKEN = "pool-stored-access-token"
_CALLER_TOKEN = "caller-own-chatgpt-token"
_REJECTED_TOKEN = "rejected-by-upstream"
# Upstream refuses this token even on the usage-identity check.
_FORGED_TOKEN = "not-this-accounts-token"

# Non-MCP backend paths Codex 0.157.0 sent with chatgpt_base_url = ".../backend-api"
# (captured 2026-09-27; see the change's context.md).
_CODEX_0157_BACKEND_PATHS = (
    "wham/accounts/check",
    "wham/settings/user",
    "ps/plugins/installed?includeExtensions=true&limit=200",
    "ps/plugins/list?scope=GLOBAL&limit=200",
    "ps/plugins/suggested/codex?scope=GLOBAL",
    "plugins/featured?platform=codex",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class _SeenRequest:
    method: str
    host: str
    path_qs: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _FakeChatGPT:
    """Real aiohttp server answering ``/backend-api/*`` like upstream ChatGPT."""

    seen: list[_SeenRequest] = field(default_factory=list)
    release_second_event: asyncio.Event = field(default_factory=asyncio.Event)
    hold_disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    port: int = 0
    _runner: web.AppRunner | None = None

    def forwarded(self) -> list[_SeenRequest]:
        """Requests other than codex-lb's own usage-identity checks."""

        return [seen for seen in self.seen if seen.path_qs != "/backend-api/wham/usage"]

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.seen.append(
            _SeenRequest(
                method=request.method,
                host=request.host,
                # Raw bytes, so encoding changes show; a proxied request's
                # absolute-form target is reduced to its path and query.
                path_qs=URL(request.raw_path, encoded=True).raw_path_qs,
                headers={key.lower(): value for key, value in request.headers.items()},
                body=await request.read(),
            )
        )
        path = request.rel_url.path
        if path == "/backend-api/wham/usage":
            if request.headers.get("Authorization") == f"Bearer {_FORGED_TOKEN}":
                return web.json_response({"detail": "token rejected"}, status=401)
            return web.json_response({"plan_type": "plus"})
        if path == "/backend-api/wham/redirect":
            return web.Response(status=302, headers={"Location": "/backend-api/wham/elsewhere"})
        if path == "/backend-api/wham/settings/user" and request.headers.get("Authorization") == (
            f"Bearer {_REJECTED_TOKEN}"
        ):
            return web.json_response({"detail": "token rejected"}, status=401)
        if path == "/backend-api/ps/mcp":
            response = web.StreamResponse(
                headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "s1", "Set-Cookie": "edge=1"}
            )
            await response.prepare(request)
            await response.write(b"event: message\ndata: first\n\n")
            await asyncio.wait_for(self.release_second_event.wait(), timeout=10)
            await response.write(b"event: message\ndata: second\n\n")
            await response.write_eof()
            return response
        if path == "/backend-api/wham/boom":
            return web.json_response({"detail": "upstream exploded"}, status=502)
        if path == "/backend-api/wham/broken":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: partial\n\n")
            assert request.transport is not None
            request.transport.abort()  # drop mid-body: the chunked stream never terminates
            return response
        if path == "/backend-api/wham/hold":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: held\n\n")
            try:
                await asyncio.sleep(3600)
            finally:
                self.hold_disconnected.set()
            return response
        return web.json_response(
            {"path": path},
            headers={"Set-Cookie": "edge=1", "X-Edge-Internal": "1", "Request-Id": "up-1"},
        )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        # Cancel handlers when codex-lb drops the connection, so ``/hold`` can observe it.
        self._runner = web.AppRunner(app, shutdown_timeout=0.1, handler_cancellation=True)
        await self._runner.setup()
        self.port = _free_port()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


@pytest.fixture
async def fake_chatgpt() -> AsyncIterator[_FakeChatGPT]:
    upstream = _FakeChatGPT()
    await upstream.start()
    try:
        yield upstream
    finally:
        await upstream.stop()


@pytest.fixture
async def live_base_url(app_instance, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """Serve the real app on loopback, lifespan included."""

    monkeypatch.setattr(codex_module, "discover_native_egress_client", lambda: None)
    port = _free_port()
    async with app_instance.router.lifespan_context(app_instance):
        server = uvicorn.Server(uvicorn.Config(app_instance, host="127.0.0.1", port=port, lifespan="off"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await task


@pytest.fixture
def direct_upstream(fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch) -> _FakeChatGPT:
    monkeypatch.setattr(get_settings(), "upstream_base_url", f"http://127.0.0.1:{fake_chatgpt.port}/backend-api")
    return fake_chatgpt


async def _seed_account(
    account_id: str,
    chatgpt_account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    proxy_port: int | None = None,
    empty_proxy_pool: bool = False,
) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                chatgpt_account_id=chatgpt_account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt(_POOL_TOKEN),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=status,
                deactivation_reason=None,
            )
        )
        if proxy_port is not None or empty_proxy_pool:
            pool = ProxyPool(id=f"pool-{account_id}", name=f"pool {account_id}")
            session.add(pool)
            if proxy_port is not None:
                endpoint = ProxyEndpoint(
                    id=f"ep-{account_id}", name="fake proxy", scheme="http", host="127.0.0.1", port=proxy_port
                )
                session.add(endpoint)
                session.add(ProxyPoolMember(pool_id=pool.id, endpoint_id=endpoint.id))
            session.add(AccountProxyBinding(account_id=account_id, pool_id=pool.id))
        await session.commit()


def _caller_headers(chatgpt_account_id: str, token: str = _CALLER_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": chatgpt_account_id,
        "User-Agent": "codex_cli_rs/0.157.0",
        "originator": "codex_cli_rs",
    }


# --- 3.1 routing -----------------------------------------------------------


@pytest.mark.asyncio
async def test_unserved_paths_reach_upstream_unchanged(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-route", "cgpt-route")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        check = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-route"))
        plugins = await client.get(
            "/backend-api/ps/plugins/list",
            params={"scope": "GLOBAL", "limit": "200"},
            headers=_caller_headers("cgpt-route"),
        )

    assert check.status_code == 200
    assert check.json() == {"path": "/backend-api/wham/accounts/check"}
    assert plugins.status_code == 200
    assert [seen.path_qs for seen in direct_upstream.forwarded()] == [
        "/backend-api/wham/accounts/check",
        "/backend-api/ps/plugins/list?scope=GLOBAL&limit=200",
    ]


@pytest.mark.asyncio
async def test_wham_usage_is_served_locally_and_matches_api_codex_usage(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-usage", "cgpt-usage")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        wham = await client.get("/backend-api/wham/usage", headers=_caller_headers("cgpt-usage"))
        api = await client.get("/api/codex/usage", headers=_caller_headers("cgpt-usage"))

    assert wham.status_code == 200
    assert wham.json() == api.json()
    # Only codex-lb's own identity/refresh fetches reached upstream /wham/usage;
    # the caller's request itself was answered locally, not relayed.
    assert direct_upstream.forwarded() == []


@pytest.mark.asyncio
async def test_wham_consume_is_served_by_the_reset_credit_handler(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-served", "cgpt-served")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.post(
            "/backend-api/wham/rate-limit-reset-credits/consume",
            json={"redeem_request_id": ""},
            headers=_caller_headers("cgpt-served"),
        )

    # The dedicated handler rejects the empty id itself; nothing is relayed.
    assert response.status_code == 400
    assert direct_upstream.forwarded() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/backend-api/codex/not-a-served-route",
        "/backend-api/files/file_abc",
        "/backend-api/transcribe/extra",
        "/backend-api/a/%2e%2e/%2e%2e/secret",
        "/backend-api/x%3Fy",
        "/backend-api/x%5Cy",
        "/backend-api/x%23y",
        "/backend-api/%2e%2e%2fsecret",
        "/backend-api/a%2f..%2fb",
        "/api/codex/accounts/check",
    ],
)
async def test_closed_and_escaping_paths_are_not_forwarded(
    live_base_url: str, direct_upstream: _FakeChatGPT, path: str
) -> None:
    await _seed_account("acc-closed", "cgpt-closed")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(path, headers=_caller_headers("cgpt-closed"))

    assert response.status_code == 404
    assert direct_upstream.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/", "/backend-api/does-not-exist"])
async def test_anonymous_unknown_paths_keep_the_root_not_found_envelope(
    live_base_url: str, direct_upstream: _FakeChatGPT, path: str
) -> None:
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": {"message": "Not Found", "type": "invalid_request_error", "code": "not_found"}}
    assert direct_upstream.seen == []


# --- 3.2 identity ----------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_credentials_are_forwarded_not_pool_credentials(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-ident", "cgpt-ident")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/settings/user", headers=_caller_headers("cgpt-ident"))

    assert response.status_code == 200
    (seen,) = direct_upstream.forwarded()
    assert seen.headers["authorization"] == f"Bearer {_CALLER_TOKEN}"
    assert seen.headers["chatgpt-account-id"] == "cgpt-ident"
    assert _POOL_TOKEN not in str(seen.headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "seed_status"),
    [
        ({"Authorization": f"Bearer {_CALLER_TOKEN}", "chatgpt-account-id": "cgpt-unknown"}, None),
        ({"Authorization": f"Bearer {_CALLER_TOKEN}", "chatgpt-account-id": "cgpt-paused"}, AccountStatus.PAUSED),
        ({"Authorization": f"Bearer {_CALLER_TOKEN}"}, None),
    ],
    ids=["unknown-account", "inactive-account", "missing-account-id"],
)
async def test_unauthenticated_callers_get_401_without_upstream_call(
    live_base_url: str,
    direct_upstream: _FakeChatGPT,
    headers: dict[str, str],
    seed_status: AccountStatus | None,
) -> None:
    if seed_status is not None:
        await _seed_account("acc-paused", "cgpt-paused", status=seed_status)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.post("/backend-api/ps/mcp", content=b"{}", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_uncredentialed_mcp_client_falls_through_like_an_unknown_path(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    # Codex's MCP client sends no credentials to a non-chatgpt.com host.
    mcp_headers = {"User-Agent": "codex-mcp-client/0.157.0", "originator": "codex_exec"}
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        mcp = await client.post("/backend-api/ps/mcp", content=b"{}", headers=mcp_headers)
        unknown = await client.post("/backend-api/codex/does-not-exist", content=b"{}", headers=mcp_headers)

    assert mcp.status_code == unknown.status_code == 405
    assert mcp.json() == unknown.json()
    assert direct_upstream.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer sk-clb-not-a-chatgpt-identity"},
        {"Authorization": "Bearer sk-clb-not-a-chatgpt-identity", "chatgpt-account-id": "cgpt-cap"},
        {
            "Authorization": f"Bearer {_CALLER_TOKEN}",
            "chatgpt-account-id": "cgpt-cap",
            "X-Codex-LB-Required-Capability": "anything",
        },
    ],
    ids=["api-key-principal", "api-key-with-account-header", "capability-carrier"],
)
async def test_proxy_api_key_principals_get_404_without_upstream_call(
    live_base_url: str, direct_upstream: _FakeChatGPT, headers: dict[str, str]
) -> None:
    await _seed_account("acc-cap", "cgpt-cap")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/settings/user", headers=headers)

    assert response.status_code == 404
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_upstream_token_rejection_is_relayed_and_account_stays_healthy(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-reject", "cgpt-reject")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(
            "/backend-api/wham/settings/user", headers=_caller_headers("cgpt-reject", token=_REJECTED_TOKEN)
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "token rejected"}
    async with SessionLocal() as session:
        account = await session.get(Account, "acc-reject")
    assert account is not None
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None


@pytest.mark.asyncio
async def test_token_not_accepted_for_account_is_not_forwarded(
    live_base_url: str, fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Knowing an active account id is not enough to send arbitrary calls out
    # through that account's proxy: only the fixed usage check may go out first.
    monkeypatch.setattr(get_settings(), "upstream_base_url", "http://upstream.invalid/backend-api")
    await _seed_account("acc-forged", "cgpt-forged", proxy_port=fake_chatgpt.port)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(
            "/backend-api/wham/settings/user", headers=_caller_headers("cgpt-forged", token=_FORGED_TOKEN)
        )

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert fake_chatgpt.forwarded() == []
    assert [seen.path_qs for seen in fake_chatgpt.seen] == ["/backend-api/wham/usage"]


@pytest.mark.asyncio
async def test_confirmed_binding_is_reused(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-bound", "cgpt-bound")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        for _ in range(2):
            response = await client.get("/backend-api/wham/settings/user", headers=_caller_headers("cgpt-bound"))
            assert response.status_code == 200

    identity_checks = [seen for seen in direct_upstream.seen if seen.path_qs == "/backend-api/wham/usage"]
    assert len(identity_checks) == 1
    assert len(direct_upstream.forwarded()) == 2


@pytest.mark.asyncio
async def test_cached_binding_is_refused_once_the_account_is_paused(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-pause", "cgpt-pause")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        first = await client.get("/backend-api/wham/settings/user", headers=_caller_headers("cgpt-pause"))
        async with SessionLocal() as session:
            account = await session.get(Account, "acc-pause")
            assert account is not None
            account.status = AccountStatus.PAUSED
            await session.commit()
        second = await client.get("/backend-api/wham/settings/user", headers=_caller_headers("cgpt-pause"))

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["type"] == "authentication_error"
    assert len(direct_upstream.forwarded()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("POST", "wham/usage", 405),
        # GET falls to the SPA fallback's not-found answer, as on the /api/codex twin.
        ("GET", "wham/rate-limit-reset-credits/consume", 404),
    ],
)
async def test_wrong_method_on_served_path_keeps_local_answer(
    live_base_url: str, direct_upstream: _FakeChatGPT, method: str, path: str, expected_status: int
) -> None:
    await _seed_account("acc-405", "cgpt-405")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.request(method, f"/backend-api/{path}", headers=_caller_headers("cgpt-405"))
        twin = await client.request(
            method, f"/api/codex/{path.removeprefix('wham/')}", headers=_caller_headers("cgpt-405")
        )

    assert response.status_code == twin.status_code == expected_status
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_query_string_encoding_is_preserved(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-query", "cgpt-query")
    raw_query = "q=a+b&x=%2Fy&z=%7e&s=a%20b&e=&k"
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(f"/backend-api/wham/echo?{raw_query}", headers=_caller_headers("cgpt-query"))

    assert response.status_code == 200
    assert [seen.path_qs for seen in direct_upstream.forwarded()] == [f"/backend-api/wham/echo?{raw_query}"]


@pytest.mark.asyncio
async def test_percent_encoded_path_is_forwarded_byte_for_byte(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-encpath", "cgpt-encpath")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/settings%2Fdetail", headers=_caller_headers("cgpt-encpath"))

    assert response.status_code == 200
    assert [seen.path_qs for seen in direct_upstream.forwarded()] == ["/backend-api/settings%2Fdetail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_prefix", ["/a%20b", ""], ids=["prefix-in-raw-path", "prefix-not-in-raw-path"])
async def test_encoded_mount_prefix_is_stripped_before_forwarding(
    app_instance, live_base_url: str, direct_upstream: _FakeChatGPT, request_prefix: str
) -> None:
    # The lifespan is already running under ``live_base_url``; drive the same app
    # in-process so the ASGI scope can carry a decoded ``root_path`` of "/a b".
    await _seed_account("acc-mount", "cgpt-mount")
    transport = httpx.ASGITransport(app=app_instance, root_path="/a b")
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            f"{request_prefix}/backend-api/settings%2Fdetail", headers=_caller_headers("cgpt-mount")
        )

    assert response.status_code == 200
    assert [seen.path_qs for seen in direct_upstream.forwarded()] == ["/backend-api/settings%2Fdetail"]


@pytest.mark.asyncio
async def test_get_body_is_relayed(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-getbody", "cgpt-getbody")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.request(
            "GET", "/backend-api/wham/echo", content=b'{"probe": 1}', headers=_caller_headers("cgpt-getbody")
        )

    assert response.status_code == 200
    (seen,) = direct_upstream.forwarded()
    assert seen.body == b'{"probe": 1}'


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxied"])
async def test_redirects_are_relayed_not_followed(
    live_base_url: str, fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch, egress: str
) -> None:
    if egress == "direct":
        monkeypatch.setattr(get_settings(), "upstream_base_url", f"http://127.0.0.1:{fake_chatgpt.port}/backend-api")
        await _seed_account("acc-redirect", "cgpt-redirect")
    else:
        monkeypatch.setattr(get_settings(), "upstream_base_url", "http://upstream.invalid/backend-api")
        await _seed_account("acc-redirect", "cgpt-redirect", proxy_port=fake_chatgpt.port)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/redirect", headers=_caller_headers("cgpt-redirect"))

    assert response.status_code == 302
    assert response.headers["location"] == "/backend-api/wham/elsewhere"
    assert [seen.path_qs for seen in fake_chatgpt.forwarded()] == ["/backend-api/wham/redirect"]


# --- 3.3 streaming and header hygiene --------------------------------------


@pytest.mark.asyncio
async def test_event_stream_is_streamed_before_upstream_finishes(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-mcp", "cgpt-mcp")
    headers = {**_caller_headers("cgpt-mcp"), "Mcp-Session-Id": "s1", "Cookie": "dashboard_session=secret"}
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        async with client.stream("POST", "/backend-api/ps/mcp", content=b'{"jsonrpc":"2.0"}', headers=headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["mcp-session-id"] == "s1"
            assert "set-cookie" not in resp.headers
            chunks = resp.aiter_bytes()
            first = await asyncio.wait_for(chunks.__anext__(), timeout=5)
            # Upstream is still blocked on the second event here.
            assert b"data: first" in first
            assert not direct_upstream.release_second_event.is_set()
            direct_upstream.release_second_event.set()
            rest = b"".join([chunk async for chunk in chunks])

    assert b"data: second" in rest
    (seen,) = direct_upstream.forwarded()
    assert seen.body == b'{"jsonrpc":"2.0"}'
    assert seen.headers["mcp-session-id"] == "s1"
    assert "cookie" not in seen.headers


@pytest.mark.asyncio
async def test_response_headers_are_allowlisted(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-hdr", "cgpt-hdr")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-hdr"))

    assert response.headers["request-id"] == "up-1"
    assert "set-cookie" not in response.headers
    assert "x-edge-internal" not in response.headers
    assert direct_upstream.seen


@pytest.mark.asyncio
async def test_client_disconnect_closes_upstream_stream(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-hold", "cgpt-hold")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        async with client.stream("GET", "/backend-api/wham/hold", headers=_caller_headers("cgpt-hold")) as resp:
            first = await asyncio.wait_for(resp.aiter_bytes().__anext__(), timeout=5)
            assert b"held" in first

    await asyncio.wait_for(direct_upstream.hold_disconnected.wait(), timeout=10)


# --- 3.4 egress ------------------------------------------------------------


@pytest.mark.asyncio
async def test_bound_account_forwards_through_its_proxy(
    live_base_url: str, fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake server plays the account's HTTP forward proxy; the upstream host
    # itself is unresolvable, so the request can only arrive via the proxy.
    monkeypatch.setattr(get_settings(), "upstream_base_url", "http://upstream.invalid/backend-api")
    await _seed_account("acc-proxied", "cgpt-proxied", proxy_port=fake_chatgpt.port)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-proxied"))

    assert response.status_code == 200
    (seen,) = fake_chatgpt.forwarded()
    assert seen.host == "upstream.invalid"
    assert seen.path_qs == "/backend-api/wham/accounts/check"


@pytest.mark.asyncio
async def test_unresolvable_route_fails_closed(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-noroute", "cgpt-noroute", empty_proxy_pool=True)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-noroute"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_error"
    assert direct_upstream.seen == []


# --- 3.5 path drift --------------------------------------------------------


@pytest.mark.asyncio
async def test_codex_0157_backend_paths_are_forwarded_unchanged(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-drift", "cgpt-drift")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        for path in _CODEX_0157_BACKEND_PATHS:
            response = await client.get(f"/backend-api/{path}", headers=_caller_headers("cgpt-drift"))
            assert response.status_code == 200, path

    assert [seen.path_qs for seen in direct_upstream.forwarded()] == [
        f"/backend-api/{path}" for path in _CODEX_0157_BACKEND_PATHS
    ]


# --- log seams ---------------------------------------------------------------

_PASSTHROUGH_LOGGER = "app.modules.proxy.codex_backend_passthrough"


@pytest.fixture
def passthrough_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=_PASSTHROUGH_LOGGER)
    caplog.set_level(logging.DEBUG, logger="app.core.auth.dependencies")
    return caplog


def _lines(caplog: pytest.LogCaptureFixture, fragment: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if fragment in record.getMessage()]


async def _wait_for_line(caplog: pytest.LogCaptureFixture, fragment: str) -> logging.LogRecord:
    for _ in range(100):
        found = _lines(caplog, fragment)
        if found:
            return found[-1]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no log line containing {fragment!r}")


@pytest.mark.asyncio
async def test_completed_call_logs_outcome_duration_and_bytes(
    live_base_url: str, direct_upstream: _FakeChatGPT, passthrough_logs: pytest.LogCaptureFixture
) -> None:
    await _seed_account("acc-log-ok", "cgpt-log-ok")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-log-ok"))

    record = await _wait_for_line(passthrough_logs, "path=wham/accounts/check account_id=acc-log-ok status=200")
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert "outcome=completed" in message
    assert f"bytes={len(response.content)}" in message
    assert "duration_ms=" in message
    identity = await _wait_for_line(passthrough_logs, "identity account_id=acc-log-ok")
    assert identity.levelno == logging.DEBUG
    assert "egress=direct" in identity.getMessage()
    assert direct_upstream.seen


@pytest.mark.asyncio
async def test_upstream_5xx_logs_at_warning(
    live_base_url: str, direct_upstream: _FakeChatGPT, passthrough_logs: pytest.LogCaptureFixture
) -> None:
    await _seed_account("acc-log-5xx", "cgpt-log-5xx")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/boom", headers=_caller_headers("cgpt-log-5xx"))

    assert response.status_code == 502
    record = await _wait_for_line(passthrough_logs, "path=wham/boom")
    assert record.levelno == logging.WARNING
    assert "status=502 outcome=completed" in record.getMessage()
    assert direct_upstream.seen


@pytest.mark.asyncio
async def test_mid_stream_upstream_failure_logs_error_outcome(
    live_base_url: str, direct_upstream: _FakeChatGPT, passthrough_logs: pytest.LogCaptureFixture
) -> None:
    await _seed_account("acc-log-broken", "cgpt-log-broken")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        with pytest.raises(httpx.HTTPError):
            async with client.stream(
                "GET", "/backend-api/wham/broken", headers=_caller_headers("cgpt-log-broken")
            ) as resp:
                async for _ in resp.aiter_bytes():
                    pass

    record = await _wait_for_line(passthrough_logs, "path=wham/broken")
    assert record.levelno == logging.WARNING
    assert "outcome=error" in record.getMessage()
    assert "error=ClientPayloadError" in record.getMessage()
    assert direct_upstream.seen


@pytest.mark.asyncio
async def test_client_disconnect_logs_client_disconnect_outcome(
    live_base_url: str, direct_upstream: _FakeChatGPT, passthrough_logs: pytest.LogCaptureFixture
) -> None:
    await _seed_account("acc-log-hold", "cgpt-log-hold")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        async with client.stream("GET", "/backend-api/wham/hold", headers=_caller_headers("cgpt-log-hold")) as resp:
            await asyncio.wait_for(resp.aiter_bytes().__anext__(), timeout=5)

    await asyncio.wait_for(direct_upstream.hold_disconnected.wait(), timeout=10)
    record = await _wait_for_line(passthrough_logs, "path=wham/hold")
    assert record.levelno == logging.INFO
    assert "outcome=client_disconnect" in record.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers", "reason"),
    [
        ("/backend-api/does-not-exist", {}, "no_bearer"),
        ("/backend-api/codex/not-served", {"Authorization": f"Bearer {_CALLER_TOKEN}"}, "closed_namespace"),
        ("/backend-api/wham/settings/user", {"Authorization": "Bearer sk-clb-key"}, "api_key_principal"),
        (
            "/backend-api/wham/settings/user",
            {"Authorization": f"Bearer {_CALLER_TOKEN}", "X-Codex-LB-Required-Capability": "x"},
            "capability_header",
        ),
        (
            "/backend-api/wham/rate-limit-reset-credits/consume",
            {"Authorization": f"Bearer {_CALLER_TOKEN}"},
            "served_locally",
        ),
    ],
)
async def test_declines_log_their_reason_at_debug(
    live_base_url: str,
    direct_upstream: _FakeChatGPT,
    passthrough_logs: pytest.LogCaptureFixture,
    path: str,
    headers: dict[str, str],
    reason: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        await client.get(path, headers=headers)

    record = await _wait_for_line(passthrough_logs, f"declined method=GET path={path} reason={reason}")
    assert record.levelno == logging.DEBUG
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_identity_logs_proxy_pool_egress(
    live_base_url: str,
    fake_chatgpt: _FakeChatGPT,
    monkeypatch: pytest.MonkeyPatch,
    passthrough_logs: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(get_settings(), "upstream_base_url", "http://upstream.invalid/backend-api")
    await _seed_account("acc-log-proxied", "cgpt-log-proxied", proxy_port=fake_chatgpt.port)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-log-proxied"))

    record = await _wait_for_line(passthrough_logs, "identity account_id=acc-log-proxied")
    assert "egress=account_bound:pool-acc-log-proxied" in record.getMessage()

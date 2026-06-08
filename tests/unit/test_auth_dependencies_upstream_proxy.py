from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.auth import dependencies as auth_dependencies
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def _account() -> Account:
    return Account(
        id="acc_1",
        chatgpt_account_id="chatgpt_1",
        email="acc@example.com",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        status=AccountStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_passes_resolved_route(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account()
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    calls: dict[str, Any] = {}

    class Repo:
        def __init__(self, session: object) -> None:
            calls["repo_session"] = session

        async def get_active_by_chatgpt_account_id(self, chatgpt_account_id: str) -> Account | None:
            calls["lookup"] = chatgpt_account_id
            return account

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        calls["resolve_kwargs"] = kwargs
        return route

    async def fetch_usage(*args: object, **kwargs: object) -> None:
        calls["fetch_kwargs"] = kwargs

    monkeypatch.setattr(auth_dependencies, "get_background_session", session_context)
    monkeypatch.setattr(auth_dependencies, "AccountsRepository", Repo)
    monkeypatch.setattr(auth_dependencies, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(auth_dependencies, "fetch_usage", fetch_usage)

    result = await auth_dependencies.validate_codex_usage_identity(
        cast(Any, SimpleNamespace(headers={"Authorization": "Bearer access", "chatgpt-account-id": "chatgpt_1"}))
    )

    assert result is None
    assert calls["lookup"] == "chatgpt_1"
    assert calls["resolve_kwargs"]["account_id"] == "acc_1"
    assert calls["resolve_kwargs"]["operation"] == "usage_identity"
    assert calls["fetch_kwargs"]["route"] is route


@pytest.mark.asyncio
async def test_validate_codex_usage_identity_fails_closed_when_route_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account()

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_active_by_chatgpt_account_id(self, chatgpt_account_id: str) -> Account | None:
            return account

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        raise UpstreamProxyRouteError("default_pool_unconfigured", account_id="acc_1")

    monkeypatch.setattr(auth_dependencies, "get_background_session", session_context)
    monkeypatch.setattr(auth_dependencies, "AccountsRepository", Repo)
    monkeypatch.setattr(auth_dependencies, "resolve_upstream_route", resolve_route)

    with pytest.raises(auth_dependencies.ProxyUpstreamError):
        await auth_dependencies.validate_codex_usage_identity(
            cast(Any, SimpleNamespace(headers={"Authorization": "Bearer access", "chatgpt-account-id": "chatgpt_1"}))
        )

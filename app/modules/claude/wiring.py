"""Wiring helpers for the Claude OAuth pool.

Keeps the heavy ``ClaudeProxyService`` construction in a single place so the
``app/main.py`` lifespan and the integration tests share one canonical
factory. Nothing here is exposed via the public router — it is purely the
dependency factory used at startup and by tests.
"""

from __future__ import annotations

from typing import Any

from app.core.clients.anthropic.chat import AiohttpClaudeChatTransport, build_claude_chat_client
from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.claude.auth_manager import ClaudeAuthManager
from app.modules.claude.repository import SqlClaudeAccountRepository
from app.modules.claude.service import ClaudeProxyService
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository


async def _proxy_repo_context_async():
    """Yield a populated :class:`ProxyRepositories` bundle backed by a real
    background session.

    Mirrors ``_proxy_repo_context`` in :mod:`app.dependencies` but is async
    so the lifespan code can ``await`` it without breaking the synchronous
    LoadBalancer contract.
    """
    async with get_background_session() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=None,  # type: ignore[arg-type]
            additional_usage=None,  # type: ignore[arg-type]
            quota_planner=None,  # type: ignore[arg-type]
        )


def build_claude_proxy_service() -> ClaudeProxyService:
    """Construct a fully-wired :class:`ClaudeProxyService`.

    Used by the lifespan and by integration tests. The returned service
    reuses the existing load balancer (so the Claude path inherits Codex's
    selection logic + cooldown bookkeeping) and the chat client is bound to
    an aiohttp-backed adapter that pulls from the shared HTTP pool.
    """
    settings = get_settings()
    chat = build_claude_chat_client(
        session=None,
        settings=settings,
        base_url=settings.claude_api_base_url,
    )

    # Replace the placeholder transport with a lazy aiohttp adapter. The
    # adapter forwards ``post`` to a small ``LazySession`` helper that
    # defers resolution of the shared aiohttp session until the request is
    # actually issued — tests that never call out to the network can still
    # exercise this code path.
    chat._transport = AiohttpClaudeChatTransport(  # type: ignore[attr-defined]
        _LazySession()
    )

    load_balancer = LoadBalancer(repo_factory=_proxy_repo_context_async)

    # The repositories below are NOT ``None``-safe — Phase 9 only calls the
    # two methods (``update_rate_limit_cache``, ``update_last_used_at`` and
    # ``add_log``). Each request opens its own AsyncSession via the
    # standard helper. We construct lightweight stand-ins here only to
    # satisfy the constructor signature; per-request logic opens real
    # sessions.
    accounts_repository = _LazyAccountsRepository()
    request_log_repository = _LazyRequestLogsRepository()
    auth_manager = ClaudeAuthManager(
        repo=SqlClaudeAccountRepository.__new__(SqlClaudeAccountRepository),
    )

    return ClaudeProxyService(
        load_balancer=load_balancer,
        chat=chat,
        auth_manager=auth_manager,
        accounts_repository=accounts_repository,  # type: ignore[arg-type]
        request_log_repository=request_log_repository,  # type: ignore[arg-type]
    )


class _LazySession:
    """Adapter that forwards ``post`` calls to the shared HTTP client.

    The :class:`AiohttpClaudeChatTransport` wraps aiohttp's
    ``ClientSession``; the production lifespan owns one and tests can
    substitute a stub via ``app.state.claude_proxy_service``. By using a
    lazy proxy we avoid forcing lifespan-driven session creation in code
    paths that never need it (e.g. unit tests using ``MonkeyPatch``).
    """

    async def post(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        from app.core.clients.http import get_http_client

        return await get_http_client().session.post(*args, **kwargs)


class _LazyAccountsRepository:
    """Stub accounts repository used only to satisfy type signatures.

    The actual ``update_rate_limit_cache`` and ``update_last_used_at``
    semantics are implemented by :class:`AccountsRepository`; the proxy
    service opens a fresh session inside each call. This stub exists so
    the lifespan can build the service without pulling in a session early.
    """

    async def update_rate_limit_cache(self, *args: Any, **kwargs: Any) -> bool:  # type: ignore[no-untyped-def]
        from app.db.session import get_background_session
        from app.modules.accounts.repository import AccountsRepository

        async with get_background_session() as session:
            return await AccountsRepository(session).update_rate_limit_cache(*args, **kwargs)

    async def update_last_used_at(self, *args: Any, **kwargs: Any) -> bool:  # type: ignore[no-untyped-def]
        from app.db.session import get_background_session
        from app.modules.accounts.repository import AccountsRepository

        async with get_background_session() as session:
            return await AccountsRepository(session).update_last_used_at(*args, **kwargs)


class _LazyRequestLogsRepository:
    """Stub request-log repository used only to satisfy type signatures."""

    async def add_log(self, **kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
        from app.db.session import get_background_session
        from app.modules.request_logs.repository import RequestLogsRepository

        async with get_background_session() as session:
            return await RequestLogsRepository(session).add_log(**kwargs)


__all__ = ["build_claude_proxy_service"]

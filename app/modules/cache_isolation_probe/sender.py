"""One probe call: the routed upstream send, reported as numbers only.

This deliberately reuses the same primitives the limit warm-up sender uses --
``AuthManager.ensure_fresh`` for credentials, ``resolve_upstream_route`` for
proxy routing, ``stream_responses`` for the upstream client -- instead of
opening its own transport. The probe therefore inherits the account's
credentials, its proxy binding (including fail-closed behaviour) and the
configured stream timeouts without restating any of them.

Nothing here returns or stores response content. The only values that leave
this module are token counts, a latency, and a sanitized error code.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Callable, Protocol, cast

from app.core.auth.refresh import RefreshError
from app.core.clients.codex import CodexTransportError
from app.core.clients.proxy import UpstreamProxyRouteTrace, stream_responses
from app.core.crypto import TokenEncryptor
from app.core.openai.models import ResponseUsage
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.cache_isolation_probe.prefix import PROBE_INSTRUCTIONS, PROBE_QUESTION

CACHE_PROBE_HEADER = "x-codex-lb-cache-isolation-probe"
CACHE_PROBE_USER_AGENT = "codex-lb-cache-isolation-probe"
#: The probe measures input caching; every output token is pure waste.
CACHE_PROBE_MAX_OUTPUT_TOKENS = 16

_TERMINAL_ERROR_EVENTS = frozenset({"response.failed", "response.incomplete", "error"})

AccountsRepoFactory = Callable[[], AbstractAsyncContextManager[AccountsRepository]]


class CacheProbeSenderPort(Protocol):
    """What the orchestrator needs from a sender, so a test can substitute one."""

    async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult: ...


@dataclass(frozen=True, slots=True)
class ProbeSendResult:
    """Numeric outcome of one upstream call. Never carries response content."""

    ok: bool
    latency_ms: int
    input_tokens: int | None = None
    cached_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _usage_tokens(usage: ResponseUsage | None) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    details = usage.input_tokens_details
    cached = details.cached_tokens if details is not None else None
    return usage.input_tokens, cached


class CacheProbeSender:
    """Sends one probe call on one named account."""

    def __init__(self, accounts_repo_factory: AccountsRepoFactory) -> None:
        self._accounts_repo_factory = accounts_repo_factory
        self._encryptor = TokenEncryptor()

    async def send(self, account_id: str, *, model: str, prefix: str) -> ProbeSendResult:
        started = time.monotonic()
        request_id = f"cache-probe-{uuid.uuid4().hex}"

        try:
            account = await self._refreshed_account(account_id)
        except RefreshError as exc:
            return ProbeSendResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error_code=f"auth_refresh_{exc.code}",
                error_message=exc.message,
            )
        if account is None or account.status != AccountStatus.ACTIVE:
            status = "missing" if account is None else account.status.value
            return ProbeSendResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error_code="account_not_active",
                error_message=f"Account is not active ({status})",
            )

        try:
            route = await self._resolve_route(account)
        except UpstreamProxyRouteError as exc:
            return ProbeSendResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error_code="upstream_proxy_unavailable",
                error_message=f"Upstream proxy route unavailable: {exc.reason}",
            )

        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        payload = ResponsesRequest.model_validate(
            {
                "model": model,
                "instructions": PROBE_INSTRUCTIONS,
                "input": f"{prefix}\n{PROBE_QUESTION}",
                "tools": [],
                "parallel_tool_calls": False,
                "stream": True,
                "store": False,
                # `max_output_tokens` is stripped by `_UNSUPPORTED_UPSTREAM_FIELDS`
                # before egress, so it cannot bound this call. It is kept because
                # it states the intent for any model source that does honour it,
                # and the real bound comes from the two fields below: the probe
                # only needs `usage.input_tokens_details.cached_tokens` off the
                # terminal frame, so every output token is waste. Without them a
                # 28k-token prompt runs an uncapped generation, up to ten per run.
                "max_output_tokens": CACHE_PROBE_MAX_OUTPUT_TOKENS,
                "reasoning": {"effort": "low"},
                "text": {"verbosity": "low"},
            }
        )
        headers = {
            "x-request-id": request_id,
            CACHE_PROBE_HEADER: "1",
            "user-agent": CACHE_PROBE_USER_AGENT,
        }

        usage: ResponseUsage | None = None
        try:
            # aclosing(), not a bare `async for`: returning mid-stream on
            # `response.completed` would otherwise leave the upstream response
            # and its session to async-generator finalization, overlapping the
            # next sequential call's connection.
            stream = stream_responses(
                payload,
                headers,
                access_token,
                account.chatgpt_account_id,
                upstream_stream_transport_override="http",
                route=route,
                route_trace=UpstreamProxyRouteTrace(),
                allow_direct_egress=route is None,
                codex_lb_account_id=account.id,
            )
            # ``stream_responses`` is an async generator function; its
            # annotation widens to ``AsyncIterator``, which does not
            # advertise ``aclose``.
            async with contextlib.aclosing(cast("AsyncGenerator[str, None]", stream)):
                async for event_block in stream:
                    event = parse_sse_event(event_block)
                    if event is None:
                        continue
                    if event.response is not None and event.response.usage is not None:
                        usage = event.response.usage
                    if event.type == "response.completed":
                        input_tokens, cached_tokens = _usage_tokens(usage)
                        return ProbeSendResult(
                            ok=True,
                            latency_ms=_elapsed_ms(started),
                            input_tokens=input_tokens,
                            cached_tokens=cached_tokens,
                        )
                    if event.type in _TERMINAL_ERROR_EVENTS:
                        error = event.error or (event.response.error if event.response is not None else None)
                        input_tokens, cached_tokens = _usage_tokens(usage)
                        return ProbeSendResult(
                            ok=False,
                            latency_ms=_elapsed_ms(started),
                            input_tokens=input_tokens,
                            cached_tokens=cached_tokens,
                            error_code=(error.code if error is not None else None) or event.type,
                            error_message=(error.message if error is not None else None) or event.type,
                        )
        except CodexTransportError as exc:
            # Routed-transport failures carry a classified code and a message
            # that is credential-safe by construction, so they become a typed
            # failed row rather than an exception the orchestrator has to
            # sanitize blindly.
            return ProbeSendResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error_code=exc.error_code or "upstream_transport_error",
                error_message=str(exc),
            )

        input_tokens, cached_tokens = _usage_tokens(usage)
        return ProbeSendResult(
            ok=False,
            latency_ms=_elapsed_ms(started),
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            error_code="stream_incomplete",
            error_message="Probe stream ended without a terminal event",
        )

    async def _refreshed_account(self, account_id: str) -> Account | None:
        async with self._accounts_repo_factory() as accounts_repo:
            current = await accounts_repo.get_by_id_fresh(account_id)
            if current is None or current.status != AccountStatus.ACTIVE:
                return current
            await AuthManager(accounts_repo, refresh_repo_factory=self._accounts_repo_factory).ensure_fresh(current)
        async with self._accounts_repo_factory() as accounts_repo:
            return await accounts_repo.get_by_id_fresh(account_id)

    async def _resolve_route(self, account: Account) -> ResolvedUpstreamRoute | None:
        async with self._accounts_repo_factory() as accounts_repo:
            return await resolve_upstream_route(
                accounts_repo.session,
                account_id=account.id,
                operation="cache_isolation_probe",
                scope="account",
                encryptor=self._encryptor,
            )

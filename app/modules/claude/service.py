"""Claude proxy service.

Bridges the load balancer, the Anthropic chat client, and the auth manager
to expose a single entry point that the API layer can call without reaching
into either of them directly. The Codex-flavored ``app.modules.proxy.service``
is intentionally NOT touched (per ADR-0001 / CLAUDE.md); Claude has its own
narrow surface so the two providers stay independent.

Source of truth: ``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— requirements *Pooled proxy passthrough*, *401 from Anthropic triggers
rotate-and-retry once*, *Per-account refresh serialization (singleflight)*,
*Claude rate-limit cooldown mirrors Codex cooldown*, and *Streaming
passthrough*.

The service is intentionally thin: account selection happens in the load
balancer, cooldown bookkeeping lives there too, and the chat client is a
pure HTTP wrapper. The proxy's only added responsibilities are:

- Provider-scope authorization on the API key.
- Single retry on 401 (with singleflight-protected rotation).
- Persisting rate-limit headers after every Anthropic response (200 and 429).
- Writing exactly one ``request_logs`` row per successful request — once
  per non-streaming call, once per completed stream.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Protocol

from app.core.clients.anthropic.chat import StreamChunk
from app.core.clients.anthropic.errors import ClaudeAPIError, ClaudeAuthError, ClaudeRateLimited
from app.core.clients.anthropic.headers import parse_anthropic_rate_limit_headers
from app.core.metrics.prometheus import codex_lb_claude_accounts_active, codex_lb_claude_requests_total
from app.core.utils.time import utcnow
from app.db.models import Account

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class ProviderScopeMismatch(Exception):
    """The API key's ``provider_scope`` does not include ``"claude"``.

    Surfaces as HTTP 403 in the API layer; raised before any account is
    selected so a misconfigured key cannot leak pool membership.
    """


class NoClaudeAccounts(Exception):
    """No Claude accounts are available in the pool.

    Surfaces as HTTP 503 in the API layer; the load balancer returned no
    candidate (no accounts, all rate-limited, or all filtered out for the
    requested model).
    """


# ---------------------------------------------------------------------------
# Port definitions — keep collaborator surface small and easy to stub.
# ---------------------------------------------------------------------------


class _LoadBalancerLike(Protocol):
    async def select_account(
        self,
        *,
        provider: str,
        sticky_key: str | None = None,
        traffic_class: Any = None,
    ) -> Any: ...

    async def record_claude_rate_limit_response(
        self,
        *,
        account: Account,
        headers: Mapping[str, str],
        is_rate_limited_response: bool = True,
    ) -> None: ...

    async def record_error(self, account: Account) -> None: ...


class _ChatLike(Protocol):
    async def send_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]: ...

    async def stream_messages(
        self,
        *,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> AsyncIterator[StreamChunk]: ...


class _AuthLike(Protocol):
    async def get_access_token(self, account: Account) -> str: ...

    async def rotate_claude_access_token(self, account: Account) -> Any: ...


class _AccountsRepoLike(Protocol):
    async def update_rate_limit_cache(self, account_id: str, fields: dict[str, object]) -> bool: ...

    async def update_last_used_at(self, account_id: str, *, at: datetime) -> bool: ...


class _RequestLogRepoLike(Protocol):
    async def add_log(self, **kwargs: Any) -> Any: ...


class _ApiKeyLike(Protocol):
    provider_scope: str | None
    id: str | None


# ---------------------------------------------------------------------------
# ClaudeProxyService
# ---------------------------------------------------------------------------


class ClaudeProxyService:
    """Stateless proxy service for the Claude OAuth pool.

    Each request runs through:

    1. Provider-scope check on the API key (raises :class:`ProviderScopeMismatch`).
    2. ``load_balancer.select_account(provider="claude")`` to choose an account.
    3. ``auth_manager.get_access_token`` to resolve the bearer token.
    4. ``chat.send_messages`` (or ``stream_messages``) — the upstream call.
    5. Persist rate-limit cache + write ``request_logs`` row.

    A 401 from Anthropic triggers at most one rotate-and-retry. Two
    consecutive 401s mark the account unhealthy and propagate.
    """

    def __init__(
        self,
        *,
        load_balancer: _LoadBalancerLike,
        chat: _ChatLike,
        auth_manager: _AuthLike,
        accounts_repository: _AccountsRepoLike,
        request_log_repository: _RequestLogRepoLike,
        metrics: Any | None = None,
    ) -> None:
        self._lb = load_balancer
        self._chat = chat
        self._auth = auth_manager
        self._repo = accounts_repository
        self._logs = request_log_repository
        self._metrics = metrics

    # ------------------------------------------------------- non-streaming

    async def stream_or_complete_messages(
        self,
        *,
        request_body: Mapping[str, Any],
        api_key: _ApiKeyLike,
        request_id: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Forward a non-streaming POST ``/v1/messages`` to Anthropic.

        Returns ``(response_body, response_headers)`` for the API layer to
        serialize. Raises:

        - :class:`ProviderScopeMismatch` if the API key is not authorized
          for ``provider="claude"``.
        - :class:`NoClaudeAccounts` if the load balancer returns no candidate.
        - :class:`ClaudeAuthError` after two consecutive 401s.
        - :class:`ClaudeRateLimited` after the cooldown is recorded.
        - Any other :class:`ClaudeAPIError` from the chat client.
        """
        self._authorize_provider_scope(api_key)
        account = await self._select_account()
        access_token = await self._auth.get_access_token(account)
        # Passthrough: the chat client signature is ``Mapping[str, Any]`` and
        # we deliberately do NOT shallow-copy — the body bytes are forwarded
        # to Anthropic and back to the client with no transformation. The
        # identity-preservation contract is asserted in
        # ``tests/unit/test_claude_proxy_service.py::test_request_body_passed_verbatim_no_copy``.
        body_in = request_body

        try:
            body, headers = await self._send_with_retry(
                account=account,
                access_token=access_token,
                request_body=body_in,
            )
        except ClaudeRateLimited as exc:
            # The chat client attaches the upstream headers to the exception
            # so we can still record cooldown + persist cache before re-raising.
            headers = getattr(exc, "headers", {}) or {}
            await self._on_rate_limited(account=account, headers=headers)
            self._record_request_metric("rate_limited")
            raise
        except ClaudeAuthError:
            self._record_request_metric("auth_error")
            raise
        except ClaudeAPIError:
            self._record_request_metric("upstream_error")
            raise

        await self._persist_rate_limit(account, headers)
        await self._persist_request_log(
            account=account,
            request_body=body_in,
            body=body,
            request_id=request_id,
            status_code="success",
        )
        self._record_request_metric("success")
        return body, headers

    # ------------------------------------------------------------- streaming

    async def stream_messages(
        self,
        *,
        request_body: Mapping[str, Any],
        api_key: _ApiKeyLike,
        request_id: str,
    ) -> AsyncIterator[StreamChunk]:
        """Forward a streaming POST ``/v1/messages`` to Anthropic.

        Yields the chat client's :class:`StreamChunk` events verbatim — SSE
        bytes are forwarded unchanged. After the upstream emits ``message_stop``
        and the final ``usage`` chunk, this method persists rate-limit cache
        (if any headers were emitted) and writes exactly one ``request_logs``
        row.

        Mid-stream 401 triggers one rotate-and-retry; on the second 401 the
        account is marked unhealthy and the exception is propagated.
        """
        self._authorize_provider_scope(api_key)
        account = await self._select_account()
        access_token = await self._auth.get_access_token(account)
        # Passthrough — see ``stream_or_complete_messages`` for the rationale.
        body_in = request_body

        retries = 0
        rate_limit_headers: dict[str, str] | None = None
        usage_payload: dict[str, Any] | None = None
        iterator: AsyncIterator[StreamChunk] | None = None

        while True:
            iterator = await self._chat.stream_messages(access_token=access_token, request_body=body_in)
            try:
                async for chunk in iterator:
                    if chunk.kind == "headers":
                        rate_limit_headers = dict(chunk.data or {})
                        yield chunk
                        continue
                    if chunk.kind == "usage":
                        usage_payload = chunk.data
                        yield chunk
                        continue
                    yield chunk
                # Stream completed cleanly — fall through to log/cache writes.
                break
            except ClaudeAuthError:
                await self._safe_aclose(iterator)
                if retries >= 1:
                    await self._lb.record_error(account)
                    self._record_request_metric("auth_error")
                    raise
                retries += 1
                rotated = await self._auth.rotate_claude_access_token(account)
                if rotated is None:
                    # invalid_grant or refresh_token_missing — account is now
                    # DEACTIVATED; abort.
                    self._record_request_metric("auth_error")
                    raise
                access_token = await self._auth.get_access_token(account)
                continue
            except ClaudeRateLimited as exc:
                await self._safe_aclose(iterator)
                headers = getattr(exc, "headers", {}) or rate_limit_headers or {}
                await self._on_rate_limited(account=account, headers=headers)
                self._record_request_metric("rate_limited")
                raise
            except ClaudeAPIError:
                await self._safe_aclose(iterator)
                self._record_request_metric("upstream_error")
                raise
            except BaseException:
                await self._safe_aclose(iterator)
                raise

        # Post-stream persistence.
        if rate_limit_headers:
            await self._persist_rate_limit(account, rate_limit_headers)
        if usage_payload is not None:
            await self._persist_request_log_stream(
                account=account,
                request_body=body_in,
                usage=usage_payload,
                request_id=request_id,
            )
        self._record_request_metric("success")

    # ------------------------------------------------------- internals

    def _authorize_provider_scope(self, api_key: _ApiKeyLike) -> None:
        """Reject API keys whose ``provider_scope`` does not include ``claude``.

        The scope column is a comma-separated string per Phase 1's schema.
        We split defensively so a missing/empty scope is treated as no
        authorization rather than as universal access.
        """
        scope = api_key.provider_scope or ""
        if "claude" not in scope.split(","):
            raise ProviderScopeMismatch("API key is not authorized for the claude provider")

    async def _select_account(self) -> Account:
        """Pick a Claude account from the load balancer."""
        selection = await self._lb.select_account(provider="claude")
        account = getattr(selection, "account", None)
        if account is None:
            raise NoClaudeAccounts("no Claude accounts available in the pool")
        return account

    async def _send_with_retry(
        self,
        *,
        account: Account,
        access_token: str,
        request_body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Send a non-streaming request with one rotate-and-retry on 401.

        The singleflight lock lives inside :class:`ClaudeAuthManager`; this
        method does not duplicate that machinery.
        """
        try:
            return await self._chat.send_messages(access_token=access_token, request_body=request_body)
        except ClaudeAuthError:
            # Refresh via the auth manager, which serializes concurrent
            # refreshes behind the per-account singleflight (and, on Postgres,
            # behind a cross-process advisory lock). If the rotation was
            # aborted by `invalid_grant` or by a missing-refresh-token
            # response, the account is now DEACTIVATED and rotation returns
            # `None` — propagate the original 401.
            rotated = await self._auth.rotate_claude_access_token(account)
            if rotated is None:
                raise
            access_token = await self._auth.get_access_token(account)
            try:
                return await self._chat.send_messages(access_token=access_token, request_body=request_body)
            except ClaudeAuthError:
                await self._lb.record_error(account)
                raise

    async def _persist_rate_limit(
        self,
        account: Account,
        headers: Mapping[str, str],
    ) -> None:
        """Parse ``anthropic-ratelimit-*`` headers and persist the cache.

        Empty parses are a no-op (the repo helper short-circuits).
        """
        parsed = parse_anthropic_rate_limit_headers(headers)
        if not parsed:
            return
        try:
            await self._repo.update_rate_limit_cache(account.id, parsed)
        except Exception:
            logger.warning(
                "claude.rate_limit_cache.persist_failed",
                extra={"account_id": account.id},
                exc_info=True,
            )

    async def _on_rate_limited(
        self,
        *,
        account: Account,
        headers: Mapping[str, str],
    ) -> None:
        """Record a 429 cooldown and persist the rate-limit cache.

        Called from the 429 catch in :meth:`stream_or_complete_messages`.
        Both writes happen before re-raising so the dashboard surfaces the
        cooldown immediately and the next selector tick does not pick the
        account while the upstream is still rejecting requests.
        """
        try:
            await self._lb.record_claude_rate_limit_response(
                account=account,
                headers=headers,
                is_rate_limited_response=True,
            )
        except Exception:
            logger.warning(
                "claude.rate_limit.cooldown_record_failed",
                extra={"account_id": account.id},
                exc_info=True,
            )
        await self._persist_rate_limit(account, headers)

    async def _persist_request_log(
        self,
        *,
        account: Account,
        request_body: Mapping[str, Any],
        body: Mapping[str, Any],
        request_id: str,
        status_code: str,
    ) -> None:
        """Write a single ``request_logs`` row for a successful call."""
        usage = body.get("usage") if isinstance(body, Mapping) else None
        input_tokens = int((usage or {}).get("input_tokens") or 0)
        output_tokens = int((usage or {}).get("output_tokens") or 0)
        cached_tokens = int((usage or {}).get("cache_creation_input_tokens") or 0)
        model = str(request_body.get("model") or "")
        await self._logs.add_log(
            provider="claude",
            account_id=account.id,
            request_id=request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            status=status_code,
        )
        try:
            await self._repo.update_last_used_at(account.id, at=utcnow())
        except Exception:
            # best-effort — last-used bookkeeping must not poison the request
            logger.debug("claude.last_used_at.update_failed", exc_info=True)

    async def _persist_request_log_stream(
        self,
        *,
        account: Account,
        request_body: Mapping[str, Any],
        usage: Mapping[str, Any],
        request_id: str,
    ) -> None:
        """Write the ``request_logs`` row after a streaming completion."""
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cached_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        model = str(request_body.get("model") or "")
        await self._logs.add_log(
            provider="claude",
            account_id=account.id,
            request_id=request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            status="success",
        )
        try:
            await self._repo.update_last_used_at(account.id, at=utcnow())
        except Exception:
            logger.debug("claude.last_used_at.update_failed", exc_info=True)

    def _record_request_metric(self, status: str) -> None:
        """Increment ``codex_lb_claude_requests_total{status=…}``.

        The counter is imported from ``app.core.metrics.prometheus``; when
        ``prometheus_client`` is unavailable the symbol is ``None`` and this
        helper is a no-op. Any label error is swallowed so a metrics outage
        cannot poison a Claude request — observability never gates the proxy.
        """
        if codex_lb_claude_requests_total is None:
            return
        try:
            codex_lb_claude_requests_total.labels(status=status).inc()
        except Exception:  # pragma: no cover - metrics layer may reject labels
            logger.debug("claude metrics increment failed", exc_info=True)

    @staticmethod
    async def _safe_aclose(iterator: Any) -> None:
        """Best-effort close of a streaming iterator.

        Mirrors :func:`app.core.clients.anthropic.chat._safe_aclose` so a
        missing ``aclose`` (test stubs) does not crash the error path.
        """
        aclose = getattr(iterator, "aclose", None)
        if not callable(aclose):
            return
        try:
            result = aclose()
        except Exception:  # pragma: no cover
            return
        if hasattr(result, "__await__"):
            try:
                await result
            except Exception:  # pragma: no cover
                return


# ---------------------------------------------------------------------------
# Gauge refresh — exposed as a module-level helper so the /metrics ASGI
# wrapper in ``app/main.py`` can call it once per scrape without coupling
# the lifespan to the proxy service instance.
# ---------------------------------------------------------------------------


class _CountActiveRepo(Protocol):
    """Subset of :class:`ClaudeAccountRepository` used by the gauge helper."""

    async def count_active(self) -> int: ...


async def refresh_claude_accounts_active_gauge(repo: _CountActiveRepo) -> int:
    """Set ``codex_lb_claude_accounts_active`` from
    ``ClaudeAccountRepository.count_active()`` and return the new value.

    Called from the ``/metrics`` ASGI wrapper so every scrape sees a fresh
    pool-size reading. When ``prometheus_client`` is unavailable the gauge
    is ``None`` and this is a no-op that still returns the count so the
    caller can log it if needed.
    """
    count = await repo.count_active()
    if codex_lb_claude_accounts_active is not None:
        try:
            codex_lb_claude_accounts_active.set(count)
        except Exception:  # pragma: no cover - metrics layer may reject labels
            logger.debug("claude metrics gauge update failed", exc_info=True)
    return count


__all__ = [
    "ClaudeProxyService",
    "NoClaudeAccounts",
    "ProviderScopeMismatch",
    "refresh_claude_accounts_active_gauge",
]

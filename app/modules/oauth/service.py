from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    OpenAIAuthClaims,
    extract_id_token_claims,
    generate_unique_account_id,
)
from app.core.clients.account_proxy_probe import (
    ProxyProbeError,
    build_account_proxy_session,
    proxy_probe_error_from_exception,
)
from app.core.clients.oauth import (
    OAuthError,
    OAuthTokens,
    build_authorization_url,
    exchange_authorization_code,
    exchange_device_token,
    generate_pkce_pair,
    request_device_code,
)
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository
from app.modules.accounts.schemas import AccountProxyInput, AccountProxySummary
from app.modules.accounts.service import (
    AccountsService,
)
from app.modules.oauth.schemas import (
    ManualCallbackResponse,
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)

# Maximum time an OAuth attempt may hold acquired tokens in transient
# state while waiting for the operator to submit proxy fields. Bounded
# so we never retain unpersisted authenticated state indefinitely. The
# dashboard's "Finish setup" stage MUST land within this window.
_PENDING_TOKENS_TTL_SECONDS: int = 600

_async_sleep = asyncio.sleep
_SUCCESS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "oauth_success.html"
_TERMINAL_OAUTH_STATUSES = {"error", "success"}
_MAX_RETAINED_TERMINAL_OAUTH_FLOWS = 16
_PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS = 15 * 60
logger = logging.getLogger(__name__)


class _StaleOAuthAttempt(Exception):
    pass


class OauthProxyExpectationError(ValueError):
    """Raised when proxy fields are supplied without opting into proxy mode."""


def _oauth_redirect_uri(callback_host: str | None, *, settings: Settings | None = None) -> str:
    effective_settings = settings or get_settings()
    configured = effective_settings.oauth_redirect_uri.strip()
    if not callback_host:
        return configured
    host = callback_host.strip()
    if not host:
        return configured
    parsed = urlsplit(configured)
    if not parsed.scheme or not parsed.netloc:
        return configured
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


@dataclass
class OAuthState:
    flow_id: str | None = None
    status: str = "pending"
    method: str | None = None
    error_message: str | None = None
    state_token: str | None = None
    code_verifier: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    interval_seconds: int | None = None
    expires_at: float | None = None
    finished_at: float | None = None
    callback_server: "OAuthCallbackServer | None" = None
    poll_task: asyncio.Task[None] | None = None
    attempt_id: str | None = None
    cancelled: bool = False
    # When ``expect_proxy`` is True, token-arrival sites stash tokens
    # in ``pending_tokens`` and set ``status='tokens_ready'`` instead
    # of persisting the Account. ``pending_expires_at`` bounds that
    # transient state to ``_PENDING_TOKENS_TTL_SECONDS`` after arrival.
    expect_proxy: bool = False
    oauth_proxy: AccountProxyInput | None = None
    pending_tokens: OAuthTokens | None = None
    pending_expires_at: float | None = None
    finalizing_proxy: bool = False
    redirect_uri: str | None = None


class OAuthStateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = OAuthState(status="idle")
        self._flows: dict[str, OAuthState] = {}
        self._state_token_index: dict[str, str] = {}
        self._callback_server: OAuthCallbackServer | None = None
        self._callback_server_stop_task: asyncio.Task[None] | None = None

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def state(self) -> OAuthState:
        return self._state

    def get_flow_locked(self, flow_id: str | None) -> OAuthState | None:
        resolved_flow_id = flow_id or self._state.flow_id
        if resolved_flow_id is None:
            return None
        return self._flows.get(resolved_flow_id)

    def get_flow_by_state_token_locked(self, state_token: str | None) -> OAuthState | None:
        if state_token is None:
            return None
        flow_id = self._state_token_index.get(state_token)
        if flow_id is None:
            return None
        return self._flows.get(flow_id)

    def remember_flow_locked(self, flow: OAuthState) -> None:
        if flow.flow_id is None:
            raise ValueError("flow_id is required")
        self.prune_expired_pending_browser_flows_locked()
        self._flows[flow.flow_id] = flow
        if flow.state_token is not None:
            self._state_token_index[flow.state_token] = flow.flow_id
        self.set_latest_flow_locked(flow)

    def set_latest_flow_locked(self, flow: OAuthState) -> None:
        self._state = OAuthState(
            flow_id=flow.flow_id,
            status=flow.status,
            method=flow.method,
            error_message=flow.error_message,
            state_token=flow.state_token,
            code_verifier=flow.code_verifier,
            device_auth_id=flow.device_auth_id,
            user_code=flow.user_code,
            interval_seconds=flow.interval_seconds,
            expires_at=flow.expires_at,
            finished_at=flow.finished_at,
            poll_task=flow.poll_task,
        )

    def set_flow_status_locked(self, flow: OAuthState, *, status: str, error_message: str | None) -> None:
        flow.status = status
        flow.error_message = error_message
        flow.finished_at = time.time() if status in _TERMINAL_OAUTH_STATUSES else None
        self.set_latest_flow_locked(flow)
        if status in _TERMINAL_OAUTH_STATUSES:
            self.prune_terminal_flows_locked()

    def has_pending_browser_flows_locked(self) -> bool:
        self.prune_expired_pending_browser_flows_locked()
        return any(flow.method == "browser" and flow.status == "pending" for flow in self._flows.values())

    async def reset(self) -> None:
        server: OAuthCallbackServer | None = None
        async with self._lock:
            server = self._cleanup_locked()
            self._state = OAuthState(status="idle")
        if server is not None:
            await server.stop()

    def _cleanup_locked(self, *, clear_callback_server: bool = True) -> OAuthCallbackServer | None:
        for flow in self._flows.values():
            task = flow.poll_task
            if task and not task.done():
                task.cancel()
        server = self._callback_server
        if clear_callback_server:
            self._callback_server = None
        self._flows.clear()
        self._state_token_index.clear()
        return server

    def prune_terminal_flows_locked(self) -> None:
        terminal_flows = [
            flow
            for flow in self._flows.values()
            if flow.flow_id is not None and flow.status in _TERMINAL_OAUTH_STATUSES
        ]
        extra_count = len(terminal_flows) - _MAX_RETAINED_TERMINAL_OAUTH_FLOWS
        if extra_count <= 0:
            return

        terminal_flows.sort(key=lambda flow: flow.finished_at or 0)
        for flow in terminal_flows[:extra_count]:
            self.remove_flow_locked(flow)

    def prune_expired_pending_browser_flows_locked(self) -> None:
        now = time.time()
        expired_flows = [
            flow
            for flow in self._flows.values()
            if flow.method == "browser"
            and flow.status == "pending"
            and flow.expires_at is not None
            and flow.expires_at <= now
        ]
        for flow in expired_flows:
            self.remove_flow_locked(flow)

    def remove_pending_device_flows_locked(self) -> None:
        pending_device_flows = [
            flow for flow in self._flows.values() if flow.method == "device" and flow.status == "pending"
        ]
        for flow in pending_device_flows:
            task = flow.poll_task
            if task and not task.done():
                task.cancel()
            self.remove_flow_locked(flow)

    def remove_flow_locked(self, flow: OAuthState) -> None:
        removed_latest = flow.flow_id is not None and flow.flow_id == self._state.flow_id
        if flow.flow_id is not None:
            self._flows.pop(flow.flow_id, None)
        if flow.state_token is not None:
            self._state_token_index.pop(flow.state_token, None)
        if removed_latest:
            self._restore_latest_flow_locked()

    def _restore_latest_flow_locked(self) -> None:
        if not self._flows:
            self._state = OAuthState(status="idle")
            return
        latest_flow = max(
            self._flows.values(),
            key=lambda flow: flow.finished_at or flow.expires_at or 0,
        )
        self.set_latest_flow_locked(latest_flow)


class OAuthCallbackServer:
    def __init__(
        self,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        host: str = "127.0.0.1",
        port: int = 1455,
    ) -> None:
        self._handler = handler
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/auth/callback", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None


_OAUTH_STORE = OAuthStateStore()


class OauthService:
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepository]] | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        self._store = _OAUTH_STORE
        self._repo_factory = repo_factory

    async def start_oauth(self, request: OauthStartRequest, *, callback_host: str | None = None) -> OauthStartResponse:
        force_method = (request.force_method or "").lower()
        expect_proxy = bool(request.expect_proxy)
        if not expect_proxy and _start_proxy_fields_present(request):
            raise OauthProxyExpectationError("Proxy fields require expectProxy=true")
        oauth_proxy = _proxy_payload_from_start_request(request, require_proxy=expect_proxy)
        if not force_method:
            accounts = await self._accounts_repo.list_accounts()
            if accounts and not expect_proxy:
                server: OAuthCallbackServer | None = None
                stop_task: asyncio.Task[None] | None = None
                async with self._store.lock:
                    server = self._store._cleanup_locked(clear_callback_server=False)
                    self._store._state = OAuthState(status="success", expect_proxy=expect_proxy)
                    if server is not None:
                        stop_task = self._start_callback_server_stop_locked(server)
                if server is not None and stop_task is not None:
                    await self._finish_callback_server_stop(server, stop_task)
                return OauthStartResponse(method="browser")

        if force_method == "device":
            return await self._start_device_flow(
                expect_proxy=expect_proxy,
                oauth_proxy=oauth_proxy,
            )

        try:
            return await self._start_browser_flow(
                expect_proxy=expect_proxy,
                oauth_proxy=oauth_proxy,
                callback_host=callback_host,
            )
        except OSError:
            return await self._start_device_flow(
                expect_proxy=expect_proxy,
                oauth_proxy=oauth_proxy,
            )

    async def reset_oauth(self) -> None:
        await self._store.reset()

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id)
            if state is None:
                state = self._store.state if flow_id is None else OAuthState(status="pending")
            if (
                state.status == "tokens_ready"
                and state.pending_expires_at is not None
                and time.time() > state.pending_expires_at
            ):
                # Bounded retention: tokens have not been finalized
                # within ``_PENDING_TOKENS_TTL_SECONDS``. Drop them
                # so we never hold authenticated state indefinitely.
                state.pending_tokens = None
                state.pending_expires_at = None
                state.finalizing_proxy = False
                state.status = "error"
                state.error_message = "Sign-in expired before proxy finalization; please restart."
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(
        self,
        request: OauthCompleteRequest | None = None,
        *,
        accounts_service: AccountsService | None = None,
    ) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()

        # Fast-paths that don't touch persistence: keep these identical
        # to the pre-deferred-persistence behavior so callers that don't
        # opt into ``expect_proxy`` see no observable change.
        async with self._store.lock:
            flow = self._store.get_flow_locked(payload.flow_id)
            state = flow
            if state is None:
                state = self._store.state if payload.flow_id is None else OAuthState(status="pending")
            if payload.device_auth_id and flow is not None:
                flow.device_auth_id = payload.device_auth_id
            if payload.user_code and flow is not None:
                flow.user_code = payload.user_code
            if flow is not None:
                self._store.set_latest_flow_locked(flow)
            if (
                state.status == "tokens_ready"
                and state.pending_expires_at is not None
                and time.time() > state.pending_expires_at
            ):
                state.pending_tokens = None
                state.pending_expires_at = None
                state.finalizing_proxy = False
                state.status = "error"
                state.error_message = "Sign-in expired before proxy finalization; please restart."
                return OauthCompleteResponse(status="error")

            # Persistence entrypoint for the ``expect_proxy=True`` path.
            # We snapshot the state under the lock, then release it for
            # the (potentially long-running) proxy probe + DB write.
            pending_tokens: OAuthTokens | None = None
            proxy_payload: AccountProxyInput | None = None
            state_ref: OAuthState | None = None
            if state.status == "tokens_ready" and state.pending_tokens is not None:
                if state.finalizing_proxy:
                    return OauthCompleteResponse(status="pending")
                proxy_payload = _proxy_payload_from_complete_request(payload, require_proxy=True)
                pending_tokens = state.pending_tokens
                state.finalizing_proxy = True
                state_ref = state

            if pending_tokens is None:
                if state.status == "success":
                    return OauthCompleteResponse(status="success")
                if state.method != "device":
                    return OauthCompleteResponse(status="pending")
                if not self._ensure_device_poll_task_locked(state):
                    state.status = "error"
                    state.error_message = "Device code flow is not initialized."
                    return OauthCompleteResponse(status="error")
                return OauthCompleteResponse(status="pending")

        if accounts_service is None:
            raise RuntimeError(
                "complete_oauth(expect_proxy) requires AccountsService injection; wire it via OauthContext"
            )

        if proxy_payload is None:
            raise RuntimeError("complete_oauth(expect_proxy) requires validated proxy payload")
        account = self._build_account_from_tokens(pending_tokens)
        refresh_token = pending_tokens.refresh_token

        async def _ensure_attempt_still_active() -> None:
            async with self._store.lock:
                current = self._store.get_flow_locked(state_ref.flow_id if state_ref is not None else None)
                if state_ref is None or current is not state_ref or state_ref.cancelled:
                    raise _StaleOAuthAttempt

        try:
            saved = await accounts_service.persist_account_with_optional_proxy(
                account,
                proxy_payload=proxy_payload,
                refresh_token=refresh_token,
                before_upsert=_ensure_attempt_still_active,
            )
        except _StaleOAuthAttempt:
            return OauthCompleteResponse(status="error")
        except Exception:
            async with self._store.lock:
                current = self._store.get_flow_locked(state_ref.flow_id if state_ref is not None else None)
                if state_ref is not None and current is state_ref:
                    state_ref.finalizing_proxy = False
            raise

        async with self._store.lock:
            current = self._store.get_flow_locked(state_ref.flow_id if state_ref is not None else None)
            if state_ref is not None and current is state_ref:
                state_ref.pending_tokens = None
                state_ref.pending_expires_at = None
                state_ref.finalizing_proxy = False
                state_ref.status = "success"
                state_ref.error_message = None
                self._store.set_latest_flow_locked(state_ref)
        return OauthCompleteResponse(
            status="success",
            account_id=saved.id,
            proxy=_proxy_summary_from_account(saved),
        )

    async def _start_browser_flow(
        self,
        *,
        expect_proxy: bool = False,
        oauth_proxy: AccountProxyInput | None = None,
        callback_host: str | None = None,
    ) -> OauthStartResponse:
        await self._wait_for_callback_server_stop()
        flow_id = secrets.token_urlsafe(12)
        attempt_id = secrets.token_urlsafe(16)
        code_verifier, code_challenge = generate_pkce_pair()
        state_token = secrets.token_urlsafe(16)
        settings = get_settings()
        callback_server: OAuthCallbackServer | None = None
        redirect_uri = _oauth_redirect_uri(callback_host, settings=settings)
        authorization_url = build_authorization_url(
            state=state_token,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
        )

        async with self._store.lock:
            self._store.remember_flow_locked(
                OAuthState(
                    flow_id=flow_id,
                    status="pending",
                    method="browser",
                    attempt_id=attempt_id,
                    state_token=state_token,
                    code_verifier=code_verifier,
                    expires_at=time.time() + _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS,
                    expect_proxy=expect_proxy,
                    oauth_proxy=oauth_proxy,
                    redirect_uri=redirect_uri,
                )
            )
            if self._store._callback_server is None:
                callback_server = OAuthCallbackServer(
                    self._handle_callback,
                    host=settings.oauth_callback_host,
                    port=settings.oauth_callback_port,
                )
                self._store._callback_server = callback_server

        if callback_server is not None:
            try:
                await callback_server.start()
            except OSError:
                async with self._store.lock:
                    if self._store._callback_server is callback_server:
                        self._store._callback_server = None

        return OauthStartResponse(
            flow_id=flow_id,
            method="browser",
            authorization_url=authorization_url,
            callback_url=redirect_uri,
        )

    async def manual_callback(self, callback_url: str, flow_id: str | None = None) -> ManualCallbackResponse:
        """Process an OAuth callback URL pasted manually by the user.

        This is useful when the server is accessed remotely and the
        OAuth callback (localhost:1455) is not reachable from the
        user's browser.
        """
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)

        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            target_flow_id = flow.flow_id if flow is not None else flow_id
            can_update_error = target_flow_id is not None
            attempt_id = flow.attempt_id if flow is not None else None
            oauth_proxy = flow.oauth_proxy if flow is not None else None
            redirect_uri = flow.redirect_uri if flow is not None else None
            if flow_id is not None and (flow is None or flow.flow_id != flow_id):
                flow = None
                verifier = None
                target_flow_id = None
                can_update_error = False
            if flow is not None and flow.status in {"success", "tokens_ready"} and state == flow.state_token:
                return ManualCallbackResponse(status=flow.status)

        if error:
            message = f"OAuth error: {error}"
            if can_update_error:
                await self._set_error(message, flow_id=target_flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        if not code or not state or flow is None or not verifier:
            message = "Invalid OAuth callback: state mismatch or missing code."
            if can_update_error:
                await self._set_error(message, flow_id=target_flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        try:
            async with _oauth_proxy_session(oauth_proxy) as session:
                tokens = await exchange_authorization_code(
                    code=code,
                    code_verifier=verifier,
                    redirect_uri=redirect_uri,
                    session=session,
                )
            response_status = await self._accept_tokens(tokens, flow_id=flow.flow_id, attempt_id=attempt_id)
            if response_status == "stale":
                message = "OAuth attempt is no longer active."
                return ManualCallbackResponse(status="error", error_message=message)
            asyncio.create_task(self._stop_callback_server_if_idle())
            return ManualCallbackResponse(status=response_status)
        except ProxyProbeError as exc:
            await self._set_error(f"Proxy validation failed: {exc.reason.value}", flow_id=flow.flow_id)
            raise
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=exc.message)
        except AccountIdentityConflictError as exc:
            message = str(exc)
            await self._set_error(message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)
        except Exception:
            logger.exception("Unexpected manual OAuth callback error")
            message = "Unexpected OAuth callback error."
            await self._set_error(message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

    async def _start_device_flow(
        self,
        *,
        expect_proxy: bool = False,
        oauth_proxy: AccountProxyInput | None = None,
    ) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        attempt_id = secrets.token_urlsafe(16)
        try:
            async with _oauth_proxy_session(oauth_proxy) as session:
                device = await request_device_code(session=session)
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise

        async with self._store.lock:
            flow = OAuthState(
                flow_id=flow_id,
                status="pending",
                method="device",
                attempt_id=attempt_id,
                device_auth_id=device.device_auth_id,
                user_code=device.user_code,
                interval_seconds=device.interval_seconds,
                expires_at=time.time() + device.expires_in_seconds,
                expect_proxy=expect_proxy,
                oauth_proxy=oauth_proxy,
            )
            self._store.remove_pending_device_flows_locked()
            self._store.remember_flow_locked(flow)
            self._ensure_device_poll_task_locked(flow)

        return OauthStartResponse(
            flow_id=flow_id,
            method="device",
            verification_url=device.verification_url,
            user_code=device.user_code,
            device_auth_id=device.device_auth_id,
            interval_seconds=device.interval_seconds,
            expires_in_seconds=device.expires_in_seconds,
        )

    async def _handle_callback(self, request: web.Request) -> web.Response:
        params = request.rel_url.query
        error = params.get("error")
        code = params.get("code")
        state = params.get("state")

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None

        if error:
            await self._set_error(f"OAuth error: {error}", flow_id=flow.flow_id if flow is not None else None)
            return self._html_response(_error_html("Authorization failed."))

        if not code or not state or flow is None or not verifier:
            await self._set_error("Invalid OAuth callback state.", flow_id=flow.flow_id if flow is not None else None)
            return self._html_response(_error_html("Invalid OAuth callback."))

        try:
            async with _oauth_proxy_session(flow.oauth_proxy) as session:
                tokens = await exchange_authorization_code(
                    code=code,
                    code_verifier=verifier,
                    redirect_uri=flow.redirect_uri,
                    session=session,
                )
            response_status = await self._accept_tokens(tokens, flow_id=flow.flow_id, attempt_id=flow.attempt_id)
            html = _success_html() if response_status != "stale" else _error_html("OAuth attempt is no longer active.")
        except ProxyProbeError as exc:
            await self._set_error(f"Proxy validation failed: {exc.reason.value}", flow_id=flow.flow_id)
            html = _error_html(f"Proxy validation failed: {exc.reason.value}")
        except aiohttp.ClientResponseError as exc:
            await self._set_error(f"Token exchange failed: {exc.status} {exc.message}", flow_id=flow.flow_id)
            html = _error_html(f"Token exchange failed: {exc.status} {exc.message}")
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow.flow_id)
            html = _error_html(exc.message)
        except AccountIdentityConflictError as exc:
            await self._set_error(str(exc), flow_id=flow.flow_id)
            html = _error_html(str(exc))

        asyncio.create_task(self._stop_callback_server_if_idle())
        return self._html_response(html)

    async def _poll_device_tokens(self, flow_id: str | None, context: "DevicePollContext") -> None:
        try:
            while time.time() < context.expires_at:
                async with _oauth_proxy_session(context.oauth_proxy) as session:
                    tokens = await exchange_device_token(
                        device_auth_id=context.device_auth_id,
                        user_code=context.user_code,
                        session=session,
                    )
                if tokens:
                    response_status = await self._accept_tokens(
                        tokens,
                        flow_id=flow_id,
                        attempt_id=context.attempt_id,
                    )
                    if response_status == "stale":
                        return
                    return
                await _async_sleep(context.interval_seconds)
            await self._set_error("Device code expired.", flow_id=flow_id)
        except ProxyProbeError as exc:
            await self._set_error(f"Proxy validation failed: {exc.reason.value}", flow_id=flow_id)
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow_id)
        except AccountIdentityConflictError as exc:
            await self._set_error(str(exc), flow_id=flow_id)
        finally:
            async with self._store.lock:
                flow = self._store.get_flow_locked(flow_id)
                current = asyncio.current_task()
                if flow is not None and flow.poll_task is current:
                    flow.poll_task = None
                    self._store.set_latest_flow_locked(flow)

    def _ensure_device_poll_task_locked(self, state: OAuthState) -> bool:
        if state.poll_task and not state.poll_task.done():
            return True
        if not state.device_auth_id or not state.user_code or not state.expires_at:
            return False

        interval = state.interval_seconds if state.interval_seconds is not None else 0
        poll_context = DevicePollContext(
            device_auth_id=state.device_auth_id,
            user_code=state.user_code,
            interval_seconds=max(interval, 5),
            expires_at=state.expires_at,
            attempt_id=state.attempt_id,
            oauth_proxy=state.oauth_proxy,
        )
        state.poll_task = asyncio.create_task(self._poll_device_tokens(state.flow_id, poll_context))
        return True

    async def _accept_tokens(self, tokens: OAuthTokens, *, flow_id: str | None, attempt_id: str | None) -> str:
        """Hand off freshly-acquired OAuth tokens to the right persistence path.

        When the current OAuth attempt was started with ``expect_proxy=True``,
        stash the tokens in transient state with a bounded TTL and return
        ``tokens_ready`` so the dashboard's "Finish setup" step can supply
        proxy fields to ``complete_oauth`` for atomic probe + persist.

        Otherwise persist the Account immediately (today's behavior) and
        return ``success``.
        """

        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id)
            if state is None:
                return "stale"
            if state.attempt_id != attempt_id or state.cancelled:
                return "stale"
            expect_proxy = state.expect_proxy
            if expect_proxy:
                state.pending_tokens = tokens
                state.pending_expires_at = time.time() + _PENDING_TOKENS_TTL_SECONDS
                state.finalizing_proxy = False
                state.status = "tokens_ready"
                state.error_message = None
                return "tokens_ready"
        await self._persist_tokens(tokens)
        await self._set_success(flow_id)
        return "success"

    async def _persist_tokens(self, tokens: OAuthTokens) -> None:
        account = self._build_account_from_tokens(tokens)
        if self._repo_factory:
            async with self._repo_factory() as repo:
                await repo.upsert(account)
        else:
            await self._accounts_repo.upsert(account)

    def _build_account_from_tokens(self, tokens: OAuthTokens) -> Account:
        claims = extract_id_token_claims(tokens.id_token)
        auth_claims = claims.auth or OpenAIAuthClaims()
        raw_account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
        email = claims.email or DEFAULT_EMAIL
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(
            auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type,
            DEFAULT_PLAN,
        )

        return Account(
            id=account_id,
            chatgpt_account_id=raw_account_id,
            email=email,
            plan_type=plan_type,
            access_token_encrypted=self._encryptor.encrypt(tokens.access_token),
            refresh_token_encrypted=self._encryptor.encrypt(tokens.refresh_token),
            id_token_encrypted=self._encryptor.encrypt(tokens.id_token),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

    async def _set_success(self, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow_id is not None and flow is None:
                return
            if flow is None:
                self._store.state.status = "success"
                self._store.state.error_message = None
                return
            self._store.set_flow_status_locked(flow, status="success", error_message=None)

    async def _set_error(self, message: str, flow_id: str | None = None) -> None:
        async with self._store.lock:
            if flow_id is None and self._store.state.flow_id is not None:
                return
            flow = self._store.get_flow_locked(flow_id)
            if flow_id is not None and flow is None:
                return
            if flow is None:
                self._store.state.status = "error"
                self._store.state.error_message = message
                return
            self._store.set_flow_status_locked(flow, status="error", error_message=message)

    def _start_callback_server_stop_locked(self, server: OAuthCallbackServer) -> asyncio.Task[None]:
        stop_task = self._store._callback_server_stop_task
        if stop_task is not None and not stop_task.done():
            return stop_task
        stop_task = asyncio.create_task(server.stop())
        self._store._callback_server_stop_task = stop_task
        return stop_task

    async def _finish_callback_server_stop(
        self,
        server: OAuthCallbackServer,
        stop_task: asyncio.Task[None],
    ) -> None:
        try:
            await asyncio.shield(stop_task)
        finally:
            async with self._store.lock:
                if self._store._callback_server is server:
                    self._store._callback_server = None
                if self._store._callback_server_stop_task is stop_task:
                    self._store._callback_server_stop_task = None

    async def _wait_for_callback_server_stop(self) -> None:
        while True:
            async with self._store.lock:
                stop_task = self._store._callback_server_stop_task
            if stop_task is None:
                return
            await asyncio.shield(stop_task)

    async def _stop_callback_server_if_idle(self) -> None:
        server: OAuthCallbackServer | None = None
        stop_task: asyncio.Task[None] | None = None
        async with self._store.lock:
            if self._store.has_pending_browser_flows_locked():
                return
            server = self._store._callback_server
            if server:
                stop_task = self._start_callback_server_stop_locked(server)
        if server and stop_task:
            await self._finish_callback_server_stop(server, stop_task)

    @staticmethod
    def _html_response(html: str) -> web.Response:
        return web.Response(text=html, content_type="text/html")


@dataclass(frozen=True)
class DevicePollContext:
    device_auth_id: str
    user_code: str
    interval_seconds: int
    expires_at: float
    attempt_id: str | None
    oauth_proxy: AccountProxyInput | None


def _proxy_payload_from_complete_request(
    payload: OauthCompleteRequest,
    *,
    require_proxy: bool = False,
) -> AccountProxyInput | None:
    """Rebundle the flat proxy fields on ``OauthCompleteRequest`` into the
    canonical ``AccountProxyInput`` for the shared persistence helper.

    Returns ``None`` only when no proxy was submitted and ``require_proxy``
    is false. Raises ``pydantic.ValidationError`` if proxy fields are
    missing or partially supplied (e.g., host without port) — the API
    layer maps that to the 422 ``validation_error`` envelope.
    """

    proxy_fields_present = (
        payload.proxy_host is not None
        or payload.proxy_port is not None
        or payload.proxy_username is not None
        or payload.proxy_password is not None
        or payload.proxy_label is not None
        or payload.proxy_remote_dns is not True
    )
    if not proxy_fields_present and not require_proxy:
        return None
    return AccountProxyInput.model_validate(
        {
            "host": payload.proxy_host,
            "port": payload.proxy_port,
            "username": payload.proxy_username,
            "password": payload.proxy_password,
            "remote_dns": payload.proxy_remote_dns,
            "label": payload.proxy_label,
        }
    )


def _proxy_payload_from_start_request(
    payload: OauthStartRequest,
    *,
    require_proxy: bool = False,
) -> AccountProxyInput | None:
    proxy_fields_present = _start_proxy_fields_present(payload)
    if not proxy_fields_present and not require_proxy:
        return None
    return AccountProxyInput.model_validate(
        {
            "host": payload.proxy_host,
            "port": payload.proxy_port,
            "username": payload.proxy_username,
            "password": payload.proxy_password,
            "remote_dns": payload.proxy_remote_dns,
            "label": payload.proxy_label,
        }
    )


def _start_proxy_fields_present(payload: OauthStartRequest) -> bool:
    return (
        payload.proxy_host is not None
        or payload.proxy_port is not None
        or payload.proxy_username is not None
        or payload.proxy_password is not None
        or payload.proxy_label is not None
        or payload.proxy_remote_dns is not True
    )


@asynccontextmanager
async def _oauth_proxy_session(
    payload: AccountProxyInput | None,
) -> AsyncIterator[aiohttp.ClientSession | None]:
    if payload is None:
        # Non-account OAuth bootstrap is intentionally not account-shaped:
        # let the OAuth client helpers use their existing shared global
        # lease_http_session(None) path.
        yield None
        return
    logging.getLogger(__name__).info(
        "oauth_proxy_session host=%s port=%s remote_dns=%s",
        payload.host,
        payload.port,
        payload.remote_dns,
    )

    settings = get_settings()
    try:
        session = await build_account_proxy_session(
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            remote_dns=payload.remote_dns,
            timeout_seconds=float(settings.oauth_timeout_seconds),
        )
    except BaseException as exc:
        mapped = proxy_probe_error_from_exception(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    map_proxy_errors = True

    async with session:
        try:
            yield session
        except BaseException as exc:
            if map_proxy_errors:
                mapped = proxy_probe_error_from_exception(exc)
                if mapped is not None:
                    raise mapped from exc
            raise


def _proxy_summary_from_account(account: Account) -> AccountProxySummary | None:
    host = account.proxy_host
    port = account.proxy_port
    if not host or port is None:
        return None
    return AccountProxySummary(
        host=host,
        port=int(port),
        username=account.proxy_username,
        has_password=account.proxy_password_encrypted is not None,
        remote_dns=bool(account.proxy_remote_dns),
        label=account.proxy_label,
        last_validated_at=account.proxy_last_validated_at,
    )


def _success_html() -> str:
    try:
        return _SUCCESS_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return "<html><body><h1>Login complete</h1><p>Return to the dashboard.</p></body></html>"


def _error_html(message: str) -> str:
    escaped = html.escape(message, quote=True)
    return f"<html><body><h1>Login failed</h1><p>{escaped}</p></body></html>"

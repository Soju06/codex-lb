from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    OpenAIAuthClaims,
    extract_id_token_claims,
    generate_unique_account_id,
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
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository
from app.modules.oauth.schemas import (
    ManualCallbackResponse,
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)

_async_sleep = asyncio.sleep
_SUCCESS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "oauth_success.html"


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
    callback_server: "OAuthCallbackServer | None" = None
    poll_task: asyncio.Task[None] | None = None


class OAuthStateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = OAuthState(status="idle")
        self._flows: dict[str, OAuthState] = {}
        self._state_token_index: dict[str, str] = {}
        self._callback_server: OAuthCallbackServer | None = None

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
            poll_task=flow.poll_task,
        )

    def set_flow_status_locked(self, flow: OAuthState, *, status: str, error_message: str | None) -> None:
        flow.status = status
        flow.error_message = error_message
        self.set_latest_flow_locked(flow)

    def has_pending_browser_flows_locked(self) -> bool:
        return any(flow.method == "browser" and flow.status == "pending" for flow in self._flows.values())

    async def reset(self) -> None:
        server: OAuthCallbackServer | None = None
        async with self._lock:
            server = self._cleanup_locked()
            self._state = OAuthState(status="idle")
        if server is not None:
            await server.stop()

    def _cleanup_locked(self) -> OAuthCallbackServer | None:
        for flow in self._flows.values():
            task = flow.poll_task
            if task and not task.done():
                task.cancel()
        server = self._callback_server
        self._callback_server = None
        self._flows.clear()
        self._state_token_index.clear()
        return server


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

    async def start_oauth(self, request: OauthStartRequest) -> OauthStartResponse:
        force_method = (request.force_method or "").lower()
        if not force_method:
            accounts = await self._accounts_repo.list_accounts()
            if accounts:
                async with self._store.lock:
                    self._store._state = OAuthState(status="success")
                return OauthStartResponse(method="browser")

        if force_method == "device":
            return await self._start_device_flow()

        try:
            return await self._start_browser_flow()
        except OSError:
            return await self._start_device_flow()

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id) or self._store.state
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        async with self._store.lock:
            state = self._store.get_flow_locked(payload.flow_id) or self._store.state
            flow = self._store.get_flow_locked(payload.flow_id)
            if payload.device_auth_id and flow is not None:
                flow.device_auth_id = payload.device_auth_id
            if payload.user_code and flow is not None:
                flow.user_code = payload.user_code
            if flow is not None:
                self._store.set_latest_flow_locked(flow)
            if state.status == "success":
                return OauthCompleteResponse(status="success")
            if state.method != "device":
                return OauthCompleteResponse(status="pending")
            if flow is not None and flow.poll_task and not flow.poll_task.done():
                return OauthCompleteResponse(status="pending")
            if not state.device_auth_id or not state.user_code or not state.expires_at:
                if flow is not None:
                    self._store.set_flow_status_locked(
                        flow,
                        status="error",
                        error_message="Device code flow is not initialized.",
                    )
                else:
                    self._store.state.status = "error"
                    self._store.state.error_message = "Device code flow is not initialized."
                return OauthCompleteResponse(status="error")

            interval = state.interval_seconds if state.interval_seconds is not None else 0
            interval = max(interval, 0)
            poll_context = DevicePollContext(
                device_auth_id=state.device_auth_id,
                user_code=state.user_code,
                interval_seconds=interval,
                expires_at=state.expires_at,
            )
            if flow is not None:
                flow.poll_task = asyncio.create_task(self._poll_device_tokens(flow.flow_id, poll_context))
                self._store.set_latest_flow_locked(flow)
            return OauthCompleteResponse(status="pending")

    async def _start_browser_flow(self) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        code_verifier, code_challenge = generate_pkce_pair()
        state_token = secrets.token_urlsafe(16)
        authorization_url = build_authorization_url(state=state_token, code_challenge=code_challenge)
        settings = get_settings()
        callback_server: OAuthCallbackServer | None = None

        async with self._store.lock:
            self._store.remember_flow_locked(
                OAuthState(
                    flow_id=flow_id,
                    status="pending",
                    method="browser",
                    state_token=state_token,
                    code_verifier=code_verifier,
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
            callback_url=settings.oauth_redirect_uri,
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

        if error:
            message = f"OAuth error: {error}"
            await self._set_error(message, flow_id=flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            target_flow_id = flow.flow_id if flow is not None else flow_id
            if flow is not None and flow.status == "success" and state == flow.state_token:
                return ManualCallbackResponse(status="success")

        if not code or not state or flow is None or not verifier:
            message = "Invalid OAuth callback: state mismatch or missing code."
            await self._set_error(message, flow_id=target_flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

        try:
            tokens = await exchange_authorization_code(code=code, code_verifier=verifier)
            await self._persist_tokens(tokens)
            await self._set_success(flow.flow_id)
            asyncio.create_task(self._stop_callback_server_if_idle())
            return ManualCallbackResponse(status="success")
        except OAuthError as exc:
            await self._set_error(exc.message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=exc.message)
        except AccountIdentityConflictError as exc:
            message = str(exc)
            await self._set_error(message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)
        except Exception as exc:
            message = f"Unexpected error: {exc}"
            await self._set_error(message, flow_id=flow.flow_id)
            return ManualCallbackResponse(status="error", error_message=message)

    async def _start_device_flow(self) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        try:
            device = await request_device_code()
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise

        async with self._store.lock:
            self._store.remember_flow_locked(
                OAuthState(
                    flow_id=flow_id,
                    status="pending",
                    method="device",
                    device_auth_id=device.device_auth_id,
                    user_code=device.user_code,
                    interval_seconds=device.interval_seconds,
                    expires_at=time.time() + device.expires_in_seconds,
                )
            )

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
            tokens = await exchange_authorization_code(code=code, code_verifier=verifier)
            await self._persist_tokens(tokens)
            await self._set_success(flow.flow_id)
            html = _success_html()
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
                tokens = await exchange_device_token(
                    device_auth_id=context.device_auth_id,
                    user_code=context.user_code,
                )
                if tokens:
                    await self._persist_tokens(tokens)
                    await self._set_success(flow_id)
                    return
                await _async_sleep(context.interval_seconds)
            await self._set_error("Device code expired.", flow_id=flow_id)
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

    async def _persist_tokens(self, tokens: OAuthTokens) -> None:
        claims = extract_id_token_claims(tokens.id_token)
        auth_claims = claims.auth or OpenAIAuthClaims()
        raw_account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
        email = claims.email or DEFAULT_EMAIL
        account_id = generate_unique_account_id(raw_account_id, email)
        plan_type = coerce_account_plan_type(
            auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type,
            DEFAULT_PLAN,
        )

        account = Account(
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
        if self._repo_factory:
            async with self._repo_factory() as repo:
                await repo.upsert(account)
        else:
            await self._accounts_repo.upsert(account)

    async def _set_success(self, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is None:
                self._store.state.status = "success"
                self._store.state.error_message = None
                return
            self._store.set_flow_status_locked(flow, status="success", error_message=None)

    async def _set_error(self, message: str, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is None:
                self._store.state.status = "error"
                self._store.state.error_message = message
                return
            self._store.set_flow_status_locked(flow, status="error", error_message=message)

    async def _stop_callback_server_if_idle(self) -> None:
        async with self._store.lock:
            if self._store.has_pending_browser_flows_locked():
                return
            server = self._store._callback_server
            self._store._callback_server = None
        if server:
            await server.stop()

    @staticmethod
    def _html_response(html: str) -> web.Response:
        return web.Response(text=html, content_type="text/html")


@dataclass(frozen=True)
class DevicePollContext:
    device_auth_id: str
    user_code: str
    interval_seconds: int
    expires_at: float


def _success_html() -> str:
    try:
        return _SUCCESS_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return "<html><body><h1>Login complete</h1><p>Return to the dashboard.</p></body></html>"


def _error_html(message: str) -> str:
    return f"<html><body><h1>Login failed</h1><p>{message}</p></body></html>"

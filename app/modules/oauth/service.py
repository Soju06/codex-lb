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

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def state(self) -> OAuthState:
        return self._state

    async def reset(self) -> None:
        async with self._lock:
            await self._cleanup_locked()
            self._state = OAuthState(status="idle")

    async def _cleanup_locked(self) -> None:
        task = self._state.poll_task
        if task and not task.done():
            task.cancel()
        server = self._state.callback_server
        if server:
            await server.stop()


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
                    await self._store._cleanup_locked()
                    self._store._state = OAuthState(status="success")
                return OauthStartResponse(method="browser")

        if force_method == "device":
            return await self._start_device_flow()

        try:
            return await self._start_browser_flow()
        except OSError:
            return await self._start_device_flow()

    async def oauth_status(self) -> OauthStatusResponse:
        async with self._store.lock:
            state = self._store.state
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        async with self._store.lock:
            state = self._store.state
            if payload.device_auth_id:
                state.device_auth_id = payload.device_auth_id
            if payload.user_code:
                state.user_code = payload.user_code
            if state.status == "success":
                return OauthCompleteResponse(status="success")
            if state.method != "device":
                return OauthCompleteResponse(status="pending")
            if state.poll_task and not state.poll_task.done():
                return OauthCompleteResponse(status="pending")
            if not state.device_auth_id or not state.user_code or not state.expires_at:
                state.status = "error"
                state.error_message = "Device code flow is not initialized."
                return OauthCompleteResponse(status="error")

            interval = state.interval_seconds if state.interval_seconds is not None else 0
            interval = max(interval, 0)
            poll_context = DevicePollContext(
                device_auth_id=state.device_auth_id,
                user_code=state.user_code,
                interval_seconds=interval,
                expires_at=state.expires_at,
            )
            state.poll_task = asyncio.create_task(self._poll_device_tokens(poll_context))
            return OauthCompleteResponse(status="pending")

    async def _start_browser_flow(self) -> OauthStartResponse:
        await self._store.reset()
        code_verifier, code_challenge = generate_pkce_pair()
        state_token = secrets.token_urlsafe(16)
        authorization_url = build_authorization_url(state=state_token, code_challenge=code_challenge)
        settings = get_settings()

        async with self._store.lock:
            state = self._store.state
            state.status = "pending"
            state.method = "browser"
            state.state_token = state_token
            state.code_verifier = code_verifier
            state.error_message = None

        callback_server = OAuthCallbackServer(
            self._handle_callback,
            host=settings.oauth_callback_host,
            port=settings.oauth_callback_port,
        )
        await callback_server.start()

        async with self._store.lock:
            self._store.state.callback_server = callback_server

        return OauthStartResponse(
            method="browser",
            authorization_url=authorization_url,
            callback_url=settings.oauth_redirect_uri,
        )

    async def _start_device_flow(self) -> OauthStartResponse:
        await self._store.reset()
        try:
            device = await request_device_code()
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise

        async with self._store.lock:
            state = self._store.state
            state.status = "pending"
            state.method = "device"
            state.device_auth_id = device.device_auth_id
            state.user_code = device.user_code
            state.interval_seconds = device.interval_seconds
            state.expires_at = time.time() + device.expires_in_seconds
            state.error_message = None

        return OauthStartResponse(
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

        if error:
            await self._set_error(f"OAuth error: {error}")
            return self._html_response(_error_html("Authorization failed."))

        async with self._store.lock:
            expected_state = self._store.state.state_token
            verifier = self._store.state.code_verifier

        if not code or not state or state != expected_state or not verifier:
            await self._set_error("Invalid OAuth callback state.")
            return self._html_response(_error_html("Invalid OAuth callback."))

        try:
            tokens = await exchange_authorization_code(code=code, code_verifier=verifier)
            await self._persist_tokens(tokens)
            await self._set_success()
            html = _success_html()
        except OAuthError as exc:
            await self._set_error(exc.message)
            html = _error_html(exc.message)
        except AccountIdentityConflictError as exc:
            await self._set_error(str(exc))
            html = _error_html(str(exc))

        asyncio.create_task(self._stop_callback_server())
        return self._html_response(html)

    async def _poll_device_tokens(self, context: "DevicePollContext") -> None:
        try:
            while time.time() < context.expires_at:
                tokens = await exchange_device_token(
                    device_auth_id=context.device_auth_id,
                    user_code=context.user_code,
                )
                if tokens:
                    await self._persist_tokens(tokens)
                    await self._set_success()
                    return
                await _async_sleep(context.interval_seconds)
            await self._set_error("Device code expired.")
        except OAuthError as exc:
            await self._set_error(exc.message)
        except AccountIdentityConflictError as exc:
            await self._set_error(str(exc))
        finally:
            async with self._store.lock:
                current = asyncio.current_task()
                if self._store.state.poll_task is current:
                    self._store.state.poll_task = None

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

    async def _set_success(self) -> None:
        async with self._store.lock:
            self._store.state.status = "success"
            self._store.state.error_message = None

    async def _set_error(self, message: str) -> None:
        async with self._store.lock:
            self._store.state.status = "error"
            self._store.state.error_message = message

    async def _stop_callback_server(self) -> None:
        async with self._store.lock:
            server = self._store.state.callback_server
            self._store.state.callback_server = None
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

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    DEFAULT_EMAIL,
    DEFAULT_PLAN,
    OpenAIAuthClaims,
    clean_account_identity_part,
    extract_id_token_claims,
    generate_unique_account_id,
    normalize_seat_type,
    resolve_seat_identity,
)
from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.clients.oauth import (
    OAuthError,
    OAuthTokens,
    build_authorization_url,
    exchange_authorization_code,
    exchange_device_token,
    generate_pkce_pair,
    request_device_code,
)
from app.core.config.settings import OAUTH_CALLBACK_PORT, OAUTH_REDIRECT_URI, get_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository
from app.modules.oauth.repository import (
    OAuthFlowRecord,
    OAuthFlowRepository,
    epoch_to_naive_utc,
)
from app.modules.oauth.schemas import (
    ManualCallbackResponse,
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    propagate_account_routing_change,
)

_async_sleep = asyncio.sleep
logger = logging.getLogger(__name__)
_SUCCESS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "oauth_success.html"
_TERMINAL_OAUTH_STATUSES = {"error", "success"}
_MAX_RETAINED_TERMINAL_OAUTH_FLOWS = 16
_PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS = 15 * 60
_ACCOUNT_IDENTITY_CONFLICT_MESSAGE = (
    "Multiple accounts match the authenticated identity. Remove duplicate accounts and retry OAuth."
)
_REAUTH_SEAT_MISMATCH_MESSAGE = (
    "The account you signed in as is not the one being re-authenticated. "
    "No changes were made. Sign out of ChatGPT (or use a private window), then re-run "
    "reauthentication and log in as the exact account that needs repair."
)


async def _has_active_proxy_bindings(session: AsyncSession) -> bool:
    try:
        result = await session.execute(
            select(AccountProxyBinding.id).where(AccountProxyBinding.is_active.is_(True)).limit(1)
        )
        return result.scalar_one_or_none() is not None
    except OperationalError:
        return False


async def _oauth_route() -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        strict = await _has_active_proxy_bindings(session)
        try:
            return await resolve_upstream_route(
                session,
                account_id=None,
                operation="oauth",
                scope="bootstrap",
                # strict=True forces default-pool requirement; None defers to dashboard setting
                strict=strict or None,
            )
        except UpstreamProxyRouteError as exc:
            raise OAuthError(exc.reason, str(exc), status_code=502) from exc


class ReauthSeatMismatchError(Exception):
    """Raised when a targeted reauth callback returns a different seat than intended."""

    def __init__(self, intended_email: str | None, returned_email: str | None) -> None:
        self.intended_email = intended_email
        self.returned_email = returned_email
        super().__init__(
            "Reauthentication returned a different account than the one being repaired "
            f"(expected {intended_email or 'the selected seat'}, got {returned_email or 'unknown'})."
        )


@dataclass
class OAuthState:
    flow_id: str | None = None
    status: str = "pending"
    method: str | None = None
    error_message: str | None = None
    state_token: str | None = None
    intended_account_id: str | None = None
    code_verifier: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None
    interval_seconds: int | None = None
    expires_at: float | None = None
    finished_at: float | None = None
    callback_server: "OAuthCallbackServer | None" = None
    poll_task: asyncio.Task[None] | None = None


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
        store: OAuthStateStore | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._encryptor = TokenEncryptor()
        # ``store`` is the process-local runtime registry (callback server +
        # device poll tasks). It defaults to the module singleton; tests inject
        # a distinct store to simulate a second replica over the shared DB.
        self._store = store if store is not None else _OAUTH_STORE
        self._repo_factory = repo_factory

    # ------------------------------------------------------------------
    # Shared DB-backed flow persistence (cross-replica source of truth)
    # ------------------------------------------------------------------

    async def _persist_flow_record(self, record: OAuthFlowRecord) -> None:
        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            await repo.purge_expired(terminal_keep=_MAX_RETAINED_TERMINAL_OAUTH_FLOWS)
            await repo.create(record)

    async def _claim_device_slot(self, flow_id: str) -> None:
        """Atomically make ``flow_id`` the single current device flow, superseding
        any prior one. Serialized in-process for SQLite; the single UPSERT is
        atomic across replicas/processes on both backends."""

        async with sqlite_writer_section():
            async with get_background_session() as session:
                await OAuthFlowRepository(session, self._encryptor).claim_device_slot(flow_id)

    async def _consume_device_slot(self, flow_id: str | None) -> bool:
        """Atomically consume the device slot iff ``flow_id`` still holds it.

        The poller's point of no return before persisting tokens: returns False
        (and the poller aborts without persisting) when the flow was superseded
        by a newer device start, which atomically UPSERTed the slot to a
        different ``flow_id``."""

        if flow_id is None:
            return False
        async with sqlite_writer_section():
            async with get_background_session() as session:
                return await OAuthFlowRepository(session, self._encryptor).consume_device_slot(flow_id)

    async def _persist_flow_status(self, flow_id: str, *, status: str, error_message: str | None) -> bool:
        """Write a durable status transition. Returns whether it was applied; a
        non-success write is rejected (``False``) by the monotonic guard when the
        durable row is already ``success`` (a racing winner committed first)."""

        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            return await repo.set_status(flow_id, status=status, error_message=error_message)

    async def _load_flow_record(
        self,
        *,
        flow_id: str | None = None,
        state_token: str | None = None,
    ) -> OAuthFlowRecord | None:
        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            if flow_id is not None:
                return await repo.get_by_flow_id(flow_id)
            if state_token is not None:
                return await repo.get_by_state_token(state_token)
        return None

    @staticmethod
    def _record_to_state(record: OAuthFlowRecord) -> OAuthState:
        return OAuthState(
            flow_id=record.flow_id,
            status=record.status,
            method=record.method,
            error_message=record.error_message,
            state_token=record.state_token,
            intended_account_id=record.intended_account_id,
            code_verifier=record.code_verifier,
            device_auth_id=record.device_auth_id,
            user_code=record.user_code,
            interval_seconds=record.interval_seconds,
            expires_at=naive_utc_to_epoch(record.expires_at) if record.expires_at is not None else None,
            finished_at=naive_utc_to_epoch(record.finished_at) if record.finished_at is not None else None,
        )

    async def _reconcile_flow_from_durable(
        self,
        *,
        flow_id: str | None = None,
        state_token: str | None = None,
    ) -> OAuthFlowRecord | None:
        """Single authoritative reconciliation gate: make the local in-memory
        ``OAuthState`` agree with the shared DB BEFORE any local-state-based
        decision, and return the durable record.

        The shared DB always wins over a local ``pending``. This one gate closes
        the whole class of "origin replica acts on stale local state" bugs by
        unifying hydrate / terminal-reconcile / expiry-prune. Every entry point
        that resolves a flow from local state MUST call this first:

        - durable terminal (``success``/``error``) OVERRIDES a local ``pending``
          (the flow was completed here or on another replica), so no consumed
          authorization code is replayed and no stale pending is reported;
        - durable ``pending`` present but missing locally -> HYDRATE it (encrypted
          verifier + metadata) so this replica can resume/complete a flow it did
          not start;
        - durable row ABSENT or EXPIRED (``get_by_*`` returns ``None`` for an
          expired pending row) -> DROP any local non-terminal flow so its cached
          verifier / device code can never be reused past the TTL.
        """

        record = await self._load_flow_record(flow_id=flow_id, state_token=state_token)
        async with self._store.lock:
            local = self._store.get_flow_locked(flow_id) if flow_id is not None else None
            if local is None and state_token is not None:
                local = self._store.get_flow_by_state_token_locked(state_token)
            if record is None:
                # No live durable row (absent or expired-pending): a local
                # non-terminal flow is stale and MUST NOT be acted on.
                if local is not None and local.status not in _TERMINAL_OAUTH_STATUSES:
                    self._store.remove_flow_locked(local)
                return None
            if record.status in _TERMINAL_OAUTH_STATUSES:
                if local is None:
                    self._store.remember_flow_locked(self._record_to_state(record))
                elif local.status != record.status:
                    self._store.set_flow_status_locked(local, status=record.status, error_message=record.error_message)
                return record
            # Durable pending: hydrate a flow this replica never saw.
            if local is None:
                self._store.remember_flow_locked(self._record_to_state(record))
        return record

    async def start_oauth(self, request: OauthStartRequest) -> OauthStartResponse:
        force_method = (request.force_method or "").lower()
        intended_account_id = clean_account_identity_part(request.account_id)
        if not force_method and not intended_account_id:
            accounts = await self._accounts_repo.list_accounts()
            if accounts:
                server: OAuthCallbackServer | None = None
                stop_task: asyncio.Task[None] | None = None
                async with self._store.lock:
                    server = self._store._cleanup_locked(clear_callback_server=False)
                    self._store._state = OAuthState(status="success")
                    if server is not None:
                        stop_task = self._start_callback_server_stop_locked(server)
                if server is not None and stop_task is not None:
                    await self._finish_callback_server_stop(server, stop_task)
                return OauthStartResponse(method="browser")

        if force_method == "device":
            if intended_account_id:
                return await self._start_device_flow(intended_account_id=intended_account_id)
            return await self._start_device_flow()

        try:
            if intended_account_id:
                return await self._start_browser_flow(intended_account_id=intended_account_id)
            return await self._start_browser_flow()
        except OSError:
            if intended_account_id:
                return await self._start_device_flow(intended_account_id=intended_account_id)
            return await self._start_device_flow()

    async def oauth_status(self, flow_id: str | None = None) -> OauthStatusResponse:
        if flow_id is not None:
            # Durable status is authoritative: the reconciliation gate syncs the
            # local in-memory flow (terminal overrides pending; expired/absent
            # drops the stale local flow) and returns the durable record.
            record = await self._reconcile_flow_from_durable(flow_id=flow_id)
            if record is not None:
                status = record.status if record.status != "idle" else "pending"
                return OauthStatusResponse(status=status, error_message=record.error_message)
        async with self._store.lock:
            state = self._store.get_flow_locked(flow_id)
            if state is None:
                state = self._store.state if flow_id is None else OAuthState(status="pending")
            status = state.status if state.status != "idle" else "pending"
            return OauthStatusResponse(status=status, error_message=state.error_message)

    async def complete_oauth(self, request: OauthCompleteRequest | None = None) -> OauthCompleteResponse:
        payload = request or OauthCompleteRequest()
        if payload.flow_id is not None:
            # Durable status is authoritative: reconcile local state and report a
            # durable terminal directly (never re-polling a single-use code).
            record = await self._reconcile_flow_from_durable(flow_id=payload.flow_id)
            if record is not None and record.status in _TERMINAL_OAUTH_STATUSES:
                return OauthCompleteResponse(status=record.status)
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
            if state.method == "device":
                # ``/complete`` only REPORTS device status; it never starts a poll
                # task. The originating replica is the sole device poller (started
                # at ``start``), so a ``/complete`` served on a replica that did
                # not originate the flow reports the durable status (via the gate
                # above) without spawning a duplicate poller for the single-use
                # device code. If the originating replica dies mid-poll the flow
                # simply expires by TTL and the user retries. A targeted query
                # (explicit ``flow_id``, sent by the dashboard after a success
                # status) reports a terminal so the UI can invalidate; the
                # fire-and-forget acknowledgement (no ``flow_id``) returns
                # ``pending`` while the sole poller runs, even if this replica's
                # own poller just raced to a terminal in-memory.
                if state.status in _TERMINAL_OAUTH_STATUSES and payload.flow_id is not None:
                    return OauthCompleteResponse(status=state.status)
                return OauthCompleteResponse(status="pending")
            if state.status == "success":
                # Browser / manual-callback: report an observed success.
                return OauthCompleteResponse(status="success")
            return OauthCompleteResponse(status="pending")

    async def _start_browser_flow(self, *, intended_account_id: str | None = None) -> OauthStartResponse:
        await self._wait_for_callback_server_stop()

        flow_id = secrets.token_urlsafe(12)
        code_verifier, code_challenge = generate_pkce_pair()
        state_token = secrets.token_urlsafe(16)
        authorization_url = build_authorization_url(state=state_token, code_challenge=code_challenge)
        settings = get_settings()
        callback_server: OAuthCallbackServer | None = None

        expires_at = time.time() + _PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS
        async with self._store.lock:
            self._store.remember_flow_locked(
                OAuthState(
                    flow_id=flow_id,
                    status="pending",
                    method="browser",
                    state_token=state_token,
                    code_verifier=code_verifier,
                    intended_account_id=intended_account_id,
                    expires_at=expires_at,
                )
            )
            if self._store._callback_server is None:
                callback_server = OAuthCallbackServer(
                    self._handle_callback,
                    host=settings.oauth_callback_host,
                    port=OAUTH_CALLBACK_PORT,
                )
                self._store._callback_server = callback_server

        await self._persist_flow_record(
            OAuthFlowRecord(
                flow_id=flow_id,
                method="browser",
                status="pending",
                state_token=state_token,
                code_verifier=code_verifier,
                intended_account_id=intended_account_id,
                expires_at=epoch_to_naive_utc(expires_at),
            )
        )

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
            callback_url=OAUTH_REDIRECT_URI,
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

        if state is not None:
            # Durable status is authoritative: the reconciliation gate hydrates
            # (verifier + metadata) when this replica never saw the flow, and --
            # critically -- overrides a stale local ``pending`` with a durable
            # terminal and drops an expired/absent flow. This closes the callback
            # -replay class: a pasted callback for a flow already completed on
            # another replica never re-exchanges the consumed authorization code.
            await self._reconcile_flow_from_durable(state_token=state)

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            target_flow_id = flow.flow_id if flow is not None else flow_id
            can_update_error = target_flow_id is not None
            if flow_id is not None and (flow is None or flow.flow_id != flow_id):
                flow = None
                verifier = None
                target_flow_id = None
                can_update_error = False
            # Durable terminal wins: return the recorded outcome instead of
            # replaying the (already-consumed) code.
            terminal_status = flow.status if (flow is not None and flow.status in _TERMINAL_OAUTH_STATUSES) else None
            terminal_error = flow.error_message if flow is not None else None

        if terminal_status is not None:
            if terminal_status == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=terminal_error)

        if error:
            message = f"OAuth error: {error}"
            if can_update_error and await self._finalize_callback_error(message, flow_id=target_flow_id) == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=message)

        if not code or not state or flow is None or not verifier:
            message = "Invalid OAuth callback: state mismatch or missing code."
            if can_update_error and await self._finalize_callback_error(message, flow_id=target_flow_id) == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=message)

        try:
            route = await _oauth_route()
            tokens = await exchange_authorization_code(
                code=code,
                code_verifier=verifier,
                route=route,
                allow_direct_egress=route is None,
            )
            await self._persist_tokens(tokens, intended_account_id=flow.intended_account_id)
            await self._set_success(flow.flow_id)
            asyncio.create_task(self._stop_callback_server_if_idle())
            return ManualCallbackResponse(status="success")
        except OAuthError as exc:
            # Loser race: a concurrent callback may have committed success for the
            # same single-use code, so honor the durable success on a rejected
            # error write instead of surfacing this ``invalid_grant``.
            if await self._finalize_callback_error(exc.message, flow_id=flow.flow_id) == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=exc.message)
        except ReauthSeatMismatchError:
            if await self._finalize_callback_error(_REAUTH_SEAT_MISMATCH_MESSAGE, flow_id=flow.flow_id) == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=_REAUTH_SEAT_MISMATCH_MESSAGE)
        except AccountIdentityConflictError:
            if (
                await self._finalize_callback_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow.flow_id)
                == "success"
            ):
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=_ACCOUNT_IDENTITY_CONFLICT_MESSAGE)
        except Exception as exc:
            logger.error("manual OAuth callback failed exception_type=%s", type(exc).__name__)
            message = "An internal error occurred."
            if await self._finalize_callback_error(message, flow_id=flow.flow_id) == "success":
                return ManualCallbackResponse(status="success")
            return ManualCallbackResponse(status="error", error_message=message)

    async def _start_device_flow(self, *, intended_account_id: str | None = None) -> OauthStartResponse:
        flow_id = secrets.token_urlsafe(12)
        try:
            route = await _oauth_route()
            device = await request_device_code(route=route, allow_direct_egress=route is None)
        except OAuthError as exc:
            await self._set_error(exc.message)
            raise

        expires_at = time.time() + device.expires_in_seconds
        flow = OAuthState(
            flow_id=flow_id,
            status="pending",
            method="device",
            device_auth_id=device.device_auth_id,
            user_code=device.user_code,
            interval_seconds=device.interval_seconds,
            intended_account_id=intended_account_id,
            expires_at=expires_at,
        )
        async with self._store.lock:
            self._store.remove_pending_device_flows_locked()
            self._store.remember_flow_locked(flow)

        # Persist the durable row BEFORE claiming the single-active slot and
        # starting the sole poll task, so the poller's slot consume never races a
        # not-yet-written row.
        await self._persist_flow_record(
            OAuthFlowRecord(
                flow_id=flow_id,
                method="device",
                status="pending",
                device_auth_id=device.device_auth_id,
                user_code=device.user_code,
                interval_seconds=device.interval_seconds,
                intended_account_id=intended_account_id,
                expires_at=epoch_to_naive_utc(expires_at),
            )
        )

        async with self._store.lock:
            # A later device start on THIS replica may have superseded this flow
            # while its row was being persisted (the newer start removed it from
            # the local store). Claim the single-active slot and start the sole
            # poll task ONLY if this is still the current local device flow, and
            # do so while holding the store lock: the claim then happens under the
            # lock a competing start must also acquire, so claim order follows
            # supersession order and a superseded start can neither install a
            # stale slot pointer nor start a duplicate poller. A superseded start
            # still returns its device code to its caller but does not poll.
            if self._store.get_flow_locked(flow_id) is flow:
                await self._claim_device_slot(flow_id)
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

        if state is not None:
            # Durable status is authoritative (see manual_callback): reconcile
            # local state before trusting the cached verifier so a browser
            # redirect that lands back on the origin after the flow completed
            # elsewhere -- or after the TTL -- is not replayed.
            await self._reconcile_flow_from_durable(state_token=state)

        async with self._store.lock:
            flow = self._store.get_flow_by_state_token_locked(state)
            verifier = flow.code_verifier if flow is not None else None
            terminal_status = flow.status if (flow is not None and flow.status in _TERMINAL_OAUTH_STATUSES) else None
            terminal_error = flow.error_message if flow is not None else None

        # Durable terminal wins: do not replay a consumed authorization code.
        if terminal_status is not None:
            if terminal_status == "success":
                return self._html_response(_success_html())
            return self._html_response(_error_html(terminal_error or "Authorization failed."))

        if error:
            outcome = await self._finalize_callback_error(
                f"OAuth error: {error}", flow_id=flow.flow_id if flow is not None else None
            )
            if outcome == "success":
                return self._html_response(_success_html())
            return self._html_response(_error_html("Authorization failed."))

        if not code or not state or flow is None or not verifier:
            outcome = await self._finalize_callback_error(
                "Invalid OAuth callback state.", flow_id=flow.flow_id if flow is not None else None
            )
            if outcome == "success":
                return self._html_response(_success_html())
            return self._html_response(_error_html("Invalid OAuth callback."))

        try:
            route = await _oauth_route()
            tokens = await exchange_authorization_code(
                code=code,
                code_verifier=verifier,
                route=route,
                allow_direct_egress=route is None,
            )
            await self._persist_tokens(tokens, intended_account_id=flow.intended_account_id)
            await self._set_success(flow.flow_id)
            html = _success_html()
        except OAuthError as exc:
            # Loser race: honor a durable success committed by a concurrent
            # callback for the same single-use code instead of showing an error.
            html = (
                _success_html()
                if await self._finalize_callback_error(exc.message, flow_id=flow.flow_id) == "success"
                else _error_html(exc.message)
            )
        except ReauthSeatMismatchError:
            html = (
                _success_html()
                if await self._finalize_callback_error(_REAUTH_SEAT_MISMATCH_MESSAGE, flow_id=flow.flow_id) == "success"
                else _error_html(_REAUTH_SEAT_MISMATCH_MESSAGE)
            )
        except AccountIdentityConflictError:
            html = (
                _success_html()
                if await self._finalize_callback_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow.flow_id)
                == "success"
                else _error_html(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE)
            )

        asyncio.create_task(self._stop_callback_server_if_idle())
        return self._html_response(html)

    async def _poll_device_tokens(self, flow_id: str | None, context: "DevicePollContext") -> None:
        # Slot ownership is the single authority for who may complete a device
        # flow. Only the poller that atomically consumed the current device slot
        # may persist an account OR write ANY terminal status (success or error).
        # A poller that does not hold/win the slot writes NOTHING: this prevents a
        # losing/duplicate poller that received ``invalid_grant`` for the consumed
        # code from writing ``error`` during the winner's persist window (which
        # would stop the dashboard polling before the winner's success lands).
        consumed = False
        try:
            while time.time() < context.expires_at:
                route = await _oauth_route()
                tokens = await exchange_device_token(
                    device_auth_id=context.device_auth_id,
                    user_code=context.user_code,
                    route=route,
                    allow_direct_egress=route is None,
                )
                if tokens:
                    # Point of no return: consume the single-active slot. If a
                    # newer start superseded this flow, the consume matches zero
                    # rows and we abort WITHOUT persisting or writing anything.
                    consumed = await self._consume_device_slot(flow_id)
                    if not consumed:
                        return
                    await self._persist_tokens(tokens, intended_account_id=context.intended_account_id)
                    await self._set_success(flow_id)
                    return
                await _async_sleep(context.interval_seconds)
            # Code expired: only the slot holder may record the terminal error.
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error("Device code expired.", flow_id=flow_id)
        except OAuthError as exc:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(exc.message, flow_id=flow_id)
        except ReauthSeatMismatchError:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(_REAUTH_SEAT_MISMATCH_MESSAGE, flow_id=flow_id)
        except AccountIdentityConflictError:
            if consumed or await self._consume_device_slot(flow_id):
                await self._set_error(_ACCOUNT_IDENTITY_CONFLICT_MESSAGE, flow_id=flow_id)
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
            interval_seconds=max(interval, 0),
            expires_at=state.expires_at,
            intended_account_id=state.intended_account_id,
        )
        state.poll_task = asyncio.create_task(self._poll_device_tokens(state.flow_id, poll_context))
        return True

    async def _persist_tokens(
        self,
        tokens: OAuthTokens,
        *,
        intended_account_id: str | None = None,
    ) -> None:
        claims = extract_id_token_claims(tokens.id_token)
        auth_claims = claims.auth or OpenAIAuthClaims()
        raw_account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
        chatgpt_user_id = resolve_seat_identity(claims, auth_claims)
        email = claims.email or DEFAULT_EMAIL
        workspace_id = clean_account_identity_part(auth_claims.workspace_id or claims.workspace_id)
        workspace_label = clean_account_identity_part(auth_claims.workspace_label or claims.workspace_label)
        seat_type = normalize_seat_type(auth_claims.seat_type or claims.seat_type)
        account_id = generate_unique_account_id(raw_account_id, email, workspace_id, workspace_label)
        plan_type = coerce_account_plan_type(
            auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type,
            DEFAULT_PLAN,
        )

        account = Account(
            id=intended_account_id or account_id,
            chatgpt_account_id=raw_account_id,
            chatgpt_user_id=chatgpt_user_id,
            email=email,
            workspace_id=workspace_id,
            workspace_label=workspace_label,
            seat_type=seat_type,
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
                saved = await self._save_oauth_account(repo, account, intended_account_id=intended_account_id)
                saved_id = saved.id
        else:
            saved = await self._save_oauth_account(
                self._accounts_repo,
                account,
                intended_account_id=intended_account_id,
            )
            saved_id = saved.id

        clear_account_routing_unavailable(saved_id)
        await self._invalidate_account_routing_caches()

    async def _save_oauth_account(
        self,
        repo: AccountsRepository,
        account: Account,
        *,
        intended_account_id: str | None,
    ) -> Account:
        if intended_account_id is None:
            return await repo.upsert_account_slot(
                account,
                preserve_unknown_workspace_duplicates=False,
                preserve_identity_slots=True,
            )

        intended = await repo.get_by_id(intended_account_id)
        if intended is None:
            raise ReauthSeatMismatchError(None, account.email)

        intended_user_id = intended.chatgpt_user_id
        intended_claims = None
        if intended_user_id is None:
            try:
                intended_token = self._encryptor.decrypt(intended.id_token_encrypted)
                intended_claims = extract_id_token_claims(intended_token)
                intended_user_id = resolve_seat_identity(intended_claims, intended_claims.auth)
            except Exception:
                intended_user_id = None
        if intended_claims is None:
            try:
                intended_claims = extract_id_token_claims(self._encryptor.decrypt(intended.id_token_encrypted))
            except Exception:
                intended_claims = None

        intended_workspace = clean_account_identity_part(intended.workspace_id or intended.workspace_label)
        callback_workspace = clean_account_identity_part(account.workspace_id or account.workspace_label)
        callback_claims = None
        try:
            callback_claims = extract_id_token_claims(self._encryptor.decrypt(account.id_token_encrypted))
        except Exception:
            callback_claims = None

        intended_seat_ids = {
            clean_account_identity_part(value)
            for value in (intended_user_id, intended_claims.sub if intended_claims else None)
            if clean_account_identity_part(value)
        }
        callback_seat_ids = {
            clean_account_identity_part(value)
            for value in (
                account.chatgpt_user_id,
                callback_claims.sub if callback_claims else None,
            )
            if clean_account_identity_part(value)
        }

        workspace_matches = (
            intended.chatgpt_account_id is None or intended.chatgpt_account_id == account.chatgpt_account_id
        )
        if workspace_matches and intended.chatgpt_account_id is None and intended_workspace is not None:
            workspace_matches = bool(callback_workspace is not None and callback_workspace == intended_workspace)
        seat_matches = bool(intended_seat_ids & callback_seat_ids)
        if not workspace_matches or not seat_matches:
            raise ReauthSeatMismatchError(intended.email, account.email)

        saved = await repo.replace_reauthorized(intended_account_id, account)
        if saved is None:
            raise ReauthSeatMismatchError(intended.email, account.email)
        return saved

    async def _invalidate_account_routing_caches(self) -> None:
        get_account_selection_cache().invalidate()
        get_api_key_cache().clear()
        await propagate_account_routing_change()
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)

    async def _set_success(self, flow_id: str | None = None) -> None:
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is not None:
                self._store.set_flow_status_locked(flow, status="success", error_message=None)
            elif flow_id is None:
                self._store.state.status = "success"
                self._store.state.error_message = None
        if flow_id is not None:
            # Durable, cross-replica status: the originating replica reads this
            # on its next status poll instead of its stale in-memory pending.
            await self._persist_flow_status(flow_id, status="success", error_message=None)

    async def _set_error(self, message: str, flow_id: str | None = None) -> bool:
        """Record a terminal error. Returns whether the durable error was applied.

        The durable write happens FIRST: if the monotonic guard rejects it
        (``False``) because the durable row is already ``success`` — a racing
        callback/poller committed success for the same single-use code — the
        local in-memory flow is left untouched (never shadowed into ``error``),
        and the caller must honor the durable success instead of surfacing the
        error. ``flow_id`` of ``None`` updates only the local latest state.
        """

        if flow_id is None:
            async with self._store.lock:
                if self._store.state.flow_id is None:
                    self._store.state.status = "error"
                    self._store.state.error_message = message
            return True
        applied = await self._persist_flow_status(flow_id, status="error", error_message=message)
        if not applied:
            return False
        async with self._store.lock:
            flow = self._store.get_flow_locked(flow_id)
            if flow is not None:
                self._store.set_flow_status_locked(flow, status="error", error_message=message)
        return True

    async def _finalize_callback_error(self, message: str, *, flow_id: str | None) -> str:
        """Record a terminal error for a browser/manual-callback flow and return
        the status to report. If the durable error write is rejected because a
        racing callback already committed ``success`` for the same single-use
        code, reconcile the local flow and report the durable ``success`` instead
        of misreporting an error (the loser must not surface an error)."""

        if await self._set_error(message, flow_id=flow_id):
            return "error"
        record = await self._reconcile_flow_from_durable(flow_id=flow_id) if flow_id is not None else None
        if record is not None and record.status == "success":
            return "success"
        return "error"

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
    intended_account_id: str | None = None


def _success_html() -> str:
    try:
        return _SUCCESS_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return "<html><body><h1>Login complete</h1><p>Return to the dashboard.</p></body></html>"


def _error_html(message: str) -> str:
    escaped_message = html.escape(message, quote=True)
    return f"<html><body><h1>Login failed</h1><p>{escaped_message}</p></body></html>"

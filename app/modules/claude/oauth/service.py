"""Claude OAuth link flow service.

State machine, single-in-flight supersession, lazy TTL expiry, CSRF state
validation, and orchestration of the Anthropic token-exchange call.

The flow is process-local (in-memory). Multi-replica deployments share the
same caveat as ``app.modules.oauth.service``; see ``context.md``.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from urllib.parse import quote

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.config.settings import get_settings
from app.modules.claude.auth_manager import ClaudeAccountAlreadyExists
from app.modules.claude.oauth.schemas import (
    ClaudeOauthCallbackResponse,
    ClaudeOauthStartResponse,
    ClaudeOauthStatusResponse,
)
from app.modules.claude.oauth.tokens import (
    ClaudeOauthClaims,
    ClaudeOauthIdTokenError,
    decode_id_token,
    generate_pkce_pair,
)
from app.modules.claude.schemas import ClaudeAccountResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ports / protocols
# ---------------------------------------------------------------------------


class _OAuthClientPort(Protocol):
    async def exchange_authorization_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> Any: ...


class _AuthManagerPort(Protocol):
    async def add_claude_account_from_oauth(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        id_token_claims: ClaudeOauthClaims,
    ) -> str: ...


class _AccountsRepoPort(Protocol):
    async def get_by_id(self, account_id: str) -> Any: ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ClaudeOauthFlowError(Exception):
    """Raised by ``ClaudeOAuthService`` for flow-level failures.

    ``code`` matches a documented dashboard error_code (see design §error_code
    reference and the spec delta).
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# State store + flow dataclass
# ---------------------------------------------------------------------------


@dataclass
class _Flow:
    flow_id: str
    state_token: str
    code_verifier: str
    redirect_uri: str
    started_at: float
    status: Literal["pending", "success", "error"] = "pending"
    error_code: str | None = None
    error_message: str | None = None
    finished_at: float | None = None
    account_id: str | None = None


class _FlowStore:
    """In-memory, process-local store keyed by ``flow_id`` and ``state_token``."""

    def __init__(self) -> None:
        self._flows: dict[str, _Flow] = {}
        self._state_index: dict[str, str] = {}

    def add(self, flow: _Flow) -> None:
        self._flows[flow.flow_id] = flow
        self._state_index[flow.state_token] = flow.flow_id

    def get_by_id(self, flow_id: str) -> _Flow | None:
        return self._flows.get(flow_id)

    def get_by_state(self, state_token: str) -> _Flow | None:
        flow_id = self._state_index.get(state_token)
        return self._flows.get(flow_id) if flow_id else None

    def latest_pending(self) -> _Flow | None:
        for f in self._flows.values():
            if f.status == "pending":
                return f
        return None

    def remove(self, flow_id: str) -> None:
        flow = self._flows.pop(flow_id, None)
        if flow is not None:
            self._state_index.pop(flow.state_token, None)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ClaudeOAuthService:
    """Business logic for the Claude OAuth link flow.

    Three public methods map to the three HTTP endpoints:

    - ``start_oauth()`` → ``POST /api/claude/oauth/start``
    - ``oauth_status(flow_id)`` → ``GET /api/claude/oauth/status``
    - ``complete_oauth(flow_id, code, state)`` → ``POST /api/claude/oauth/callback``

    Constructor collaborators are duck-typed via Protocols so tests can pass
    lightweight stubs without instantiating the full aiohttp-backed
    :class:`ClaudeOAuthClient` or a real ``ClaudeAuthManager``.
    """

    def __init__(
        self,
        *,
        settings: Any | None = None,
        oauth_client: _OAuthClientPort | None = None,
        auth_manager: _AuthManagerPort | None = None,
        accounts_repo: _AccountsRepoPort | None = None,
        flow_store: _FlowStore | None = None,
        now_func: Any = time.time,
    ) -> None:
        self._settings = settings or get_settings()
        self._oauth_client = oauth_client
        self._auth_manager = auth_manager
        self._accounts_repo = accounts_repo
        self._store = flow_store or _FlowStore()
        self._now = now_func

    # ------------------------------------------------------------------ start

    async def start_oauth(self) -> ClaudeOauthStartResponse:
        """Create a new pending flow; supersede any prior pending flow."""
        # Supersede any prior pending flow.
        prior = self._store.latest_pending()
        if prior is not None:
            prior.status = "error"
            prior.error_code = "superseded"
            prior.error_message = "Superseded by a new OAuth flow."
            prior.finished_at = self._now()
            logger.info(
                "claude.oauth.flow.superseded",
                extra={"flow_id": prior.flow_id},
            )

        verifier, challenge = generate_pkce_pair()
        flow_id = secrets.token_urlsafe(12)
        state_token = secrets.token_urlsafe(32)
        redirect_uri = str(self._settings.claude_oauth_redirect_uri)
        scope = str(self._settings.claude_oauth_scopes)
        client_id = str(self._settings.claude_oauth_client_id)

        params = {
            # ``code=true`` selects Anthropic's OOB code-display flow on
            # ``/cai/oauth/authorize`` (matches Claude Code CLI). Without it
            # the authorize endpoint attempts a normal browser redirect that
            # we cannot complete (no local callback server). See
            # openspec/changes/fix-claude-oauth-link-endpoints for evidence.
            "code": "true",
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state_token,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
        authorize_endpoint = str(self._settings.claude_oauth_authorize_endpoint)
        authorization_url = f"{authorize_endpoint}?{qs}"

        flow = _Flow(
            flow_id=flow_id,
            state_token=state_token,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            started_at=self._now(),
        )
        self._store.add(flow)

        logger.info(
            "claude.oauth.flow.started",
            extra={"flow_id": flow_id},
        )

        raw_ttl = int(self._settings.claude_oauth_flow_ttl_seconds)
        # Schema requires ``expires_in_seconds > 0``; the flow-internal
        # store still treats ``ttl <= 0`` as "expire immediately" so the
        # service can be exercised at zero TTL without violating the
        # response-schema contract.
        expires_in_seconds = raw_ttl if raw_ttl > 0 else 1

        return ClaudeOauthStartResponse(
            flow_id=flow_id,
            authorization_url=authorization_url,
            state_token=state_token,
            expires_in_seconds=expires_in_seconds,
            callback_instructions=("Open the URL, authorize, then copy the code from claude.ai and paste it here."),
            redirect_uri=redirect_uri,
        )

    # ---------------------------------------------------------------- status

    async def oauth_status(self, flow_id: str) -> ClaudeOauthStatusResponse:
        flow = self._store.get_by_id(flow_id)
        if flow is None:
            now = datetime.now(timezone.utc)
            return ClaudeOauthStatusResponse(
                flow_id=flow_id,
                status="error",
                error_code="flow_not_found",
                error_message="No OAuth flow with that id (was it superseded?).",
                started_at=now,
                finished_at=now,
            )

        self._maybe_expire_locked(flow)
        return ClaudeOauthStatusResponse(
            flow_id=flow.flow_id,
            status=flow.status,
            error_code=flow.error_code,
            error_message=flow.error_message,
            account_id=flow.account_id,
            started_at=_dt_from_ts(flow.started_at),
            finished_at=_dt_from_ts(flow.finished_at) if flow.finished_at else None,
        )

    # -------------------------------------------------------------- complete

    async def complete_oauth(
        self,
        *,
        flow_id: str,
        code: str,
        state: str,
    ) -> ClaudeOauthCallbackResponse:
        """Validate CSRF, exchange code for tokens, persist account."""
        flow = self._store.get_by_id(flow_id)
        if flow is None:
            raise ClaudeOauthFlowError(
                "flow_not_found",
                "No OAuth flow with that id (was it superseded?).",
                http_status=404,
            )

        self._maybe_expire_locked(flow)
        if flow.status != "pending":
            if flow.error_code == "flow_expired":
                raise ClaudeOauthFlowError("flow_expired", flow.error_message or "Flow expired.", http_status=410)
            raise ClaudeOauthFlowError(
                "flow_not_pending",
                flow.error_message or "Flow is not pending.",
                http_status=409,
            )

        if not secrets.compare_digest(state, flow.state_token):
            raise ClaudeOauthFlowError(
                "state_mismatch",
                "Pasted state does not match the stored token.",
                http_status=400,
            )

        if self._oauth_client is None or self._auth_manager is None:
            # The service is constructed without collaborators only by tests
            # that exercise the ``start`` / ``status`` paths in isolation.
            # ``complete_oauth`` requires both — surface a flow-level error
            # rather than a NoneType crash so the dashboard sees a clean
            # envelope.
            raise ClaudeOauthFlowError(
                "anthropic_unreachable",
                "OAuth service is not fully wired; cannot complete the flow.",
                http_status=503,
            )

        # Token exchange via the OAuth client.
        try:
            result = await self._oauth_client.exchange_authorization_code(
                code=code,
                code_verifier=flow.code_verifier,
                redirect_uri=flow.redirect_uri,
            )
        except ClaudeAuthError as exc:
            flow.status = "error"
            flow.error_code = "invalid_grant"
            flow.error_message = str(exc)
            flow.finished_at = self._now()
            logger.warning(
                "claude.oauth.flow.callback",
                extra={"flow_id": flow.flow_id, "status": "error", "error_code": flow.error_code},
            )
            raise ClaudeOauthFlowError("invalid_grant", "Anthropic rejected the code.", http_status=502) from exc
        except ClaudeUpstreamError as exc:
            flow.status = "error"
            flow.error_code = "anthropic_unreachable"
            flow.error_message = str(exc)
            flow.finished_at = self._now()
            raise ClaudeOauthFlowError(
                "anthropic_unreachable",
                "Anthropic OAuth is unreachable.",
                http_status=502,
            ) from exc

        if not result.id_token:
            flow.status = "error"
            flow.error_code = "id_token_missing"
            flow.error_message = (
                "Anthropic did not return an id_token. Use the manual paste option to add this account."
            )
            flow.finished_at = self._now()
            raise ClaudeOauthFlowError(
                "id_token_missing",
                flow.error_message,
                http_status=400,
            )

        try:
            claims = decode_id_token(result.id_token)
        except ClaudeOauthIdTokenError as exc:
            flow.status = "error"
            flow.error_code = exc.code
            flow.error_message = str(exc)
            flow.finished_at = self._now()
            raise ClaudeOauthFlowError(
                exc.code,
                str(exc),
                http_status=400,
            ) from exc

        try:
            account_id = await self._auth_manager.add_claude_account_from_oauth(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                expires_in=result.expires_in,
                id_token_claims=claims,
            )
        except ClaudeAccountAlreadyExists as exc:
            flow.status = "error"
            flow.error_code = "account_already_exists"
            flow.error_message = str(exc)
            flow.finished_at = self._now()
            raise ClaudeOauthFlowError(
                "account_already_exists",
                "This Claude account is already in the pool.",
                http_status=409,
            ) from exc

        flow.status = "success"
        flow.account_id = account_id
        flow.finished_at = self._now()

        logger.info(
            "claude.oauth.flow.callback",
            extra={
                "flow_id": flow.flow_id,
                "status": "success",
                "account_id": account_id,
                "claude_account_uuid": claims.claude_account_uuid,
            },
        )

        if self._accounts_repo is not None:
            account = await self._accounts_repo.get_by_id(account_id)
            if account is None:
                raise ClaudeOauthFlowError(
                    "anthropic_unreachable",
                    "Account created but could not be reloaded.",
                    http_status=500,
                )
            payload = _serialize_claude_account(account)
        else:
            # Test/dev path: synthesize a minimal stub so the response shape
            # matches the production schema. ``accounts_repo`` will be wired
            # in Task 7; until then the service tolerates its absence.
            payload = _build_stub_account_payload(account_id)

        return ClaudeOauthCallbackResponse(
            status="success",
            account=ClaudeAccountResponse.model_validate(payload),
        )

    # ------------------------------------------------------ internals

    def _maybe_expire_locked(self, flow: _Flow) -> None:
        """Lazy TTL check. Marks flow ``error`` if past TTL.

        No background sweeper; expiry is detected on access.
        """
        ttl = int(self._settings.claude_oauth_flow_ttl_seconds)
        if ttl <= 0:
            expired = True
        else:
            expired = (self._now() - flow.started_at) >= ttl

        if expired and flow.status == "pending":
            flow.status = "error"
            flow.error_code = "flow_expired"
            flow.error_message = "Authorization request expired; please start a new flow."
            flow.finished_at = self._now()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt_from_ts(ts: float | None):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _serialize_claude_account(account: Any) -> dict[str, Any]:
    """Project an ``Account`` row to the public schema payload.

    Mirrors the field selection in ``app/modules/claude/api.py::_serialize_account``
    so the OAuth callback response shape matches the manual-paste response shape.
    Plaintext tokens SHALL NOT be serialized.
    """

    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        return str(value)

    status_value = getattr(account, "status", None)
    status_string = (
        status_value.value if hasattr(status_value, "value") else (str(status_value) if status_value else "active")
    )

    return {
        "id": getattr(account, "id", None),
        "claudeAccountUuid": getattr(account, "claude_account_uuid", None),
        "userEmail": getattr(account, "claude_user_email", None),
        "userOrganizationUuid": getattr(account, "claude_user_organization_uuid", None),
        "status": status_string,
        "isActive": status_string == "active",
        "claudeAccessTokenExpiresAt": _iso(getattr(account, "claude_access_token_expires_at", None)),
        "lastUsedAt": _iso(getattr(account, "last_used_at", None)),
        "rateLimitRequestsRemaining": getattr(account, "rate_limit_requests_remaining", None),
        "rateLimitInputTokensRemaining": getattr(account, "rate_limit_input_tokens_remaining", None),
        "rateLimitOutputTokensRemaining": getattr(account, "rate_limit_output_tokens_remaining", None),
        "rateLimitStatus": getattr(account, "rate_limit_status", None),
        "createdAt": _iso(getattr(account, "created_at", None)),
    }


def _build_stub_account_payload(account_id: str) -> dict[str, Any]:
    """Synthesize a minimal ``ClaudeAccountResponse`` payload from an account id.

    Used only when the service is constructed without an ``accounts_repo``
    (tests / dev mode). Production wiring provides a real repo via Task 7.
    """
    return {
        "id": account_id,
        "claudeAccountUuid": account_id,
        "userEmail": None,
        "userOrganizationUuid": None,
        "status": "active",
        "isActive": True,
        "claudeAccessTokenExpiresAt": None,
        "lastUsedAt": None,
        "rateLimitRequestsRemaining": None,
        "rateLimitInputTokensRemaining": None,
        "rateLimitOutputTokensRemaining": None,
        "rateLimitStatus": None,
        "createdAt": datetime.now(timezone.utc),
    }


__all__ = [
    "ClaudeOAuthService",
    "ClaudeOauthFlowError",
]

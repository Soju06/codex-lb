"""End-to-end SOCKS5 proxy probe.

This module performs the save-time validation of a proposed per-account
SOCKS5 proxy configuration. It is intentionally narrow: it constructs a
**one-shot** :class:`aiohttp_socks.ProxyConnector`, performs a real OAuth
``refresh_token`` request against the configured ``auth_base_url`` (defaults
to ``https://auth.openai.com``), and classifies the outcome into a typed
:class:`ProbeResult`.

Why a real refresh and not a lightweight HEAD?

- We want to surface authentication regressions (e.g. revoked refresh
  tokens) BEFORE we let the operator save a proxy that will then immediately
  trip the runtime failure tracker.
- Using the actual upstream OAuth endpoint exercises both the proxy
  negotiation AND the TLS handshake AND the upstream response, so each of
  the typed reasons (``proxy_connect``, ``proxy_auth``, ``tls``,
  ``upstream_status``, ``timeout``) can be distinguished.

Tests inject a stub session factory via :func:`_set_session_factory_for_test`
to avoid spinning up a real SOCKS5 server on the unit-test path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType
from aiohttp_socks._errors import (
    ProxyConnectionError as AiohttpSocksProxyConnectionError,
)
from aiohttp_socks._errors import (
    ProxyError as AiohttpSocksProxyError,
)
from aiohttp_socks._errors import (
    ProxyTimeoutError as AiohttpSocksProxyTimeoutError,
)
from pydantic import ValidationError
from python_socks._errors import (
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
)

from app.core.auth.models import OAuthTokenPayload
from app.core.clients.account_http import AccountProxyConnection
from app.core.config.settings import Settings, get_settings
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow

logger = logging.getLogger(__name__)


# Tuple of "TCP could not reach the SOCKS5 endpoint" errors. Includes
# the aiohttp_socks / python_socks / aiohttp variants.
_PROXY_CONNECT_ERRORS: tuple[type[BaseException], ...] = (
    ProxyConnectionError,
    AiohttpSocksProxyConnectionError,
    aiohttp.ClientProxyConnectionError,
)
_PROXY_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (
    ProxyTimeoutError,
    AiohttpSocksProxyTimeoutError,
    asyncio.TimeoutError,
)
_PROXY_PROTOCOL_ERRORS: tuple[type[BaseException], ...] = (
    ProxyError,
    AiohttpSocksProxyError,
)
_TLS_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectorSSLError,
    aiohttp.ClientConnectorCertificateError,
)


class ProbeReason(str, Enum):
    OK = "ok"
    PROXY_CONNECT = "proxy_connect"
    PROXY_AUTH = "proxy_auth"
    TLS = "tls"
    UPSTREAM_STATUS = "upstream_status"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Outcome of a single proxy probe.

    ``upstream_status_code`` is populated only for the ``ok`` and
    ``upstream_status`` reasons (i.e. when we actually reached the upstream
    endpoint and observed an HTTP response). ``checked_at`` is the timestamp
    at which the probe completed and is used by the service layer to set
    ``Account.proxy_last_validated_at`` on success.

    ``tokens`` holds the refreshed :class:`OAuthTokenPayload` when the
    probe completed an end-to-end refresh successfully. The service
    layer MUST persist these tokens (the refresh token may have been
    rotated upstream) — otherwise the account's stored refresh token
    would be stale and subsequent real refreshes would fail.
    """

    reason: ProbeReason
    detail: str | None = None
    upstream_status_code: int | None = None
    checked_at: datetime | None = None
    tokens: OAuthTokenPayload | None = None

    @property
    def ok(self) -> bool:
        return self.reason is ProbeReason.OK


class ProxyProbeError(Exception):
    """Surfaced by ``AccountsService.set_account_proxy`` when a probe fails.

    The service layer maps ``reason`` to an HTTP 422 with a stable error
    code so the dashboard can render a precise error message.
    """

    def __init__(self, reason: ProbeReason, detail: str | None = None) -> None:
        message = detail or reason.value
        super().__init__(message)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# Session factory injection (for tests)
# --------------------------------------------------------------------------

# Tests set this to short-circuit the ProxyConnector construction. The
# factory takes the AccountProxyConnection and returns a ready-to-use
# ``aiohttp.ClientSession`` (which the probe will close after use). The
# session's ``post`` is what gets exercised.
_SessionFactory = Callable[[AccountProxyConnection, float], Awaitable[Any]]
_session_factory_override: _SessionFactory | None = None


def _set_session_factory_for_test(factory: _SessionFactory | None) -> None:
    """Install a custom session factory for unit tests.

    Production code MUST NOT call this; it exists purely to bypass the real
    ``ProxyConnector`` construction during tests so we don't need a fake
    SOCKS5 server in unit-level coverage.
    """

    global _session_factory_override
    _session_factory_override = factory


async def _build_probe_session(
    connection: AccountProxyConnection,
    timeout_seconds: float,
) -> aiohttp.ClientSession:
    """Build the probe session.

    Builds an :class:`aiohttp.ClientSession` backed by
    :class:`aiohttp_socks.ProxyConnector`. ``codex`` is the only active
    account TLS profile.
    """

    if _session_factory_override is not None:
        return await _session_factory_override(connection, timeout_seconds)

    from app.core.clients.account_tls import (  # noqa: PLC0415
        cached_codex_ssl_context,
    )

    ssl_ctx: object | None = cached_codex_ssl_context()
    connector_kwargs: dict[str, Any] = {
        "proxy_type": ProxyType.SOCKS5,
        "host": connection.host,
        "port": connection.port,
        "username": connection.username,
        "password": connection.password,
        "rdns": connection.remote_dns,
    }
    if ssl_ctx is not None:
        connector_kwargs["ssl"] = ssl_ctx
    connector = ProxyConnector(**connector_kwargs)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    return aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        # Ignore environment proxies — the probe MUST exercise the explicit
        # SOCKS5 connector, not whatever HTTP_PROXY is set to.
        trust_env=False,
    )


async def build_account_proxy_session(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    remote_dns: bool,
    timeout_seconds: float,
) -> aiohttp.ClientSession:
    """Build a one-shot SOCKS5-backed session for pre-persistence OAuth calls.

    OAuth add-account flows that are started with a proxy need to route their
    server-side OAuth bootstrap/token-exchange calls through that proxy before
    an account row exists. Codex is the only active TLS profile, so the
    upstream-TLS hop always uses the singleton codex SSL context.
    """

    connection = AccountProxyConnection(
        host=host,
        port=int(port),
        username=username,
        password=password,
        remote_dns=bool(remote_dns),
    )
    return await _build_probe_session(connection, timeout_seconds)


def proxy_probe_error_from_exception(exc: BaseException) -> ProxyProbeError | None:
    """Map transport exceptions from a SOCKS5-backed OAuth call to probe errors."""

    if isinstance(exc, _PROXY_TIMEOUT_ERRORS):
        return ProxyProbeError(ProbeReason.TIMEOUT, _short_detail(exc))
    if isinstance(exc, _TLS_ERRORS):
        return ProxyProbeError(ProbeReason.TLS, _short_detail(exc))
    if isinstance(exc, _PROXY_CONNECT_ERRORS):
        return ProxyProbeError(ProbeReason.PROXY_CONNECT, _short_detail(exc))
    if isinstance(exc, _PROXY_PROTOCOL_ERRORS):
        detail = _short_detail(exc)
        reason = ProbeReason.PROXY_AUTH if _looks_like_auth_failure(detail) else ProbeReason.PROXY_CONNECT
        return ProxyProbeError(reason, detail)
    if isinstance(exc, aiohttp.ClientConnectorError):
        return ProxyProbeError(ProbeReason.PROXY_CONNECT, _short_detail(exc))
    if isinstance(exc, aiohttp.ClientResponseError):
        return ProxyProbeError(ProbeReason.UPSTREAM_STATUS, _short_detail(exc))
    return None


# --------------------------------------------------------------------------
# Probe entry point
# --------------------------------------------------------------------------


async def probe_account_proxy(
    *,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    remote_dns: bool,
    refresh_token: str,
    settings: Settings | None = None,
) -> ProbeResult:
    """Run the end-to-end SOCKS5 + OAuth refresh probe.

    Returns a :class:`ProbeResult` reflecting the classified outcome. Never
    raises :class:`ProxyProbeError` — that mapping is the API layer's job.
    """

    effective_settings = settings or get_settings()
    timeout_seconds = float(effective_settings.account_proxy_probe_timeout_seconds)
    auth_base_url = effective_settings.auth_base_url.rstrip("/")
    url = f"{auth_base_url}/oauth/token"

    connection = AccountProxyConnection(
        host=host,
        port=int(port),
        username=username,
        password=password,
        remote_dns=bool(remote_dns),
    )
    payload = {
        "grant_type": "refresh_token",
        "client_id": effective_settings.oauth_client_id,
        "refresh_token": refresh_token,
        "scope": effective_settings.oauth_scope,
    }
    headers: dict[str, str] = {}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id

    started_at = utcnow()
    try:
        session = await _build_probe_session(connection, timeout_seconds)
    except _PROXY_CONNECT_ERRORS as exc:
        return ProbeResult(
            reason=ProbeReason.PROXY_CONNECT,
            detail=_short_detail(exc),
            checked_at=utcnow(),
        )
    except _PROXY_TIMEOUT_ERRORS as exc:
        return ProbeResult(
            reason=ProbeReason.TIMEOUT,
            detail=_short_detail(exc),
            checked_at=utcnow(),
        )
    except Exception as exc:  # pragma: no cover - defensive; misclassified as connect
        logger.warning(
            "Failed to construct proxy probe session for host=%s port=%s: %s",
            host,
            port,
            exc,
        )
        return ProbeResult(
            reason=ProbeReason.PROXY_CONNECT,
            detail=_short_detail(exc),
            checked_at=utcnow(),
        )

    try:
        async with session:
            try:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as resp:
                    status = resp.status
                    if 200 <= status < 300:
                        # Capture the rotated tokens. OpenAI's OAuth
                        # provider may rotate the refresh_token on every
                        # successful refresh; if the caller doesn't
                        # persist what came back, the stored token is
                        # now stale and the next real refresh fails.
                        rotated = await _try_parse_token_payload(resp)
                        if rotated is None:
                            return ProbeResult(
                                reason=ProbeReason.INVALID_RESPONSE,
                                detail="OAuth refresh succeeded but token payload was incomplete",
                                upstream_status_code=status,
                                checked_at=utcnow(),
                            )
                        return ProbeResult(
                            reason=ProbeReason.OK,
                            upstream_status_code=status,
                            checked_at=utcnow(),
                            tokens=rotated,
                        )
                    detail = await _read_error_detail(resp)
                    return ProbeResult(
                        reason=ProbeReason.UPSTREAM_STATUS,
                        detail=detail,
                        upstream_status_code=status,
                        checked_at=utcnow(),
                    )
            except _PROXY_TIMEOUT_ERRORS as exc:
                return ProbeResult(
                    reason=ProbeReason.TIMEOUT,
                    detail=_short_detail(exc),
                    checked_at=utcnow(),
                )
            except _TLS_ERRORS as exc:
                return ProbeResult(
                    reason=ProbeReason.TLS,
                    detail=_short_detail(exc),
                    checked_at=utcnow(),
                )
            except _PROXY_CONNECT_ERRORS as exc:
                detail = _short_detail(exc)
                return ProbeResult(
                    reason=ProbeReason.PROXY_CONNECT,
                    detail=detail,
                    checked_at=utcnow(),
                )
            except _PROXY_PROTOCOL_ERRORS as exc:
                # ``ProxyError`` covers SOCKS5 protocol-level rejections
                # including ``ReplyError("Username and password
                # authentication failure")``. Use the message to split auth
                # rejections from generic negotiation problems.
                detail = _short_detail(exc)
                if _looks_like_auth_failure(detail):
                    reason = ProbeReason.PROXY_AUTH
                else:
                    reason = ProbeReason.PROXY_CONNECT
                return ProbeResult(reason=reason, detail=detail, checked_at=utcnow())
            except aiohttp.ClientConnectorError as exc:
                # Anything else at the connector layer — e.g. DNS failure
                # at the proxy when ``rdns=False`` and the local resolver
                # cannot reach the upstream — is reported as a connect-
                # level proxy error.
                return ProbeResult(
                    reason=ProbeReason.PROXY_CONNECT,
                    detail=_short_detail(exc),
                    checked_at=utcnow(),
                )
    except _PROXY_TIMEOUT_ERRORS as exc:
        return ProbeResult(
            reason=ProbeReason.TIMEOUT,
            detail=_short_detail(exc),
            checked_at=utcnow(),
        )

    # Unreachable: the inner ``try`` above either returns or re-raises.
    # Logging keeps mypy/ruff happy and surfaces unexpected control flow.
    logger.error(
        "Proxy probe finished without classification host=%s port=%s started_at=%s",
        host,
        port,
        started_at.isoformat(),
    )  # pragma: no cover
    return ProbeResult(reason=ProbeReason.PROXY_CONNECT, detail="unclassified")  # pragma: no cover


def _looks_like_auth_failure(detail: str | None) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    needles = (
        "authentication",
        "authentification",  # python_socks message stays in English; defensive
        "auth failure",
        "auth fail",
        "no acceptable authentication",
        "authorization",
        "login denied",
        "credentials",
    )
    return any(needle in lowered for needle in needles)


def _short_detail(exc: BaseException) -> str:
    message = str(exc) or exc.__class__.__name__
    return message[:200]


async def _read_error_detail(resp: aiohttp.ClientResponse) -> str:
    try:
        body = await resp.text()
    except Exception:
        return f"http_{resp.status}"
    snippet = body.strip()
    if not snippet:
        return f"http_{resp.status}"
    return snippet[:200]


async def _try_parse_token_payload(resp: aiohttp.ClientResponse) -> OAuthTokenPayload | None:
    """Try to parse the 2xx response body as an OAuth token payload.

    Returns ``None`` (rather than raising) when the body is not the
    expected shape; callers classify that as ``invalid_response`` so a
    2xx response without complete rotated tokens never persists the
    proxy. Returns a :class:`OAuthTokenPayload` only when ALL THREE
    tokens (access / refresh / id) are present so we don't half-write a
    torn refresh state.
    """

    try:
        data = await resp.json(content_type=None)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        payload = OAuthTokenPayload.model_validate(data)
    except ValidationError:
        return None
    if not (payload.access_token and payload.refresh_token and payload.id_token):
        return None
    return payload

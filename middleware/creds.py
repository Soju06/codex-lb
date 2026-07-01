"""Upstream header assembly.

Headers are a transparent passthrough: every agent header is forwarded
unchanged. The proxy invents nothing (no User-Agent / Content-Type / Accept of
its own). Two exceptions:
  1. client-owned headers (Host, Content-Length, ...) are dropped so httpx sets
     them correctly — the body length changes when we merge `include`.
  2. credentials (Authorization, chatgpt-account-id) follow the auth mode, with
     the token / account id supplied directly from config.toml `[auth]`.
"""
from __future__ import annotations

from typing import Iterable

from .config import Config

# Headers the HTTP client must own (plan-allowed) + hop-by-hop headers a proxy
# must not forward. Content-Type is NOT here: it is passed through unchanged
# (we send the body as raw bytes, so httpx never invents one).
_CLIENT_OWNED = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "accept-encoding",
}

_AUTH = "authorization"
_ACCOUNT = "chatgpt-account-id"

# Proxy control headers: consumed by the middleware, never forwarded upstream.
RESPONSES_API_BASE = "responses-api-base"
_PROXY_CONTROL = {RESPONSES_API_BASE}


def _has(headers: dict[str, str], name: str) -> bool:
    return any(k.lower() == name for k in headers)


def _should_inject(mode: str, header_present: bool) -> bool:
    if mode == "inject":
        return True  # config wins, overriding any agent value
    if mode == "passthrough_then_inject":
        return not header_present  # agent wins; config only as fallback
    return False  # passthrough: never inject


def would_inject_authorization(cfg: Config, *, agent_has_authorization: bool) -> bool:
    """True iff build_upstream_headers would set Authorization from config.
    Used by the safety guard: such a config credential must never be sent to a
    URL supplied by the request (Responses-API-Base header)."""
    return bool(cfg.auth.access_token) and _should_inject(cfg.auth.mode, agent_has_authorization)


def build_upstream_headers(
    agent_headers: Iterable[tuple[str, str]],
    cfg: Config,
) -> dict[str, str]:
    """Construct the headers sent upstream from the agent's request headers.

    Forwards every agent header verbatim except the client-owned/hop-by-hop set;
    invents nothing (no User-Agent / Accept / Content-Type of our own). The
    Authorization token and chatgpt-account-id are supplied from config `[auth]`
    per the auth mode. An empty account_id means the header is never added
    (e.g. plain Responses endpoints that don't use it).
    """
    out: dict[str, str] = {}
    for name, value in agent_headers:
        lname = name.lower()
        if lname in _CLIENT_OWNED or lname in _PROXY_CONTROL:
            continue  # drop client-owned/hop-by-hop + proxy control headers
        out[name] = value

    if would_inject_authorization(cfg, agent_has_authorization=_has(out, _AUTH)):
        _set(out, "Authorization", f"Bearer {cfg.auth.access_token}")

    account = cfg.auth.chatgpt_account_id
    if account and _should_inject(cfg.auth.mode, _has(out, _ACCOUNT)):
        _set(out, "chatgpt-account-id", account)

    # Optional explicit overrides, applied last.
    for name, value in cfg.upstream.headers.items():
        _set(out, name, value)

    return out


def _set(headers: dict[str, str], name: str, value: str) -> None:
    """Set a header, replacing any existing entry case-insensitively."""
    for k in list(headers):
        if k.lower() == name.lower():
            del headers[k]
    headers[name] = value

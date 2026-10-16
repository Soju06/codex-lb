"""Pure request policy for the desktop bridge: targets, local auth, and headers.

Header helpers take and return ``(name, value)`` pairs so duplicates, original
spelling, and order survive for both aiohttp and websockets.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from typing import Final

from app.desktop_bridge.credentials import is_visible_ascii

LOOPBACK_HOST: Final[str] = "127.0.0.1"
CODEX_NAMESPACE: Final[str] = "/backend-api/codex"

_FORBIDDEN_METHODS: Final = frozenset({"CONNECT", "TRACE"})
# Encoded dot, slash, backslash, and percent. Refusing an encoded percent means
# nested encodings (``%252e``) can never decode into a traversal downstream.
_FORBIDDEN_ESCAPES: Final = ("%2e", "%2f", "%5c", "%25")

_HOP_BY_HOP: Final = frozenset(
    {"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade", "http2-settings"}
)
# Cookies are stripped in both directions as a deliberate v1 limit. Expect is
# answered hop-locally by the bridge's own server.
_REQUEST_DROPPED: Final = _HOP_BY_HOP | {
    "host",
    "origin",
    "expect",
    "authorization",
    "proxy-authorization",
    "x-openai-actor-authorization",
    "chatgpt-account-id",
    "cookie",
    "cookie2",
    "forwarded",
    "x-real-ip",
    "true-client-ip",
    "cf-connecting-ip",
}
_FORWARDED_PREFIX: Final[str] = "x-forwarded-"
_RESPONSE_DROPPED: Final = _HOP_BY_HOP | {
    "set-cookie",
    "set-cookie2",
    "www-authenticate",
    "proxy-authenticate",
    "authorization",
    "proxy-authorization",
    "x-openai-actor-authorization",
}
# Rebuilt by each side's WebSocket library rather than copied across.
_WEBSOCKET_HANDSHAKE: Final = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
        "content-length",
    }
)


class PolicyRejection(Exception):
    """A request the bridge refuses before contacting codex-lb."""

    def __init__(self, status: int) -> None:
        """Store the local HTTP rejection status without retaining request details."""
        super().__init__(status)
        self.status = status


def checked_target(method: str, raw_target: str) -> str:
    """Return the raw origin-form target when it stays inside the Codex namespace."""
    if method.upper() in _FORBIDDEN_METHODS:
        raise PolicyRejection(405)
    if not raw_target.startswith("/") or "#" in raw_target:
        raise PolicyRejection(400)
    path = raw_target.partition("?")[0]
    lowered = path.lower()
    if "\\" in path or "//" in path or any(escape in lowered for escape in _FORBIDDEN_ESCAPES):
        raise PolicyRejection(400)
    if any(segment in (".", "..") for segment in path.split("/")):
        raise PolicyRejection(400)
    if path != CODEX_NAMESPACE and not path.startswith(f"{CODEX_NAMESPACE}/"):
        raise PolicyRejection(404)
    return raw_target


def check_host(host_values: list[str], listen_port: int) -> None:
    """Require exactly one loopback Host header matching the bridge listen port."""
    allowed = (f"{LOOPBACK_HOST}:{listen_port}", f"localhost:{listen_port}")
    if len(host_values) != 1 or host_values[0].lower() not in allowed:
        raise PolicyRejection(403)


def check_no_origin(origin_values: list[str]) -> None:
    """Reject every Origin header, including an empty value, with status 403."""
    # Desktop's native client sends no Origin; any browser-originated call is refused.
    if origin_values:
        raise PolicyRejection(403)


def bearer_token(authorization_values: list[str]) -> str:
    """Extract one visible-ASCII bearer token or reject the request with status 403."""
    if len(authorization_values) != 1:
        raise PolicyRejection(403)
    scheme, separator, token = authorization_values[0].partition(" ")
    if not separator or scheme.lower() != "bearer" or not is_visible_ascii(token):
        raise PolicyRejection(403)
    return token


def tokens_match(presented: str, expected: str) -> bool:
    """Compare two validated ASCII tokens with constant-time equality checking."""
    # Both sides are visible ASCII, which compare_digest accepts as str.
    return hmac.compare_digest(presented, expected)


def offered_subprotocols(protocol_values: list[str]) -> list[str]:
    """Parse one WebSocket protocol field; reject split offers to avoid negotiation ambiguity."""
    # aiohttp negotiates against the first field only, so a split offer is refused.
    if len(protocol_values) > 1:
        raise PolicyRejection(400)
    if not protocol_values:
        return []
    return [protocol.strip() for protocol in protocol_values[0].split(",") if protocol.strip()]


def request_headers(items: Iterable[tuple[str, str]], lb_token: str, *, websocket: bool) -> list[tuple[str, str]]:
    """Strip inbound credentials and forwarding metadata, then add only the LB bearer."""
    fields = [(name, value) for name, value in items if not name.lower().startswith(_FORWARDED_PREFIX)]
    kept = _without(fields, _REQUEST_DROPPED, websocket=websocket)
    kept.append(("Authorization", f"Bearer {lb_token}"))
    return kept


def response_headers(items: Iterable[tuple[str, str]], *, websocket: bool) -> list[tuple[str, str]]:
    """Strip credentials, cookies, and hop-by-hop fields from an upstream response."""
    return [
        (name, value)
        for name, value in _without(list(items), _RESPONSE_DROPPED, websocket=websocket)
        if not name.lower().startswith("access-control-")
    ]


def _without(fields: list[tuple[str, str]], dropped: frozenset[str], *, websocket: bool) -> list[tuple[str, str]]:
    """Filter excluded and Connection-nominated fields while preserving order and duplicates."""
    excluded = dropped | _connection_nominated(fields) | (_WEBSOCKET_HANDSHAKE if websocket else frozenset())
    return [(name, value) for name, value in fields if name.lower() not in excluded]


def _connection_nominated(fields: list[tuple[str, str]]) -> frozenset[str]:
    """Collect case-insensitive hop-by-hop field names from every Connection header."""
    return frozenset(
        token.strip().lower()
        for name, value in fields
        if name.lower() == "connection"
        for token in value.split(",")
        if token.strip()
    )

"""Uvicorn HTTP protocol selection tolerant of opportunistic upgrade offers.

JetBrains/Ktor clients attach cleartext HTTP/2 upgrade headers
(``Connection: Upgrade, HTTP2-Settings`` + ``Upgrade: h2c`` +
``HTTP2-Settings``) to ordinary HTTP/1.1 Responses API POSTs. RFC 9110
section 7.8 lets a server ignore such an offer and answer over HTTP/1.1 —
upstream OpenAI endpoints do exactly that — but uvicorn's stock protocol
implementations either wedge on the offer (httptools) or leak the declined
offer's hop-by-hop headers into the ASGI scope (h11). See
https://github.com/Soju06/codex-lb/issues/1757 and the module docstring of
``app.core.http_protocol_httptools`` for the full failure analysis.

This module exposes :func:`load_http_protocol_class`, which returns the
tolerant httptools subclass when httptools is importable (matching uvicorn's
``auto`` preference) and an h11 subclass with the same header hygiene
otherwise.

Both subclasses also release the keep-alive timer on every connection loss.
Stock uvicorn cancels it only on a clean close (``exc is None``), so after an
RST/``ECONNRESET`` the armed ``TimerHandle`` keeps the protocol graph alive for
``--timeout-keep-alive`` seconds — a per-request leak behind reverse proxies
that purge idle connections with RST. See
``tests/integration/test_http_keepalive_timer.py``.
"""

from __future__ import annotations

import asyncio

from uvicorn.protocols.http.h11_impl import H11Protocol

# Hop-by-hop headers that only exist to carry the declined protocol switch.
# ``HTTP2-Settings`` is defined exclusively for the h2c upgrade (RFC 9113
# section 3.1) and MUST NOT be forwarded once the offer is declined.
UPGRADE_HOP_BY_HOP_HEADERS = frozenset({b"upgrade", b"http2-settings"})


def combined_upgrade_offer(headers: list[tuple[bytes, bytes]]) -> bytes | None:
    """Return the accepted ``Upgrade`` token, honoring repeated/list-valued fields.

    Unlike uvicorn's ``_get_upgrade`` — which keeps only the tokens of the
    *last* ``Connection`` field (so ``Connection: Upgrade`` followed by
    ``Connection: keep-alive`` hides the offer) and the last ``Upgrade``
    field's raw value (so ``Upgrade: websocket, h2c`` matches nothing) —
    repeated fields are combined per RFC 9110 section 5.3 and the ``Upgrade``
    protocol list is tokenized per section 7.8. ``websocket`` is returned
    whenever it is among the offered protocols (the server may pick any
    offered protocol it supports); otherwise the client's first preference is
    returned. Header names must already be lowercased (both uvicorn
    implementations store them that way).
    """
    connection_tokens: list[bytes] = []
    upgrade_tokens: list[bytes] = []
    for name, value in headers:
        if name == b"connection":
            connection_tokens.extend(token.lower().strip() for token in value.split(b","))
        elif name == b"upgrade":
            upgrade_tokens.extend(token for token in (token.lower().strip() for token in value.split(b",")) if token)
    if b"upgrade" not in connection_tokens or not upgrade_tokens:
        return None
    if b"websocket" in upgrade_tokens:
        return b"websocket"
    return upgrade_tokens[0]


def offers_ignorable_upgrade(headers: list[tuple[bytes, bytes]]) -> bool:
    """True when the request offers a non-WebSocket protocol switch (e.g. h2c)."""
    upgrade = combined_upgrade_offer(headers)
    return upgrade is not None and upgrade != b"websocket"


def without_upgrade_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Drop the declined offer's hop-by-hop headers and ``Connection`` tokens."""
    sanitized: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        if name in UPGRADE_HOP_BY_HOP_HEADERS:
            continue
        if name == b"connection":
            tokens = [token.strip() for token in value.split(b",")]
            kept = [token for token in tokens if token and token.lower() not in UPGRADE_HOP_BY_HOP_HEADERS]
            if not kept:
                continue
            value = b", ".join(kept)
        sanitized.append((name, value))
    return sanitized


class UpgradeTolerantH11Protocol(H11Protocol):
    """h11 protocol that hides declined non-WebSocket upgrade offers from the app.

    The stock h11 implementation already serves such requests as plain
    HTTP/1.1 with the full body, but it exposes the declined offer's
    hop-by-hop headers in the ASGI scope and logs a spurious
    "Unsupported upgrade request." warning. ``_should_upgrade`` is the seam:
    it runs right after ``self.headers`` (the same list object referenced by
    ``scope["headers"]``) is populated, so sanitizing in place here is enough.
    """

    def connection_lost(self, exc: Exception | None) -> None:
        # Same defect as httptools_impl: stock h11_impl cancels the keep-alive
        # timer only when exc is None, so an RST-closed idle connection stays
        # pinned by the armed TimerHandle for timeout_keep_alive seconds.
        # super() first: the base sends h11.ConnectionClosed before the exc
        # check; the cancel touches only ``timeout_keep_alive_task``.
        super().connection_lost(exc)
        self._unset_keepalive_if_required()

    def _should_upgrade(self) -> bool:
        # Reimplements the stock decision on top of combined Connection fields
        # (RFC 9110 section 5.3): the stock ``_get_upgrade`` keeps only the
        # last field's tokens, so ``Connection: Upgrade`` followed by
        # ``Connection: keep-alive`` would hide the offer entirely.
        upgrade = combined_upgrade_offer(self.headers)
        if upgrade is None:
            return False
        if upgrade == b"websocket":
            if self._should_upgrade_to_ws():
                return True
            self._unsupported_upgrade_warning()
            return False
        self.headers[:] = without_upgrade_headers(self.headers)
        return False


def load_http_protocol_class() -> type[asyncio.Protocol]:
    """Return the HTTP protocol implementation for ``uvicorn.Config(http=...)``."""
    try:
        from app.core.http_protocol_httptools import UpgradeTolerantHttpToolsProtocol
    except ImportError:
        # httptools is an optional (transitive) dependency; uvicorn's "auto"
        # selection would fall back to h11 as well.
        return UpgradeTolerantH11Protocol
    return UpgradeTolerantHttpToolsProtocol

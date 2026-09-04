"""Uvicorn httptools protocol that tolerates non-WebSocket upgrade offers.

Uvicorn's ``auto`` HTTP implementation picks the ``httptools`` parser whenever
the ``httptools`` package is importable (it is, transitively via
``fastapi[standard]``). That parser treats *any* HTTP/1.1 request carrying
``Connection: Upgrade`` as a protocol switch: httptools raises
``HttpParserUpgrade`` at the end of the headers, never delivers the body, and
uvicorn only handles the WebSocket case. For every other ``Upgrade`` offer —
most notably the cleartext HTTP/2 (``h2c``) offer JetBrains/Ktor clients attach
to ordinary Responses API POSTs — uvicorn logs "Unsupported upgrade request."
and stops feeding the parser. Two failure shapes follow:

- body coalesced with the headers: the body is silently dropped, so the
  application sees an empty body (422 from request validation);
- headers and body written as separate segments (Ktor's write pattern): the
  next bytes hit the wedged parser, ``HttpParserError`` follows, and the client
  receives ``400 Bad Request / Invalid HTTP request received.``

RFC 9110 section 7.8 lets a server ignore an upgrade offer and answer over
HTTP/1.1 — upstream OpenAI endpoints do exactly that. This subclass neutralizes
non-WebSocket upgrade offers: the request head is replayed through a fresh
parser with the declined offer's hop-by-hop headers removed, and the request is
served as plain HTTP/1.1. Legitimate WebSocket upgrades keep the stock path.

See https://github.com/Soju06/codex-lb/issues/1757.

The subclass has a second, independent job: releasing the keep-alive timer on
*every* connection loss. Stock uvicorn cancels it only when the peer closed
cleanly (``exc is None``); after an RST/``ECONNRESET`` the armed
``TimerHandle`` keeps the protocol — and with it the transport, request cycle
and ASGI scope — reachable from the event loop for ``--timeout-keep-alive``
seconds. Reverse proxies purge idle server-side connections with RST, so
behind one every request leaked its protocol for the whole window. Fixing the
upgrade handling upstream therefore does not by itself make this module
retirable; see ``tests/integration/test_http_keepalive_timer.py``.
"""

from __future__ import annotations

import httptools
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

from app.core.http_protocol import combined_upgrade_offer, offers_ignorable_upgrade, without_upgrade_headers


class UpgradeTolerantHttpToolsProtocol(HttpToolsProtocol):
    """httptools protocol that serves non-WebSocket upgrade offers as HTTP/1.1."""

    def connection_lost(self, exc: Exception | None) -> None:
        # uvicorn (<= 0.52.4 and master) cancels the keep-alive timer only when
        # the peer closed cleanly (exc is None). On RST/ECONNRESET the armed
        # TimerHandle holds a bound method of this protocol, pinning it and its
        # transport/cycle/scope for timeout_keep_alive seconds. Cancel it on
        # every loss. super() first: the base clears ``self.parser`` last and
        # the cancel touches only ``timeout_keep_alive_task``. The transport is
        # not closed here on the exc path (stock semantics: the loop already
        # force-closed it).
        super().connection_lost(exc)
        self._unset_keepalive_if_required()

    def _active_parser(self) -> httptools.HttpRequestParser:
        # The base class only clears ``self.parser`` in connection_lost, after
        # which no parser callback or data_received can run.
        parser = self.parser
        assert parser is not None
        return parser

    def _should_upgrade(self) -> bool:
        # Combine repeated Connection fields (RFC 9110 section 5.3) so a
        # trailing ``Connection: keep-alive`` field cannot hide a WebSocket
        # handshake from the protocol switch (the stock ``_get_upgrade`` keeps
        # only the last field's tokens). Also used by the stock parser
        # callbacks to defer body handling until the handoff.
        return combined_upgrade_offer(self.headers) == b"websocket" and self._should_upgrade_to_ws()

    def _paused_on_ignorable_upgrade(self) -> bool:
        return self._active_parser().should_upgrade() and offers_ignorable_upgrade(self.headers)

    # -- Parser callbacks --------------------------------------------------
    # For an upgrade-offering request httptools fires on_headers_complete and
    # on_message_complete *before* feed_data raises HttpParserUpgrade, and it
    # never delivers the body. The stock callbacks would therefore start the
    # ASGI cycle with an empty-but-complete body. Defer instead: data_received
    # replays the sanitized request through a fresh parser, and these callbacks
    # then run with ``should_upgrade()`` false.

    def on_headers_complete(self) -> None:
        if self._paused_on_ignorable_upgrade():
            return
        super().on_headers_complete()

    def on_body(self, body: bytes) -> None:
        if self._paused_on_ignorable_upgrade():
            return
        super().on_body(body)

    def on_message_complete(self) -> None:
        if self._paused_on_ignorable_upgrade():
            return
        super().on_message_complete()

    def data_received(self, data: bytes) -> None:
        # Mirrors HttpToolsProtocol.data_received; the upgrade branch cannot be
        # intercepted from outside because the stock method swallows the
        # HttpParserUpgrade exception itself.
        self._unset_keepalive_if_required()

        # Replay declined offers iteratively, not recursively: a single
        # segment can pipeline many upgrade-offering requests (one replay
        # each), so recursion depth would be attacker-controlled — ~66KB of
        # minimal h2c GETs already exceeds Python's default 1000-frame limit,
        # and the RecursionError would escape into the event loop and abort
        # the connection. Each replay strips at least one declined offer from
        # ``data``, so the loop terminates.
        while True:
            try:
                self._active_parser().feed_data(data)
            except httptools.HttpParserError:
                msg = "Invalid HTTP request received."
                self.logger.warning(msg)
                self.send_400_response(msg)
            except httptools.HttpParserUpgrade as exc:
                if self._should_upgrade():
                    self.handle_websocket_upgrade()
                elif offers_ignorable_upgrade(self.headers):
                    data = self._continue_as_plain_http(data, exc)
                    continue
                else:
                    self._unsupported_upgrade_warning()
            return

    def _continue_as_plain_http(self, data: bytes, exc: httptools.HttpParserUpgrade) -> bytes:
        """Decline the offered protocol switch; return the bytes to re-feed as HTTP/1.1."""
        self.logger.debug(
            "Ignoring unsupported upgrade offer; serving the request as plain HTTP/1.1.",
        )
        # httptools pauses at the end of the headers; the exception argument is
        # the offset of the first unparsed byte in this segment (the body when
        # it arrived coalesced with the headers).
        offset = exc.args[0] if exc.args else len(data)
        head = self._sanitized_request_head()
        self.parser = httptools.HttpRequestParser(self)
        try:
            self.parser.set_dangerous_leniencies(lenient_data_after_close=True)
        except AttributeError:  # pragma: no cover - httptools < 0.6.3
            pass
        # The sanitized head no longer carries upgrade headers, so re-feeding
        # it cannot pause the fresh parser on the same offer (a *pipelined*
        # follow-up offer pauses again and takes another loop iteration in
        # data_received); malformed leftover bytes keep the stock 400
        # handling. Later segments of a split request feed the fresh parser
        # through the normal data_received path.
        return head + data[offset:]

    def _sanitized_request_head(self) -> bytes:
        """Rebuild the parsed request head without the declined upgrade offer.

        ``self.url`` and ``self.headers`` were accumulated by the parser
        callbacks of the aborted parse (header names already lowercased), so
        the head is complete even when the client split it across segments.
        """
        parser = self._active_parser()
        method = parser.get_method()
        http_version = parser.get_http_version().encode("ascii")
        lines = [b"%s %s HTTP/%s\r\n" % (method, self.url, http_version)]
        lines.extend(b"%s: %s\r\n" % (name, value) for name, value in without_upgrade_headers(self.headers))
        lines.append(b"\r\n")
        return b"".join(lines)

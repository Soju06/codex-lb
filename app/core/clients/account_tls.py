"""Codex TLS context for account-bound upstream traffic."""

from __future__ import annotations

import ssl
from functools import lru_cache


def build_codex_ssl_context() -> ssl.SSLContext:
    """Build the SSLContext for Codex-shaped account traffic.

    Codex CLI (the upstream Rust client we proxy for) uses
    ``reqwest`` + ``native-tls`` + OpenSSL 3.5.5. Because Python's
    stdlib ``ssl`` module is also a thin wrapper over OpenSSL, when
    codex-lb runs on the same OpenSSL version the
    ``ssl.create_default_context()`` ClientHello is byte-identical to
    Codex CLI's ClientHello (verified empirically against
    ``tls.peet.ws``). Account-bound upstream traffic therefore shares
    one JA3 fingerprint that matches what ChatGPT's backend would
    expect from a legitimate Codex CLI session.

    Implementation: just ``ssl.create_default_context()`` with ALPN
    set to ``["http/1.1"]``. NO ``set_ciphers`` (the OpenSSL default
    cipher list IS what we want), NO ALPN reordering, NO per-account
    variation. The singleton cache below shares this context across
    every account-bound upstream request.

    Note: aiohttp only speaks HTTP/1.1, so ``"h2"`` is deliberately
    omitted from ALPN. Advertising h2 causes some SOCKS5 proxies to
    attempt HTTP/2 framing on the tunnelled connection, which produces
    empty/garbage responses. The JA3 difference (``http/1.1`` only vs
    ``h2, http/1.1``) is negligible — many legitimate OpenSSL clients
    don't advertise h2.
    """

    context = ssl.create_default_context()
    try:
        context.set_alpn_protocols(["http/1.1"])
    except (NotImplementedError, ssl.SSLError):
        # Defensive: ALPN is supported on every modern OpenSSL
        # (Python 3.13 always has it), but ALPN support must not be a
        # hard startup dependency.
        pass
    return context


@lru_cache(maxsize=1)
def cached_codex_ssl_context() -> ssl.SSLContext:
    """Singleton cache for account-bound upstream TLS.

    There is no per-account variation: every request shares the same
    JA3 by design (see :func:`build_codex_ssl_context`). The
    ``maxsize=1`` cache makes the singleton property explicit.
    """

    return build_codex_ssl_context()


__all__ = [
    "build_codex_ssl_context",
    "cached_codex_ssl_context",
]

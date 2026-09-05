from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

_PLAINTEXT_SCHEMES = frozenset({"http", "socks5", "socks5h"})


def _encode_basic_proxy_auth(username: str, password: str) -> str:
    # RFC 7617 Basic token encoded with latin1, byte-identical to the header
    # value aiohttp derives from URL userinfo (``BasicAuth`` default encoding);
    # the CONNECT request differs only in header order. Local so the declared
    # ``aiohttp>=3.13`` floor holds (``aiohttp.encode_basic_auth`` is 3.14+).
    if ":" in username:
        raise ValueError("proxy usernames cannot contain ':'")
    token = base64.b64encode(f"{username}:{password}".encode("latin1")).decode("ascii")
    return f"Basic {token}"


@dataclass(frozen=True, slots=True)
class ResolvedProxyEndpoint:
    id: str
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    def _reject_plaintext_credentials(self) -> None:
        if self.scheme.lower() in _PLAINTEXT_SCHEMES and (self.username is not None or self.password is not None):
            raise ValueError("credential-bearing plaintext proxy URLs are forbidden")

    @property
    def proxy_url(self) -> str:
        self._reject_plaintext_credentials()
        scheme = "socks5h" if self.scheme == "socks5" else self.scheme
        auth = ""
        if self.username:
            auth = f"{quote(self.username, safe='')}:{quote(self.password or '', safe='')}@"
        return f"{scheme}://{auth}{self.host}:{self.port}"

    @property
    def proxy_url_without_credentials(self) -> str:
        scheme = "socks5h" if self.scheme == "socks5" else self.scheme
        return f"{scheme}://{self.host}:{self.port}"

    def aiohttp_proxy_kwargs(self) -> dict[str, Any]:
        """Return ``proxy``/``proxy_headers`` kwargs for aiohttp request and ws_connect.

        Credentials travel in a ``Proxy-Authorization`` header instead of URL
        userinfo so aiohttp's ``ConnectionKey``/``Connection`` reprs and
        ``ClientHttpProxyError.__str__`` never carry the password. ``latin1``
        matches aiohttp's own userinfo encoding (``BasicAuth`` default), so the
        CONNECT ``Proxy-Authorization`` value stays byte-identical (aiohttp
        emits it right after ``Host`` instead of last, which no proxy
        observes). aiohttp forwards ``proxy_headers``
        only on the CONNECT tunnel request, i.e. for TLS (``https``/``wss``)
        targets; callers must not use these kwargs for plaintext targets.
        """
        self._reject_plaintext_credentials()
        kwargs: dict[str, Any] = {"proxy": self.proxy_url_without_credentials}
        if self.username:
            kwargs["proxy_headers"] = {
                "Proxy-Authorization": _encode_basic_proxy_auth(self.username, self.password or ""),
            }
        return kwargs


@dataclass(frozen=True, slots=True)
class ResolvedUpstreamRoute:
    mode: str
    pool_id: str
    endpoint: ResolvedProxyEndpoint
    fallbacks: tuple[ResolvedProxyEndpoint, ...] = ()

    @property
    def endpoint_id(self) -> str:
        return self.endpoint.id

    @property
    def proxy_url(self) -> str:
        return self.endpoint.proxy_url

    def with_endpoint(
        self,
        endpoint: ResolvedProxyEndpoint,
        fallbacks: tuple[ResolvedProxyEndpoint, ...],
    ) -> "ResolvedUpstreamRoute":
        return ResolvedUpstreamRoute(self.mode, self.pool_id, endpoint, fallbacks)

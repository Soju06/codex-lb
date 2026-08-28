from __future__ import annotations

from typing import Final, TypeAlias, cast

from starlette.requests import HTTPConnection
from starlette.types import Scope

_RawSocketPeer: TypeAlias = tuple[str, int] | None
_RAW_SOCKET_PEER_SCOPE_KEY: Final = "_codex_lb_raw_socket_peer"


def _capture_raw_socket_peer(scope: Scope) -> None:
    """Store the server-observed peer before proxy headers mutate the scope."""
    scope[_RAW_SOCKET_PEER_SCOPE_KEY] = cast(_RawSocketPeer, scope.get("client"))


def raw_socket_peer_host(connection: HTTPConnection) -> str | None:
    """Return the server-observed socket peer, failing closed if uncaptured."""
    if _RAW_SOCKET_PEER_SCOPE_KEY not in connection.scope:
        return None
    peer = cast(_RawSocketPeer, connection.scope[_RAW_SOCKET_PEER_SCOPE_KEY])
    return peer[0] if peer is not None else None

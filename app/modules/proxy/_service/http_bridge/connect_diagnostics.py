"""Bounded metadata only; never serialize exception messages or arguments."""

from __future__ import annotations

import ssl

from websockets.exceptions import InvalidHandshake, InvalidStatus

from app.core.clients.proxy import ProxyResponseError

_KNOWN_TYPES = (
    ProxyResponseError,
    ssl.SSLCertVerificationError,
    ssl.SSLError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    TimeoutError,
    InvalidStatus,
    InvalidHandshake,
    OSError,
)


def exception_metadata(error: BaseException) -> list[dict[str, str | int]]:
    result: list[dict[str, str | int]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(result) < 5:
        seen.add(id(current))
        category = next((kind.__name__ for kind in _KNOWN_TYPES if isinstance(current, kind)), "Other")
        item: dict[str, str | int] = {"type": category}
        for field in ("errno", "winerror", "verify_code", "status_code"):
            value = getattr(current, field, None)
            if type(value) is int and -(2**31) <= value < 2**31:
                item[field] = value
        result.append(item)
        current = current.__cause__
    return result

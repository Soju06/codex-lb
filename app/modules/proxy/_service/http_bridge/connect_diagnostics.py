"""Bounded metadata only; never serialize exception messages or arguments."""

from __future__ import annotations

import ssl
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class ExceptionMetadata:
    """Bounded metadata for one exception in a cause chain.

    Only the known exception category and the small set of numeric fields
    below are captured. Exception messages and arguments are deliberately
    never serialized, so this stays safe to log.
    """

    type: str
    errno: int | None = None
    winerror: int | None = None
    verify_code: int | None = None
    status_code: int | None = None


def exception_metadata(error: BaseException) -> list[ExceptionMetadata]:
    result: list[ExceptionMetadata] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(result) < 5:
        seen.add(id(current))
        category = next((kind.__name__ for kind in _KNOWN_TYPES if isinstance(current, kind)), "Other")
        numeric_fields: dict[str, int] = {}
        for field in ("errno", "winerror", "verify_code", "status_code"):
            value = getattr(current, field, None)
            if type(value) is int and -(2**31) <= value < 2**31:
                numeric_fields[field] = value
        result.append(ExceptionMetadata(type=category, **numeric_fields))
        current = current.__cause__
    return result

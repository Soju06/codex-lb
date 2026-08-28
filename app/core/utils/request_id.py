from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import uuid4

_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_SCOPE_ID: ContextVar[str | None] = ContextVar("request_scope_id", default=None)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def set_request_id(value: str | None) -> Token[str | None]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def clear_request_id() -> None:
    _REQUEST_ID.set(None)


def ensure_request_id(value: str | None = None) -> str:
    """Return the active request ID, storing an explicit or generated value when needed."""
    if value:
        _REQUEST_ID.set(value)
        return value
    current = _REQUEST_ID.get()
    if current:
        return current
    generated = str(uuid4())
    _REQUEST_ID.set(generated)
    return generated


def get_request_scope_id() -> str | None:
    return _REQUEST_SCOPE_ID.get()


def set_request_scope_id(value: str | None) -> Token[str | None]:
    return _REQUEST_SCOPE_ID.set(value)


def reset_request_scope_id(token: Token[str | None]) -> None:
    _REQUEST_SCOPE_ID.reset(token)


def clear_request_scope_id() -> None:
    _REQUEST_SCOPE_ID.set(None)


def ensure_request_scope_id(value: str | None = None) -> str:
    """Return a server-owned ID that is unique to one inbound request scope."""
    if value:
        _REQUEST_SCOPE_ID.set(value)
        return value
    current = _REQUEST_SCOPE_ID.get()
    if current:
        return current
    generated = str(uuid4())
    _REQUEST_SCOPE_ID.set(generated)
    return generated

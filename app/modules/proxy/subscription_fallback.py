from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_usage_limit_reservation_transfer: ContextVar[bool] = ContextVar(
    "codex_lb_usage_limit_reservation_transfer", default=False
)


@contextmanager
def usage_limit_reservation_transfer(enabled: bool) -> Iterator[None]:
    token = _usage_limit_reservation_transfer.set(enabled)
    try:
        yield
    finally:
        _usage_limit_reservation_transfer.reset(token)


def usage_limit_reservation_transfer_enabled() -> bool:
    return _usage_limit_reservation_transfer.get()

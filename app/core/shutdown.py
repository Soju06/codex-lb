from __future__ import annotations

import asyncio
import time

_draining: bool = False
_in_flight: int = 0


def set_draining(val: bool = True) -> None:
    global _draining
    _draining = val


def increment_in_flight() -> None:
    global _in_flight
    _in_flight += 1


def decrement_in_flight() -> None:
    global _in_flight
    _in_flight = max(0, _in_flight - 1)


def get_in_flight() -> int:
    return _in_flight


async def wait_for_in_flight_drain(timeout_seconds: float, poll_interval_seconds: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while get_in_flight() > 0 and time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_seconds)
    return get_in_flight() == 0

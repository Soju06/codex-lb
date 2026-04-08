"""Startup state management."""

from __future__ import annotations

import asyncio

_startup_complete: bool = False
_bridge_registration_complete: bool = False
_bridge_registration_event: asyncio.Event | None = None


def reset_bridge_registration() -> None:
    global _bridge_registration_complete, _bridge_registration_event
    _bridge_registration_complete = False
    _bridge_registration_event = asyncio.Event()


def mark_bridge_registration_complete() -> None:
    global _bridge_registration_complete
    _bridge_registration_complete = True
    if _bridge_registration_event is not None:
        _bridge_registration_event.set()


async def wait_for_bridge_registration(timeout_seconds: float) -> bool:
    if _bridge_registration_complete:
        return True
    if _bridge_registration_event is None:
        return False
    try:
        await asyncio.wait_for(_bridge_registration_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True

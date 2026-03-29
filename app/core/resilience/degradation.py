from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class DegradationLevel(Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class DegradationManager:
    def __init__(self) -> None:
        self._level = DegradationLevel.NORMAL
        self._reason: str | None = None

    def set_degraded(self, reason: str | None = None) -> None:
        self._level = DegradationLevel.DEGRADED
        self._reason = reason
        logger.warning("Operating in degraded mode: %s", reason or "unknown reason")

    def set_normal(self) -> None:
        self._level = DegradationLevel.NORMAL
        self._reason = None

    def is_degraded(self) -> bool:
        return self._level != DegradationLevel.NORMAL

    def get_status(self) -> dict[str, str | None]:
        return {
            "level": self._level.value,
            "reason": self._reason,
        }


_manager = DegradationManager()


def set_degraded(reason: str | None = None) -> None:
    _manager.set_degraded(reason)


def set_normal() -> None:
    _manager.set_normal()


def is_degraded() -> bool:
    return _manager.is_degraded()


def get_status() -> dict[str, str | None]:
    return _manager.get_status()

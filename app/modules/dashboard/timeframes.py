from datetime import datetime, timedelta
from typing import Final, cast

from app.core.utils.time import utcnow
from app.modules.dashboard.builders import _OVERVIEW_TIMEFRAME_CONFIGS
from app.modules.dashboard.schemas import DashboardOverviewTimeframeKey

CONVERSATION_TIMEFRAME_KEYS: frozenset[str] = frozenset({"1d", "7d", "30d"})
REQUEST_LOG_TIMEFRAME_KEYS: frozenset[str] = frozenset({"1h", "24h", "7d"})
_REQUEST_LOG_TIMEFRAME_MINUTES: Final = {"1h": 60, "24h": 24 * 60, "7d": 7 * 24 * 60}


def resolve_conversation_timeframe(key: str) -> tuple[int, datetime]:
    """Return the window duration and rolling start for a conversation timeframe."""
    _key = cast(DashboardOverviewTimeframeKey, key)
    timeframe = _OVERVIEW_TIMEFRAME_CONFIGS[_key]
    return timeframe.window_minutes, utcnow() - timedelta(minutes=timeframe.window_minutes)


def resolve_request_log_timeframe(key: str) -> datetime:
    return utcnow() - timedelta(minutes=_REQUEST_LOG_TIMEFRAME_MINUTES[key])

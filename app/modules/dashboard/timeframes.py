from datetime import datetime, timedelta
from typing import cast

from app.core.utils.time import utcnow
from app.modules.dashboard.builders import _OVERVIEW_TIMEFRAME_CONFIGS
from app.modules.dashboard.schemas import DashboardOverviewTimeframeKey

CONVERSATION_TIMEFRAME_KEYS: frozenset[str] = frozenset({"1d", "7d", "30d"})


def resolve_conversation_timeframe(key: str) -> tuple[int, datetime]:
    """Return the window duration and rolling start for a conversation timeframe."""
    _key = cast(DashboardOverviewTimeframeKey, key)
    timeframe = _OVERVIEW_TIMEFRAME_CONFIGS[_key]
    return timeframe.window_minutes, utcnow() - timedelta(minutes=timeframe.window_minutes)

"""Background usage refresh cadence (fixed; issue #1340 / PRINCIPLES.md P2).

One constant feeds the refresh scheduler slice, the updater's freshness window,
the rate-limit header cache TTL and the dashboard weekly-pace freshness horizon,
so those consumers cannot drift apart. Tests monkeypatch the module attribute.
"""

from __future__ import annotations

from typing import Final

USAGE_REFRESH_INTERVAL_SECONDS: Final[int] = 60

# Rows older than this no longer count as fresh usage evidence for routing or
# account summaries: two refresh cycles, never below three minutes so a single
# missed slice does not blank the dashboard.
_FRESHNESS_HORIZON_FLOOR_SECONDS: Final[int] = 180


def usage_freshness_horizon_seconds() -> int:
    return max(USAGE_REFRESH_INTERVAL_SECONDS * 2, _FRESHNESS_HORIZON_FLOOR_SECONDS)

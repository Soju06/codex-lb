from __future__ import annotations

from datetime import datetime

from app.modules.shared.schemas import DashboardModel


class ReadSnapshotMetadata(DashboardModel):
    generated_at: datetime | None = None
    stale: bool = False
    refreshing: bool = False
    last_error: str | None = None
    generation: int = 0
    refresh_interval_seconds: int

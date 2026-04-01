from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.db.models import StickySessionKind
from app.modules.shared.schemas import DashboardModel


class StickySessionEntryResponse(DashboardModel):
    key: str
    display_name: str
    kind: StickySessionKind
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    is_stale: bool


class StickySessionsListResponse(DashboardModel):
    entries: list[StickySessionEntryResponse] = Field(default_factory=list)
    stale_prompt_cache_count: int = 0
    total: int = 0
    has_more: bool = False


class StickySessionIdentifier(DashboardModel):
    key: str = Field(min_length=1)
    kind: StickySessionKind


class StickySessionDeleteResponse(DashboardModel):
    status: str


class StickySessionsDeleteRequest(DashboardModel):
    sessions: list[StickySessionIdentifier] = Field(min_length=1, max_length=500)


class StickySessionsDeleteResponse(DashboardModel):
    deleted_count: int


class StickySessionsPurgeRequest(DashboardModel):
    stale_only: Literal[True] = True


class StickySessionsPurgeResponse(DashboardModel):
    deleted_count: int

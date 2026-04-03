from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

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

    @model_validator(mode="after")
    def validate_unique_sessions(self) -> "StickySessionsDeleteRequest":
        seen: set[tuple[str, StickySessionKind]] = set()
        for session in self.sessions:
            target = (session.key, session.kind)
            if target in seen:
                raise ValueError("duplicate sticky session targets are not allowed")
            seen.add(target)
        return self


class StickySessionDeleteFailure(DashboardModel):
    key: str
    kind: StickySessionKind
    reason: str


class StickySessionsDeleteResponse(DashboardModel):
    deleted_count: int
    deleted: list[StickySessionIdentifier] = Field(default_factory=list)
    failed: list[StickySessionDeleteFailure] = Field(default_factory=list)


class StickySessionsPurgeRequest(DashboardModel):
    stale_only: Literal[True] = True


class StickySessionsPurgeResponse(DashboardModel):
    deleted_count: int

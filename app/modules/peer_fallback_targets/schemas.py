from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class PeerFallbackTargetResponse(DashboardModel):
    id: str
    base_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PeerFallbackTargetsResponse(DashboardModel):
    targets: list[PeerFallbackTargetResponse] = Field(default_factory=list)


class PeerFallbackTargetCreateRequest(DashboardModel):
    base_url: str
    enabled: bool = True


class PeerFallbackTargetUpdateRequest(DashboardModel):
    base_url: str | None = None
    enabled: bool | None = None


class PeerFallbackTargetDeleteResponse(DashboardModel):
    status: str

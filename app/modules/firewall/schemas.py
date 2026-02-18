from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.shared.schemas import DashboardModel

FirewallMode = Literal["allow_all", "allowlist_active"]


class FirewallIpEntry(DashboardModel):
    ip_address: str
    created_at: datetime


class FirewallIpsResponse(DashboardModel):
    mode: FirewallMode
    entries: list[FirewallIpEntry] = Field(default_factory=list)


class FirewallIpCreateRequest(DashboardModel):
    ip_address: str


class FirewallDeleteResponse(DashboardModel):
    status: str

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DashboardUserResponse(DashboardModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DashboardUsersResponse(DashboardModel):
    users: list[DashboardUserResponse] = Field(default_factory=list)


class DashboardUserCreateRequest(DashboardModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(pattern=r"^(admin|user)$")


class DashboardUserUpdateRequest(DashboardModel):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: str | None = Field(default=None, pattern=r"^(admin|user)$")
    is_active: bool | None = None


class DashboardUserDeleteResponse(DashboardModel):
    status: str

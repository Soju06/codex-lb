from __future__ import annotations

from datetime import datetime

from app.modules.shared.schemas import DashboardModel


class OAuthLivePolicyUpdateRequest(DashboardModel):
    is_active: bool
    allowed_account_ids: list[str]


class OAuthLivePolicyResponse(DashboardModel):
    caller_account_id: str
    is_active: bool
    allowed_account_ids: list[str]
    created_at: datetime | None
    updated_at: datetime | None

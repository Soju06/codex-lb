from __future__ import annotations

from datetime import datetime
from typing import Any

from app.modules.shared.schemas import DashboardModel


class ConversationArchiveFileResponse(DashboardModel):
    name: str
    date: str | None
    size_bytes: int
    compressed: bool
    modified_at: datetime


class ConversationArchiveRecordResponse(DashboardModel):
    file_name: str | None = None
    timestamp: datetime | None
    request_id: str | None
    direction: str | None
    kind: str | None
    transport: str | None
    account_id: str | None
    method: str | None
    url: str | None
    status_code: int | None
    headers: dict[str, str] | None
    payload: Any
    extra: dict[str, Any] | None = None


class ConversationArchiveRecordsResponse(DashboardModel):
    records: list[ConversationArchiveRecordResponse]
    total: int
    has_more: bool

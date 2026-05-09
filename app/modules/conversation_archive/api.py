from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.modules.conversation_archive import service
from app.modules.conversation_archive.schemas import (
    ConversationArchiveFileResponse,
    ConversationArchiveRecordResponse,
    ConversationArchiveRecordsResponse,
)

router = APIRouter(
    prefix="/api/conversation-archive",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/files", response_model=list[ConversationArchiveFileResponse])
async def list_conversation_archive_files() -> list[ConversationArchiveFileResponse]:
    return [
        ConversationArchiveFileResponse(
            name=file.name,
            date=file.date,
            size_bytes=file.size_bytes,
            compressed=file.compressed,
            modified_at=file.modified_at,
        )
        for file in service.list_archive_files()
    ]


@router.get("/records", response_model=ConversationArchiveRecordsResponse)
async def list_conversation_archive_records(
    file: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    direction: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    transport: str | None = Query(default=None),
    request_id: str | None = Query(default=None, alias="requestId"),
    requested_at: datetime | None = Query(default=None, alias="requestedAt"),
) -> ConversationArchiveRecordsResponse:
    if file is None and not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file or requestId is required",
        )
    try:
        page = await run_in_threadpool(
            service.read_archive_records,
            filename=file,
            limit=limit,
            offset=offset,
            direction=direction,
            kind=kind,
            transport=transport,
            request_id=request_id,
            requested_at=requested_at,
        )
    except service.ConversationArchiveInvalidFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except service.ConversationArchiveNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ConversationArchiveRecordsResponse(
        records=[_record_response(record) for record in page.records],
        total=page.total,
        has_more=page.has_more,
    )


def _record_response(record: dict[str, Any]) -> ConversationArchiveRecordResponse:
    return ConversationArchiveRecordResponse(
        file_name=_optional_str(record.get("_archive_file")),
        timestamp=_parse_timestamp(record.get("timestamp")),
        request_id=_optional_str(record.get("request_id")),
        direction=_optional_str(record.get("direction")),
        kind=_optional_str(record.get("kind")),
        transport=_optional_str(record.get("transport")),
        account_id=_optional_str(record.get("account_id")),
        method=_optional_str(record.get("method")),
        url=_optional_str(record.get("url")),
        status_code=record.get("status_code") if isinstance(record.get("status_code"), int) else None,
        headers=_headers(record.get("headers")),
        payload=record.get("payload"),
        extra=record.get("extra") if isinstance(record.get("extra"), dict) else None,
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _headers(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items()}

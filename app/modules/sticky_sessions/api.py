from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardNotFoundError
from app.db.models import StickySessionKind
from app.dependencies import StickySessionsContext, get_sticky_sessions_context
from app.modules.sticky_sessions.schemas import (
    StickySessionDeleteFailure,
    StickySessionDeleteResponse,
    StickySessionEntryResponse,
    StickySessionIdentifier,
    StickySessionsDeleteFilteredRequest,
    StickySessionsDeleteFilteredResponse,
    StickySessionsDeleteRequest,
    StickySessionsDeleteResponse,
    StickySessionsListResponse,
    StickySessionSortBy,
    StickySessionSortDir,
    StickySessionsPurgeRequest,
    StickySessionsPurgeResponse,
)
from app.modules.upstream_identities.types import CHATGPT_WEB_PROVIDER_KIND, ProviderKind

router = APIRouter(
    prefix="/api/sticky-sessions",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=StickySessionsListResponse)
async def list_sticky_sessions(
    kind: StickySessionKind | None = Query(default=None),
    stale_only: bool = Query(default=False, alias="staleOnly"),
    provider_kind: ProviderKind | None = Query(default=None, alias="providerKind"),
    account_query: str | None = Query(default=None, alias="accountQuery"),
    key_query: str | None = Query(default=None, alias="keyQuery"),
    sort_by: StickySessionSortBy = Query(default="updated_at", alias="sortBy"),
    sort_dir: StickySessionSortDir = Query(default="desc", alias="sortDir"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    context: StickySessionsContext = Depends(get_sticky_sessions_context),
) -> StickySessionsListResponse:
    result = await context.service.list_entries(
        kind=kind,
        stale_only=stale_only,
        provider_kind=provider_kind,
        account_query=account_query,
        key_query=key_query,
        sort_by=sort_by,
        sort_dir=sort_dir,
        offset=offset,
        limit=limit,
    )
    return StickySessionsListResponse(
        entries=[
            StickySessionEntryResponse(
                key=entry.key,
                display_name=entry.display_name,
                kind=entry.kind,
                provider_kind=entry.provider_kind,
                routing_subject_id=entry.routing_subject_id,
                affinity_scope=entry.affinity_scope,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                expires_at=entry.expires_at,
                is_stale=entry.is_stale,
            )
            for entry in result.entries
        ],
        stale_prompt_cache_count=result.stale_prompt_cache_count,
        total=result.total,
        has_more=result.has_more,
    )


@router.post("/purge", response_model=StickySessionsPurgeResponse)
async def purge_sticky_sessions(
    payload: StickySessionsPurgeRequest = Body(default=StickySessionsPurgeRequest()),
    context: StickySessionsContext = Depends(get_sticky_sessions_context),
) -> StickySessionsPurgeResponse:
    deleted_count = await context.service.purge_entries()
    return StickySessionsPurgeResponse(deleted_count=deleted_count)


@router.post("/delete", response_model=StickySessionsDeleteResponse)
async def delete_sticky_sessions(
    payload: StickySessionsDeleteRequest,
    context: StickySessionsContext = Depends(get_sticky_sessions_context),
) -> StickySessionsDeleteResponse:
    result = await context.service.delete_entries(
        [(entry.key, entry.kind, entry.provider_kind) for entry in payload.sessions]
    )
    return StickySessionsDeleteResponse(
        deleted_count=result.deleted_count,
        deleted=[
            StickySessionIdentifier(key=key, kind=kind, provider_kind=provider_kind)
            for key, kind, provider_kind in result.deleted
        ],
        failed=[
            StickySessionDeleteFailure(
                key=entry.key,
                kind=entry.kind,
                provider_kind=entry.provider_kind,
                reason=entry.reason,
            )
            for entry in result.failed
        ],
    )


@router.post("/delete-filtered", response_model=StickySessionsDeleteFilteredResponse)
async def delete_filtered_sticky_sessions(
    payload: StickySessionsDeleteFilteredRequest,
    context: StickySessionsContext = Depends(get_sticky_sessions_context),
) -> StickySessionsDeleteFilteredResponse:
    deleted_count = await context.service.delete_filtered_entries(
        stale_only=payload.stale_only,
        provider_kind=payload.provider_kind,
        account_query=payload.account_query,
        key_query=payload.key_query,
    )
    return StickySessionsDeleteFilteredResponse(deleted_count=deleted_count)


@router.delete("/{kind}/{key:path}", response_model=StickySessionDeleteResponse)
async def delete_sticky_session(
    kind: StickySessionKind,
    key: str,
    provider_kind: ProviderKind = Query(default=CHATGPT_WEB_PROVIDER_KIND, alias="providerKind"),
    context: StickySessionsContext = Depends(get_sticky_sessions_context),
) -> StickySessionDeleteResponse:
    deleted = await context.service.delete_entry(key, kind=kind, provider_kind=provider_kind)
    if not deleted:
        raise DashboardNotFoundError("Sticky session not found", code="sticky_session_not_found")
    return StickySessionDeleteResponse(status="deleted")

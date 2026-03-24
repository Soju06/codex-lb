from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import StickySessionKind
from app.modules.proxy.sticky_repository import StickySessionListEntryRecord, StickySessionsRepository
from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class StickySessionEntryData:
    key: str
    display_name: str
    kind: StickySessionKind
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class StickySessionListData:
    entries: list[StickySessionEntryData]
    stale_prompt_cache_count: int
    total: int
    has_more: bool


class StickySessionsService:
    def __init__(
        self,
        repository: StickySessionsRepository,
        settings_repository: SettingsRepository,
    ) -> None:
        self._repository = repository
        self._settings_repository = settings_repository

    async def list_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        stale_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> StickySessionListData:
        settings = await self._settings_repository.get_or_create()
        ttl_seconds = settings.openai_cache_affinity_max_age_seconds
        stale_cutoff = utcnow() - timedelta(seconds=ttl_seconds)
        stale_prompt_cache_count = await self._count_stale_prompt_cache_entries(kind=kind, stale_cutoff=stale_cutoff)
        if stale_only and kind not in (None, StickySessionKind.PROMPT_CACHE):
            return StickySessionListData(
                entries=[],
                stale_prompt_cache_count=stale_prompt_cache_count,
                total=0,
                has_more=False,
            )
        effective_kind = StickySessionKind.PROMPT_CACHE if stale_only else kind
        total = await self._repository.count_entries(
            kind=effective_kind,
            updated_before=stale_cutoff if stale_only else None,
        )
        rows = await self._repository.list_entries(
            kind=effective_kind,
            updated_before=stale_cutoff if stale_only else None,
            offset=offset,
            limit=limit,
        )
        entries = [self._to_entry(row, ttl_seconds=ttl_seconds) for row in rows]
        return StickySessionListData(
            entries=entries,
            stale_prompt_cache_count=stale_prompt_cache_count,
            total=total,
            has_more=offset + len(entries) < total,
        )

    async def delete_entry(self, key: str, *, kind: StickySessionKind) -> bool:
        return await self._repository.delete(key, kind=kind)

    async def purge_entries(self) -> int:
        settings = await self._settings_repository.get_or_create()
        cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
        return await self._repository.purge_prompt_cache_before(cutoff)

    def _to_entry(self, row: StickySessionListEntryRecord, *, ttl_seconds: int) -> StickySessionEntryData:
        sticky_session = row.sticky_session
        expires_at: datetime | None = None
        is_stale = False
        if sticky_session.kind == StickySessionKind.PROMPT_CACHE:
            expires_at = to_utc_naive(sticky_session.updated_at) + timedelta(seconds=ttl_seconds)
            is_stale = expires_at <= utcnow()
        return StickySessionEntryData(
            key=sticky_session.key,
            display_name=row.display_name,
            kind=sticky_session.kind,
            created_at=sticky_session.created_at,
            updated_at=sticky_session.updated_at,
            expires_at=expires_at,
            is_stale=is_stale,
        )

    async def _count_stale_prompt_cache_entries(
        self,
        *,
        kind: StickySessionKind | None,
        stale_cutoff: datetime,
    ) -> int:
        if kind not in (None, StickySessionKind.PROMPT_CACHE):
            return 0
        return await self._repository.count_entries(
            kind=StickySessionKind.PROMPT_CACHE,
            updated_before=stale_cutoff,
        )

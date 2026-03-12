from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import StickySession, StickySessionKind
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class StickySessionEntryData:
    key: str
    account_id: str
    kind: StickySessionKind
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class StickySessionListData:
    entries: list[StickySessionEntryData]
    stale_prompt_cache_count: int


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
        limit: int = 100,
    ) -> StickySessionListData:
        settings = await self._settings_repository.get_or_create()
        ttl_seconds = settings.openai_cache_affinity_max_age_seconds
        stale_cutoff = utcnow() - timedelta(seconds=ttl_seconds)
        stale_prompt_cache_count = await self._count_stale_prompt_cache_entries(kind=kind, stale_cutoff=stale_cutoff)
        if stale_only and kind not in (None, StickySessionKind.PROMPT_CACHE):
            return StickySessionListData(entries=[], stale_prompt_cache_count=stale_prompt_cache_count)
        effective_kind = StickySessionKind.PROMPT_CACHE if stale_only else kind
        rows = await self._repository.list_entries(
            kind=effective_kind,
            updated_before=stale_cutoff if stale_only else None,
            limit=limit,
        )
        entries = [self._to_entry(row, ttl_seconds=ttl_seconds) for row in rows]
        return StickySessionListData(entries=entries, stale_prompt_cache_count=stale_prompt_cache_count)

    async def delete_entry(self, key: str, *, kind: StickySessionKind) -> bool:
        return await self._repository.delete(key, kind=kind)

    async def purge_entries(self) -> int:
        settings = await self._settings_repository.get_or_create()
        cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
        return await self._repository.purge_prompt_cache_before(cutoff)

    def _to_entry(self, row: StickySession, *, ttl_seconds: int) -> StickySessionEntryData:
        expires_at: datetime | None = None
        is_stale = False
        if row.kind == StickySessionKind.PROMPT_CACHE:
            expires_at = to_utc_naive(row.updated_at) + timedelta(seconds=ttl_seconds)
            is_stale = expires_at <= utcnow()
        return StickySessionEntryData(
            key=row.key,
            account_id=row.account_id,
            kind=row.kind,
            created_at=row.created_at,
            updated_at=row.updated_at,
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

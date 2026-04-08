from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import StickySessionKind
from app.modules.proxy.sticky_repository import StickySessionListEntryRecord, StickySessionsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.sticky_sessions.schemas import (
    StickySessionAffinityScope,
    StickySessionSortBy,
    StickySessionSortDir,
)
from app.modules.upstream_identities.types import (
    CHATGPT_WEB_PROVIDER_KIND,
    OPENAI_PLATFORM_PROVIDER_KIND,
    ProviderKind,
)


@dataclass(frozen=True, slots=True)
class StickySessionEntryData:
    key: str
    display_name: str
    kind: StickySessionKind
    provider_kind: ProviderKind
    routing_subject_id: str
    affinity_scope: StickySessionAffinityScope
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


@dataclass(frozen=True, slots=True)
class StickySessionDeleteFailureData:
    key: str
    kind: StickySessionKind
    provider_kind: ProviderKind
    reason: str


@dataclass(frozen=True, slots=True)
class StickySessionsDeleteData:
    deleted: list[tuple[str, StickySessionKind, ProviderKind]]
    failed: list[StickySessionDeleteFailureData]

    @property
    def deleted_count(self) -> int:
        return len(self.deleted)


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
        provider_kind: ProviderKind | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        sort_by: StickySessionSortBy = "updated_at",
        sort_dir: StickySessionSortDir = "desc",
        offset: int = 0,
        limit: int = 100,
    ) -> StickySessionListData:
        settings = await self._settings_repository.get_or_create()
        ttl_seconds = settings.openai_cache_affinity_max_age_seconds
        stale_cutoff = utcnow() - timedelta(seconds=ttl_seconds)
        normalized_account_query = account_query.strip() if account_query else None
        normalized_key_query = key_query.strip() if key_query else None
        normalized_provider_kind = _normalize_provider_kind(provider_kind)
        stale_prompt_cache_count = await self._count_stale_prompt_cache_entries(
            kind=kind,
            stale_cutoff=stale_cutoff,
            provider_kind=normalized_provider_kind,
        )
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
            account_query=normalized_account_query,
            key_query=normalized_key_query,
            provider_kind=normalized_provider_kind,
        )
        rows = await self._repository.list_entries(
            kind=effective_kind,
            updated_before=stale_cutoff if stale_only else None,
            account_query=normalized_account_query,
            key_query=normalized_key_query,
            provider_kind=normalized_provider_kind,
            sort_by=sort_by,
            sort_dir=sort_dir,
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

    async def delete_entry(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        provider_kind: ProviderKind = CHATGPT_WEB_PROVIDER_KIND,
    ) -> bool:
        return await self._repository.delete_scoped(key, kind=kind, provider_kind=provider_kind)

    async def delete_entries(
        self,
        entries: Sequence[tuple[str, StickySessionKind, ProviderKind]],
    ) -> StickySessionsDeleteData:
        failed: list[StickySessionDeleteFailureData] = []
        seen: set[tuple[str, StickySessionKind, ProviderKind]] = set()
        targets: list[tuple[str, StickySessionKind, ProviderKind]] = []

        for key, kind, provider_kind in entries:
            if not key:
                continue
            target = (key, kind, provider_kind)
            if target in seen:
                continue
            seen.add(target)
            targets.append(target)

        deleted_rows = await self._repository.delete_entries_scoped(targets)
        deleted = cast(
            "list[tuple[str, StickySessionKind, ProviderKind]]",
            [(key, kind, _coerce_provider_kind(provider_kind)) for key, kind, provider_kind in deleted_rows],
        )
        deleted_set = set(deleted)

        for key, kind, provider_kind in targets:
            if (key, kind, provider_kind) not in deleted_set:
                failed.append(
                    StickySessionDeleteFailureData(
                        key=key,
                        kind=kind,
                        provider_kind=provider_kind,
                        reason="not_found",
                    )
                )

        return StickySessionsDeleteData(deleted=deleted, failed=failed)

    async def delete_filtered_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        stale_only: bool = False,
        provider_kind: ProviderKind | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
    ) -> int:
        settings = await self._settings_repository.get_or_create()
        stale_cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
        if stale_only and kind not in (None, StickySessionKind.PROMPT_CACHE):
            return 0
        effective_kind = StickySessionKind.PROMPT_CACHE if stale_only else kind
        normalized_account_query = account_query.strip() if account_query else None
        normalized_key_query = key_query.strip() if key_query else None
        normalized_provider_kind = _normalize_provider_kind(provider_kind)
        targets = await self._repository.list_scoped_entry_identifiers(
            kind=effective_kind,
            updated_before=stale_cutoff if stale_only else None,
            account_query=normalized_account_query,
            key_query=normalized_key_query,
            provider_kind=normalized_provider_kind,
        )
        deleted = await self._repository.delete_entries_scoped(targets)
        return len(deleted)

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
        provider_kind = _coerce_provider_kind(sticky_session.provider_kind)
        return StickySessionEntryData(
            key=sticky_session.key,
            display_name=row.display_name,
            kind=sticky_session.kind,
            provider_kind=provider_kind,
            routing_subject_id=sticky_session.routing_subject_id,
            affinity_scope=self._affinity_scope(provider_kind, sticky_session.kind),
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
        provider_kind: ProviderKind | None,
    ) -> int:
        if kind not in (None, StickySessionKind.PROMPT_CACHE):
            return 0
        return await self._repository.count_entries(
            kind=StickySessionKind.PROMPT_CACHE,
            updated_before=stale_cutoff,
            provider_kind=provider_kind,
        )

    @staticmethod
    def _affinity_scope(provider_kind: ProviderKind, kind: StickySessionKind) -> StickySessionAffinityScope:
        if kind == StickySessionKind.PROMPT_CACHE:
            return "provider_prompt_cache"
        if provider_kind == CHATGPT_WEB_PROVIDER_KIND:
            return "chatgpt_continuity"
        return "provider_scoped"


def _normalize_provider_kind(provider_kind: ProviderKind | None) -> ProviderKind | None:
    if provider_kind is None:
        return None
    return _coerce_provider_kind(provider_kind)


def _coerce_provider_kind(provider_kind: str) -> ProviderKind:
    if provider_kind == OPENAI_PLATFORM_PROVIDER_KIND:
        return OPENAI_PLATFORM_PROVIDER_KIND
    if provider_kind == CHATGPT_WEB_PROVIDER_KIND:
        return CHATGPT_WEB_PROVIDER_KIND
    return cast("ProviderKind", provider_kind)

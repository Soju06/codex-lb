"""Scoped reset-credit invalidation using the existing namespace wake-up bus."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.cache.invalidation import NAMESPACE_RESET_CREDITS, bump_cache_invalidation
from app.db.models import CacheInvalidation, ResetCreditSnapshotRevision
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

logger = logging.getLogger(__name__)


async def publish_reset_credit_invalidation(account_id: str) -> int | None:
    for attempt in range(3):
        try:
            async with SessionLocal() as session:
                insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
                await session.execute(
                    insert(CacheInvalidation)
                    .values(namespace=NAMESPACE_RESET_CREDITS, version=1)
                    .on_conflict_do_update(
                        index_elements=[CacheInvalidation.namespace], set_={"version": CacheInvalidation.version + 1}
                    )
                )
                revision = await session.scalar(
                    insert(ResetCreditSnapshotRevision)
                    .values(account_id=account_id, revision=1)
                    .on_conflict_do_update(
                        index_elements=[ResetCreditSnapshotRevision.account_id],
                        set_={"revision": ResetCreditSnapshotRevision.revision + 1},
                    )
                    .returning(ResetCreditSnapshotRevision.revision)
                )
                await session.commit()
            return revision
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))
    logger.warning("Scoped reset-credit invalidation failed account_id=%s; using legacy wake-up", account_id)
    await bump_cache_invalidation(NAMESPACE_RESET_CREDITS)


@dataclass(slots=True)
class ResetCreditInvalidationTracker:
    store: RateLimitResetCreditsStore
    _version: int = 0
    _revisions: dict[str, int] = field(default_factory=dict)
    _initialized: bool = False

    async def initialize(self) -> None:
        self._version, self._revisions = await self._read()
        for account_id, revision in self._revisions.items():
            await self.store.acknowledge_revision(account_id, revision)
        self._initialized = True

    async def reconcile(self) -> None:
        version, revisions = await self._read()
        changed = {key for key, revision in revisions.items() if self._revisions.get(key, 0) != revision}
        removed = self._revisions.keys() - revisions.keys()
        increments = sum(revisions[key] - self._revisions.get(key, 0) for key in changed)
        # A single SELECT reads a consistent version/revisions snapshot. Any
        # unaccounted increment is an old writer or missing revision history.
        if not self._initialized or increments != version - self._version or removed:
            await self.store.invalidate()
        else:
            for account_id in changed:
                await self.store.invalidate_revision(account_id, revisions[account_id])
        self._version, self._revisions = version, revisions
        self._initialized = True

    @staticmethod
    async def _read() -> tuple[int, dict[str, int]]:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        CacheInvalidation.version,
                        ResetCreditSnapshotRevision.account_id,
                        ResetCreditSnapshotRevision.revision,
                    )
                    .select_from(CacheInvalidation)
                    .outerjoin(ResetCreditSnapshotRevision, true())
                    .where(CacheInvalidation.namespace == NAMESPACE_RESET_CREDITS)
                )
            ).all()
        return (rows[0][0] if rows else 0, {key: revision for _, key, revision in rows if key is not None})


async def reconcile_account_revision(account_id: str, store: RateLimitResetCreditsStore) -> None:
    async with SessionLocal() as session:
        revision = await session.scalar(
            select(ResetCreditSnapshotRevision.revision).where(
                ResetCreditSnapshotRevision.account_id == account_id,
            )
        )
    if revision is not None:
        await store.invalidate_revision(account_id, revision)

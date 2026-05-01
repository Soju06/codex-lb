from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PeerFallbackTarget


class PeerFallbackTargetConflictError(ValueError):
    pass


class PeerFallbackTargetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_targets(self) -> Sequence[PeerFallbackTarget]:
        result = await self._session.execute(
            select(PeerFallbackTarget).order_by(PeerFallbackTarget.created_at, PeerFallbackTarget.base_url)
        )
        return list(result.scalars().all())

    async def list_enabled_base_urls(self) -> list[str]:
        result = await self._session.execute(
            select(PeerFallbackTarget.base_url)
            .where(PeerFallbackTarget.enabled.is_(True))
            .order_by(PeerFallbackTarget.created_at, PeerFallbackTarget.base_url)
        )
        return [row[0] for row in result.all()]

    async def list_runtime_base_urls(self) -> list[str] | None:
        result = await self._session.execute(
            select(PeerFallbackTarget.base_url, PeerFallbackTarget.enabled).order_by(
                PeerFallbackTarget.created_at,
                PeerFallbackTarget.base_url,
            )
        )
        rows = result.all()
        if not rows:
            return None
        return [base_url for base_url, enabled in rows if enabled]

    async def get(self, target_id: str) -> PeerFallbackTarget | None:
        return await self._session.get(PeerFallbackTarget, target_id)

    async def create(self, *, base_url: str, enabled: bool) -> PeerFallbackTarget:
        row = PeerFallbackTarget(base_url=base_url, enabled=enabled)
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PeerFallbackTargetConflictError("Peer fallback target already exists") from exc
        await self._session.refresh(row)
        return row

    async def update(
        self,
        target: PeerFallbackTarget,
        *,
        base_url: str | None = None,
        enabled: bool | None = None,
    ) -> PeerFallbackTarget:
        if base_url is not None:
            target.base_url = base_url
        if enabled is not None:
            target.enabled = enabled
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PeerFallbackTargetConflictError("Peer fallback target already exists") from exc
        await self._session.refresh(target)
        return target

    async def delete(self, target: PeerFallbackTarget) -> None:
        await self._session.delete(target)
        await self._session.commit()

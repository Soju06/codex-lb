from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelSourceOwnership, ModelSourceOwnershipHistory, RequestLog


class SourceOwnershipConflict(RuntimeError):
    pass


class SourceOwnershipRepository:
    """Atomic ownership claims. The caller owns the transaction and writer section."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, keys: Sequence[str], *, now: datetime) -> dict[str, ModelSourceOwnership]:
        if not keys:
            return {}
        rows = await self._session.scalars(
            select(ModelSourceOwnership).where(
                ModelSourceOwnership.reference_key.in_(keys), ModelSourceOwnership.expires_at > now
            )
        )
        return {row.reference_key: row for row in rows}

    async def find_history(self, keys: Sequence[str]) -> dict[str, list[ModelSourceOwnershipHistory]]:
        """Return all durable owners, including expired/pruned live references."""
        if not keys:
            return {}
        rows = await self._session.scalars(
            select(ModelSourceOwnershipHistory).where(ModelSourceOwnershipHistory.reference_key.in_(keys))
        )
        history: dict[str, list[ModelSourceOwnershipHistory]] = {}
        for row in rows:
            history.setdefault(row.reference_key, []).append(row)
        return history

    async def claim(
        self,
        keys: Sequence[str],
        *,
        source_id: str,
        source_revision: str,
        expires_at: datetime,
        legacy_response_ids: Mapping[str, str] | None = None,
        api_key_id: str | None = None,
        model: str | None = None,
        reconcile_legacy: bool = True,
    ) -> None:
        if not keys and not legacy_response_ids:
            return
        history_rows = await self.find_history(keys)
        live_keys = (
            set(
                await self._session.scalars(
                    select(ModelSourceOwnership.reference_key).where(ModelSourceOwnership.reference_key.in_(keys))
                )
            )
            if keys
            else set()
        )
        authoritative_keys = set(history_rows) | live_keys
        response_ids_without_evidence = [
            response_id
            for reference_key, response_id in (legacy_response_ids or {}).items()
            if reference_key not in authoritative_keys
        ]
        if response_ids_without_evidence and reconcile_legacy:
            if model is None:
                raise ValueError("model is required when reconciling legacy response ownership")
            legacy_owners = await self._session.execute(
                select(RequestLog.model_source_id, RequestLog.model_source_revision)
                .where(
                    RequestLog.request_id.in_(response_ids_without_evidence),
                    RequestLog.api_key_id == api_key_id,
                    RequestLog.model == model,
                    RequestLog.model_source_id.is_not(None),
                    ~and_(
                        RequestLog.status == "error",
                        RequestLog.error_code.is_not_distinct_from("model_source_ownership_unavailable"),
                    ),
                )
                .distinct()
            )
            if any(
                owner != source_id or revision != source_revision
                for owner, revision in legacy_owners.all()
                if owner is not None
            ):
                raise SourceOwnershipConflict("Conflicting legacy source ownership")

        if any(
            row.source_id != source_id or row.source_revision != source_revision
            for rows in history_rows.values()
            for row in rows
        ):
            raise SourceOwnershipConflict("Conflicting historical source ownership")

        insert = pg_insert if self._session.get_bind().dialect.name == "postgresql" else sqlite_insert
        if keys:
            history_stmt = insert(ModelSourceOwnershipHistory).values(
                [dict(reference_key=key, source_id=source_id, source_revision=source_revision) for key in keys]
            )
            history_stmt = history_stmt.on_conflict_do_nothing(
                index_elements=[
                    ModelSourceOwnershipHistory.reference_key,
                    ModelSourceOwnershipHistory.source_id,
                    ModelSourceOwnershipHistory.source_revision,
                ]
            )
            await self._session.execute(history_stmt)
        if not keys:
            return
        stmt = insert(ModelSourceOwnership).values(
            [
                dict(reference_key=key, source_id=source_id, source_revision=source_revision, expires_at=expires_at)
                for key in keys
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ModelSourceOwnership.reference_key],
            set_={"expires_at": expires_at},
            where=and_(
                ModelSourceOwnership.source_id == source_id,
                ModelSourceOwnership.source_revision == source_revision,
            ),
        ).returning(ModelSourceOwnership.reference_key)
        claimed = set((await self._session.scalars(stmt)).all())
        if claimed != set(keys):
            raise SourceOwnershipConflict("Conflicting source ownership")

    async def prune(self, *, now: datetime, batch_size: int) -> int:
        keys = (
            select(ModelSourceOwnership.reference_key).where(ModelSourceOwnership.expires_at <= now).limit(batch_size)
        )
        result = await self._session.scalars(
            delete(ModelSourceOwnership)
            .where(ModelSourceOwnership.reference_key.in_(keys), ModelSourceOwnership.expires_at <= now)
            .returning(ModelSourceOwnership.reference_key)
        )
        return len(result.all())

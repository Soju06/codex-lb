from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert, func

from app.core.utils.time import to_utc_naive
from app.db.models import HttpBridgeLease


class HttpBridgeLeasesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_session_id(self, session_id: str) -> HttpBridgeLease | None:
        if not session_id:
            return None
        statement = select(HttpBridgeLease).where(HttpBridgeLease.session_id == session_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        session_id: str,
        affinity_kind: str,
        affinity_key: str,
        api_key_scope: str,
        owner_instance_id: str,
        lease_expires_at: datetime,
        account_id: str | None,
        request_model: str | None,
        codex_session: bool,
        idle_ttl_seconds: float,
        upstream_turn_state: str | None,
        downstream_turn_state: str | None,
    ) -> HttpBridgeLease:
        statement = self._build_upsert_statement(
            session_id=session_id,
            affinity_kind=affinity_kind,
            affinity_key=affinity_key,
            api_key_scope=api_key_scope,
            owner_instance_id=owner_instance_id,
            lease_expires_at=lease_expires_at,
            account_id=account_id,
            request_model=request_model,
            codex_session=codex_session,
            idle_ttl_seconds=idle_ttl_seconds,
            upstream_turn_state=upstream_turn_state,
            downstream_turn_state=downstream_turn_state,
        )
        await self._session.execute(statement)
        await self._session.commit()
        row = await self.get_by_session_id(session_id)
        if row is None:
            raise RuntimeError(f"HttpBridgeLease upsert failed for session_id={session_id!r}")
        await self._session.refresh(row)
        return row

    async def delete(self, session_id: str) -> bool:
        if not session_id:
            return False
        statement = delete(HttpBridgeLease).where(HttpBridgeLease.session_id == session_id)
        result = await self._session.execute(statement.returning(HttpBridgeLease.session_id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def touch(
        self,
        session_id: str,
        *,
        affinity_kind: str,
        affinity_key: str,
        api_key_scope: str,
        owner_instance_id: str,
        lease_expires_at: datetime,
        account_id: str | None,
        request_model: str | None,
        codex_session: bool,
        idle_ttl_seconds: float,
        upstream_turn_state: str | None,
        downstream_turn_state: str | None,
    ) -> bool:
        if not session_id:
            return False
        statement = (
            update(HttpBridgeLease)
            .where(HttpBridgeLease.session_id == session_id)
            .values(
                affinity_kind=affinity_kind,
                affinity_key=affinity_key,
                api_key_scope=api_key_scope,
                owner_instance_id=owner_instance_id,
                lease_expires_at=to_utc_naive(lease_expires_at),
                account_id=account_id,
                request_model=request_model,
                codex_session=codex_session,
                idle_ttl_seconds=idle_ttl_seconds,
                upstream_turn_state=upstream_turn_state,
                downstream_turn_state=downstream_turn_state,
                updated_at=func.now(),
            )
            .returning(HttpBridgeLease.session_id)
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def purge_expired(self, *, expires_before: datetime) -> int:
        statement = delete(HttpBridgeLease).where(HttpBridgeLease.lease_expires_at < to_utc_naive(expires_before))
        result = await self._session.execute(statement.returning(HttpBridgeLease.session_id))
        deleted = len(result.scalars().all())
        await self._session.commit()
        return deleted

    def _build_upsert_statement(
        self,
        *,
        session_id: str,
        affinity_kind: str,
        affinity_key: str,
        api_key_scope: str,
        owner_instance_id: str,
        lease_expires_at: datetime,
        account_id: str | None,
        request_model: str | None,
        codex_session: bool,
        idle_ttl_seconds: float,
        upstream_turn_state: str | None,
        downstream_turn_state: str | None,
    ) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"HttpBridgeLease upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(HttpBridgeLease).values(
            session_id=session_id,
            affinity_kind=affinity_kind,
            affinity_key=affinity_key,
            api_key_scope=api_key_scope,
            owner_instance_id=owner_instance_id,
            lease_expires_at=to_utc_naive(lease_expires_at),
            account_id=account_id,
            request_model=request_model,
            codex_session=codex_session,
            idle_ttl_seconds=idle_ttl_seconds,
            upstream_turn_state=upstream_turn_state,
            downstream_turn_state=downstream_turn_state,
        )
        return statement.on_conflict_do_update(
            index_elements=[HttpBridgeLease.session_id],
            set_={
                "affinity_kind": affinity_kind,
                "affinity_key": affinity_key,
                "api_key_scope": api_key_scope,
                "owner_instance_id": owner_instance_id,
                "lease_expires_at": to_utc_naive(lease_expires_at),
                "account_id": account_id,
                "request_model": request_model,
                "codex_session": codex_session,
                "idle_ttl_seconds": idle_ttl_seconds,
                "upstream_turn_state": upstream_turn_state,
                "downstream_turn_state": downstream_turn_state,
                "updated_at": func.now(),
            },
        )

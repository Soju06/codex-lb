from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert, func

from app.core.types import JsonValue
from app.db.models import ResponseSnapshot


class ResponseSnapshotsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, response_id: str) -> ResponseSnapshot | None:
        if not response_id:
            return None
        result = await self._session.execute(
            select(ResponseSnapshot).where(ResponseSnapshot.response_id == response_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        response_id: str,
        parent_response_id: str | None,
        account_id: str | None,
        model: str,
        input_items: list[JsonValue],
        response_payload: dict[str, JsonValue],
    ) -> ResponseSnapshot:
        statement = self._build_upsert_statement(
            response_id=response_id,
            parent_response_id=parent_response_id,
            account_id=account_id,
            model=model,
            input_items_json=json.dumps(input_items, ensure_ascii=False, separators=(",", ":")),
            response_json=json.dumps(response_payload, ensure_ascii=False, separators=(",", ":")),
        )
        await self._session.execute(statement)
        await self._session.commit()
        snapshot = await self.get(response_id)
        if snapshot is None:
            raise RuntimeError(f"ResponseSnapshot upsert failed for response_id={response_id!r}")
        await self._session.refresh(snapshot)
        return snapshot

    def _build_upsert_statement(
        self,
        *,
        response_id: str,
        parent_response_id: str | None,
        account_id: str | None,
        model: str,
        input_items_json: str,
        response_json: str,
    ) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"ResponseSnapshot upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(ResponseSnapshot).values(
            response_id=response_id,
            parent_response_id=parent_response_id,
            account_id=account_id,
            model=model,
            input_items_json=input_items_json,
            response_json=response_json,
        )
        return statement.on_conflict_do_update(
            index_elements=[ResponseSnapshot.response_id],
            set_={
                "parent_response_id": parent_response_id,
                "account_id": account_id,
                "model": model,
                "input_items_json": input_items_json,
                "response_json": response_json,
                "created_at": func.now(),
            },
        )

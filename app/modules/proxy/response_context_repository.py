from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.core.utils.time import utcnow
from app.db.models import ResponseContext, ResponseContextItem
from app.core.config.settings import get_settings


class ResponseContextRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store_response(
        self,
        *,
        response_payload: dict[str, JsonValue],
        api_key_id: str | None,
        expires_at: datetime,
    ) -> bool:
        response_id = response_payload.get("id")
        output = response_payload.get("output")
        if not isinstance(response_id, str) or not response_id or not is_json_list(output):
            return False

        output_json = json.dumps(output, ensure_ascii=False, separators=(",", ":"))

        existing = await self._session.get(ResponseContext, response_id)
        if existing is None:
            row = ResponseContext(
                response_id=response_id,
                api_key_id=api_key_id,
                output_json=output_json,
                expires_at=expires_at,
            )
            self._session.add(row)
        else:
            existing.api_key_id = api_key_id
            existing.output_json = output_json
            existing.expires_at = expires_at

        # Replace items for this response to keep durable references in sync.
        await self._session.execute(delete(ResponseContextItem).where(ResponseContextItem.response_id == response_id))

        for item in output:
            if not is_json_mapping(item):
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                continue
            self._session.add(
                ResponseContextItem(
                    item_id=item_id,
                    response_id=response_id,
                    api_key_id=api_key_id,
                    item_json=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    expires_at=expires_at,
                )
            )

        await self._session.commit()
        return True

    async def resolve_reference(self, *, reference_id: str, api_key_id: str | None) -> list[JsonValue] | None:
        now = utcnow()
        row = await self._resolve_response(reference_id=reference_id, api_key_id=api_key_id, now=now)
        if row is not None:
            return _parse_json_list(row.output_json)

        item_row = await self._resolve_item(reference_id=reference_id, api_key_id=api_key_id, now=now)
        if item_row is not None:
            item = _parse_json_mapping(item_row.item_json)
            if item is not None:
                return [item]

        return None

    async def delete_expired(self, *, now: datetime | None = None) -> tuple[int, int]:
        cutoff = now or utcnow()
        stale_items = await self._session.execute(
            delete(ResponseContextItem).where(ResponseContextItem.expires_at <= cutoff)
        )
        stale_responses = await self._session.execute(
            delete(ResponseContext).where(ResponseContext.expires_at <= cutoff)
        )
        await self._session.commit()
        return int(stale_responses.rowcount or 0), int(stale_items.rowcount or 0)

    async def _resolve_response(
        self,
        *,
        reference_id: str,
        api_key_id: str | None,
        now: datetime,
    ) -> ResponseContext | None:
        stmt = (
            select(ResponseContext)
            .where(ResponseContext.response_id == reference_id)
            .where(ResponseContext.expires_at > now)
            .where(_scope_clause(ResponseContext.api_key_id, api_key_id))
            .order_by(ResponseContext.api_key_id.is_(None))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_item(
        self,
        *,
        reference_id: str,
        api_key_id: str | None,
        now: datetime,
    ) -> ResponseContextItem | None:
        stmt = (
            select(ResponseContextItem)
            .where(ResponseContextItem.item_id == reference_id)
            .where(ResponseContextItem.expires_at > now)
            .where(_scope_clause(ResponseContextItem.api_key_id, api_key_id))
            .order_by(ResponseContextItem.api_key_id.is_(None))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


def _scope_clause(column, api_key_id: str | None):
    settings = get_settings()
    if settings.response_context_global_fallback_enabled:
        if api_key_id is None:
            return column.is_(None)
        return or_(column == api_key_id, column.is_(None))
    if api_key_id is None:
        return column.is_(None)
    return column == api_key_id


def _parse_json_list(raw: str) -> list[JsonValue] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if is_json_list(value) else None


def _parse_json_mapping(raw: str) -> dict[str, JsonValue] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if is_json_mapping(value) else None

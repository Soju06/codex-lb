from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesktopResetCreditRedemption


@dataclass(frozen=True)
class RedemptionPin:
    owner_id: str
    credit_id: str
    owner_chatgpt_account_id: str


class RedemptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, caller: str, request_id: str) -> RedemptionPin | None:
        row = await self._session.scalar(
            select(DesktopResetCreditRedemption).where(
                DesktopResetCreditRedemption.caller_account_id == caller,
                DesktopResetCreditRedemption.redeem_request_id == request_id,
            )
        )
        return (
            RedemptionPin(row.owner_account_id, row.credit_id, row.owner_chatgpt_account_id)
            if row is not None
            else None
        )

    async def pin(self, caller: str, request_id: str, selected: RedemptionPin) -> RedemptionPin:
        insert = pg_insert if self._session.get_bind().dialect.name == "postgresql" else sqlite_insert
        await self._session.execute(
            insert(DesktopResetCreditRedemption)
            .values(
                caller_account_id=caller,
                redeem_request_id=request_id,
                owner_account_id=selected.owner_id,
                credit_id=selected.credit_id,
                owner_chatgpt_account_id=selected.owner_chatgpt_account_id,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["caller_account_id", "redeem_request_id"])
        )
        await self._session.commit()
        pinned = await self.get(caller, request_id)
        if pinned is None:
            raise RuntimeError("Reset credit selection was not persisted")
        return pinned

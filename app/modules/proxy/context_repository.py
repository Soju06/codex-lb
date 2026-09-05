from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodexContextParticipant, CodexContextSession
from app.modules.proxy.context_codec import context_error


class ContextRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, session_id: str, api_key_id: str) -> CodexContextSession | None:
        row = await self._session.get(CodexContextSession, session_id)
        if row is not None and row.api_key_id != api_key_id:
            raise context_error("context_scope_mismatch", 403)
        return row

    async def bind(self, session_id: str, api_key_id: str, owner_account_id: str) -> CodexContextSession:
        insert = sqlite_insert if self._session.get_bind().dialect.name == "sqlite" else pg_insert
        await self._session.execute(
            insert(CodexContextSession)
            .values(
                session_id=session_id,
                api_key_id=api_key_id,
                owner_account_id=owner_account_id,
            )
            .on_conflict_do_nothing(index_elements=["session_id"])
        )
        row = await self.get(session_id, api_key_id)
        assert row is not None
        return row

    async def record(self, session_id: str, api_key_id: str, account_id: str) -> None:
        await self.bind(session_id, api_key_id, account_id)
        insert = sqlite_insert if self._session.get_bind().dialect.name == "sqlite" else pg_insert
        await self._session.execute(
            insert(CodexContextParticipant)
            .values(
                session_id=session_id,
                account_id=account_id,
            )
            .on_conflict_do_nothing(index_elements=["session_id", "account_id"])
        )

    async def participants(self, session_id: str) -> list[str]:
        return list(
            (
                await self._session.scalars(
                    select(CodexContextParticipant.account_id)
                    .where(
                        CodexContextParticipant.session_id == session_id,
                    )
                    .order_by(CodexContextParticipant.account_id)
                )
            ).all()
        )

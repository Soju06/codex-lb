from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import DEFAULT_PLAN
from app.core.plan_types import coerce_account_plan_type


async def run(session: AsyncSession) -> None:
    # Avoid ORM model selection in migrations: models may already include
    # columns that are not present in older databases at this migration step.
    rows = await session.execute(text("SELECT id, plan_type FROM accounts"))
    for row in rows:
        account_id = row[0]
        plan_type = row[1]
        coerced = coerce_account_plan_type(plan_type, DEFAULT_PLAN)
        if plan_type != coerced:
            await session.execute(
                text("UPDATE accounts SET plan_type = :plan_type WHERE id = :account_id"),
                {"plan_type": coerced, "account_id": account_id},
            )

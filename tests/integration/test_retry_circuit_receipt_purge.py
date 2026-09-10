from __future__ import annotations

from typing import Any, Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.sql.selectable import Select

from app.db.models import HttpBridgeRetryCircuit
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, durable_bridge_hash

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_reset_db_state")]


@pytest.mark.parametrize("changed_field", ["generation", "start", "expiry"])
async def test_scheduled_cleanup_does_not_recapture_an_expired_receipt(
    monkeypatch: pytest.MonkeyPatch,
    changed_field: Literal["generation", "start", "expiry"],
) -> None:
    async with SessionLocal() as seed:
        for key in ("racing-receipt", "unchanged-receipt"):
            seed.add(
                HttpBridgeRetryCircuit(
                    session_key_kind="session_header",
                    session_key_hash=durable_bridge_hash(key),
                    api_key_scope="key-1",
                    consecutive_failures=2,
                    cooldown_until_epoch=0.0,
                    last_detail="stream_incomplete",
                    updated_at_epoch=1000.0,
                    admission_generation=5,
                    admission_claimed_generation=5,
                    admission_claimed_at_epoch=10.0,
                    admission_claimed_until_epoch=20.0,
                )
            )
        await seed.commit()

    changed = False
    async with SessionLocal() as cleanup:
        execute = cleanup.execute

        async def replace_receipt_after_selection(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal changed
            result = await execute(statement, *args, **kwargs)
            if not changed and isinstance(statement, Select):
                changed = True
                replacement = update(HttpBridgeRetryCircuit).where(
                    HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash("racing-receipt")
                )
                if changed_field == "generation":
                    replacement = replacement.values(admission_generation=6, admission_claimed_generation=6)
                elif changed_field == "start":
                    replacement = replacement.values(admission_claimed_at_epoch=11.0)
                else:
                    replacement = replacement.values(admission_claimed_until_epoch=21.0)
                async with SessionLocal() as writer:
                    await writer.execute(replacement)
                    await writer.commit()
            return result

        monkeypatch.setattr(cleanup, "execute", replace_receipt_after_selection)
        deleted = await DurableBridgeRepository(cleanup).purge_retry_circuits_before(100000.0, batch_size=2)
    assert changed
    assert deleted == 1
    async with SessionLocal() as observer:
        rows = (await observer.execute(select(HttpBridgeRetryCircuit))).scalars().all()
        assert len(rows) == 1
        assert rows[0].session_key_hash == durable_bridge_hash("racing-receipt")

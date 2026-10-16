from __future__ import annotations

from typing import Any, Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.sql.selectable import Select

from app.db.models import HttpBridgeRetryCircuit
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, durable_bridge_hash

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("_reset_db_state")]


@pytest.mark.parametrize("mutation", ["claim", "failure", "timestamp"])
@pytest.mark.parametrize("tombstone", [False, True])
async def test_scheduled_purge_preserves_a_row_changed_after_selection(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Literal["claim", "failure", "timestamp"],
    tombstone: bool,
) -> None:
    cutoff = 100000.0
    observed_at = 1000.0
    detail = "anchor_abandoned" if tombstone else "stream_incomplete"
    async with SessionLocal() as seed:
        for key in ("racing", "unchanged"):
            seed.add(
                HttpBridgeRetryCircuit(
                    session_key_kind="session_header",
                    session_key_hash=durable_bridge_hash(key),
                    api_key_scope="key-1",
                    consecutive_failures=0 if tombstone else 2,
                    cooldown_until_epoch=0.0,
                    last_detail=detail,
                    updated_at_epoch=observed_at,
                    admission_generation=0,
                )
            )
        await seed.commit()

    mutation_done = False
    async with SessionLocal() as cleanup:
        execute = cleanup.execute

        async def change_after_selection(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal mutation_done
            result = await execute(statement, *args, **kwargs)
            if not mutation_done and isinstance(statement, Select):
                mutation_done = True
                async with SessionLocal() as writer:
                    repository = DurableBridgeRepository(writer)
                    if mutation == "claim":
                        claimed = await repository.claim_retry_circuit_generation(
                            session_key_kind="session_header",
                            session_key_value="racing",
                            api_key_scope="key-1",
                            expected_updated_at_epoch=observed_at,
                            expected_admission_generation=0,
                            expected_consecutive_failures=0 if tombstone else 2,
                            expected_cooldown_until_epoch=0.0,
                        )
                        assert claimed is not None
                        assert claimed.admission_generation == 1
                        assert claimed.updated_at_epoch == observed_at
                    elif mutation == "timestamp":
                        await writer.execute(
                            update(HttpBridgeRetryCircuit)
                            .where(HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash("racing"))
                            .values(updated_at_epoch=observed_at + 1)
                        )
                        await writer.commit()
                    else:
                        await repository.upsert_retry_circuit(
                            session_key_kind="session_header",
                            session_key_value="racing",
                            api_key_scope="key-1",
                            consecutive_failures=1 if tombstone else 3,
                            cooldown_until_epoch=0.0,
                            last_detail="stream_incomplete",
                            updated_at_epoch=observed_at - 1,
                            base_updated_at_epoch=observed_at,
                            failure_threshold=2,
                            conflict_cooldown_until_epoch=observed_at + 60.0,
                        )
                return result
            return result

        monkeypatch.setattr(cleanup, "execute", change_after_selection)
        deleted = await DurableBridgeRepository(cleanup).purge_retry_circuits_before(
            cutoff, tombstone_cutoff_epoch=cutoff, batch_size=2
        )

    assert mutation_done
    assert deleted == 1
    async with SessionLocal() as observer:
        rows = (await observer.execute(select(HttpBridgeRetryCircuit))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.session_key_hash == durable_bridge_hash("racing")
        assert row.admission_generation == (1 if mutation == "claim" else 0)
        assert row.updated_at_epoch == (observed_at + 1 if mutation == "timestamp" else observed_at)
        assert row.consecutive_failures == (0 if tombstone else 2) + (mutation == "failure")
        assert row.last_detail == detail


async def test_scheduled_purge_drains_unchanged_rows_across_batches() -> None:
    async with SessionLocal() as seed:
        for index in range(5):
            seed.add(
                HttpBridgeRetryCircuit(
                    session_key_kind="session_header",
                    session_key_hash=durable_bridge_hash(f"unchanged-{index}"),
                    api_key_scope="key-1",
                    consecutive_failures=2,
                    cooldown_until_epoch=0.0,
                    last_detail="stream_incomplete",
                    updated_at_epoch=1000.0,
                    admission_generation=0,
                )
            )
        await seed.commit()

    async with SessionLocal() as cleanup:
        deleted = await DurableBridgeRepository(cleanup).purge_retry_circuits_before(100000.0, batch_size=2)
    assert deleted == 5
    async with SessionLocal() as observer:
        assert (await observer.execute(select(HttpBridgeRetryCircuit))).scalars().all() == []


@pytest.mark.parametrize("prior_detail", [None, "stream_incomplete", "anchor_abandoned"])
async def test_scheduled_purge_preserves_detail_only_partial_registration(
    monkeypatch: pytest.MonkeyPatch,
    prior_detail: str | None,
) -> None:
    # Registration has not committed a session/alias yet; only the detail
    # protects the abandoned anchor while settlement is suspended.
    async with SessionLocal() as seed:
        for key in ("settling", "unchanged"):
            seed.add(
                HttpBridgeRetryCircuit(
                    session_key_kind="session_header",
                    session_key_hash=durable_bridge_hash(key),
                    api_key_scope="key-1",
                    consecutive_failures=2,
                    cooldown_until_epoch=0.0,
                    last_detail=prior_detail,
                    updated_at_epoch=1000.0,
                    admission_generation=0,
                )
            )
        await seed.commit()
    changed = False
    new_detail = None if prior_detail == "anchor_abandoned" else "anchor_abandoned"
    async with SessionLocal() as cleanup:
        execute = cleanup.execute

        async def settle_after_selection(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal changed
            result = await execute(statement, *args, **kwargs)
            if not changed and isinstance(statement, Select):
                changed = True
                async with SessionLocal() as writer:
                    assert await DurableBridgeRepository(writer).supersede_retry_circuit_detail(
                        session_key_kind="session_header",
                        session_key_value="settling",
                        api_key_scope="key-1",
                        expected_updated_at_epoch=1000.0,
                        expected_consecutive_failures=2,
                        expected_last_detail=prior_detail,
                        last_detail=new_detail,
                    )
            return result

        monkeypatch.setattr(cleanup, "execute", settle_after_selection)
        deleted = await DurableBridgeRepository(cleanup).purge_retry_circuits_before(
            100000.0,
            tombstone_cutoff_epoch=100000.0,
            batch_size=2,
        )
    assert changed
    assert deleted == 1
    async with SessionLocal() as observer:
        rows = (await observer.execute(select(HttpBridgeRetryCircuit))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.session_key_hash == durable_bridge_hash("settling")
        assert row.last_detail == new_detail
        assert row.updated_at_epoch == 1000.0
        assert row.admission_generation == 0
        assert row.consecutive_failures == 2

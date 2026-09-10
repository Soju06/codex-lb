from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import HttpBridgeOperationRecord, HttpBridgeSessionRecord
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse_insertion", [False, True])
@pytest.mark.parametrize("newer_z", [False, True])
async def test_complete_transcript_response_timestamp_ties_have_stable_parent_chain(
    db_setup, reverse_insertion, newer_z
):
    """Equal response timestamps still reconstruct a stable parent-ordered transcript."""
    timestamp = datetime(2026, 9, 9, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        session.add(
            HttpBridgeSessionRecord(
                id="transcript-tie-session",
                session_key_kind="prompt_cache",
                session_key_value="transcript-tie",
                session_key_hash="transcript-tie-hash",
                api_key_scope="transcript-tie-scope",
            )
        )
        await session.flush()
        rows = []
        for suffix in ("a", "z"):
            for is_child in (False, True):
                operation_id = f"{'child' if is_child else 'root'}_{suffix}"
                rows.append(
                    HttpBridgeOperationRecord(
                        operation_id=operation_id,
                        session_id="transcript-tie-session",
                        request_fingerprint=operation_id,
                        state="completed",
                        response_id="resp_duplicate" if is_child else f"resp_root_{suffix}",
                        parent_response_id=f"resp_root_{suffix}" if is_child else None,
                        request_text=json.dumps({"type": "response.create", "input": operation_id}),
                        response_output_items_json="[]",
                        response_output_items_complete=True,
                        response_replay_input_turn_count=1,
                        updated_at=timestamp + timedelta(seconds=int(newer_z and suffix == "z")),
                    )
                )
        if reverse_insertion:
            rows.reverse()
        for row in rows:
            session.add(row)
            await session.flush()
        await session.commit()

    expected_suffix = "z" if newer_z else "a"
    # Fresh sessions force the real persisted selection rather than identity-map
    # ordering. Both insertion orders must produce the same complete chain.
    for _ in range(2):
        async with SessionLocal() as session:
            selected = await DurableBridgeRepository(session).get_operation_by_response_id(response_id="resp_duplicate")
            assert selected is not None
            assert selected.operation_id == f"child_{expected_suffix}"
            turns = await DurableBridgeRepository(session).get_complete_transcript(
                response_id="resp_duplicate", api_key_scope="transcript-tie-scope"
            )
            assert turns is not None
            assert [turn.operation.operation_id for turn in turns] == [
                f"root_{expected_suffix}",
                f"child_{expected_suffix}",
            ]
            request_inputs = []
            for turn in turns:
                assert turn.operation.request_text is not None
                request_inputs.append(json.loads(turn.operation.request_text)["input"])
            assert request_inputs == [
                f"root_{expected_suffix}",
                f"child_{expected_suffix}",
            ]

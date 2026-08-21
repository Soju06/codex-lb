from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.proxy.complete_transcript import (
    build_complete_replay_payload,
    build_replay_input_snapshot,
    materialize_output_items_from_events,
)
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeOperationSnapshot,
    DurableBridgeRepository,
    DurableBridgeTranscriptTurn,
)


def _turn(
    number: int,
    *,
    parent_response_id: str | None,
    response_id: str,
    request_input: list[dict[str, object]],
    output: list[dict[str, object]],
) -> DurableBridgeTranscriptTurn:
    operation = DurableBridgeOperationSnapshot(
        operation_id=f"op_{number}",
        session_id="session",
        request_fingerprint=f"fingerprint_{number}",
        account_id="account",
        model="gpt-test",
        parent_response_id=parent_response_id,
        state="completed",
        response_id=response_id,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": parent_response_id,
                "input": request_input,
            }
        ),
    )
    return DurableBridgeTranscriptTurn(operation=operation, events=(), response_output_items_json=json.dumps(output))


def test_build_complete_replay_payload_inserts_prior_assistant_output() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        ),
    ]

    payload = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_1",
                "input": [{"type": "message", "role": "user", "content": "follow up"}],
            }
        ),
    )

    assert payload is not None
    parsed = json.loads(payload)
    assert "previous_response_id" not in parsed
    assert [item["role"] for item in parsed["input"]] == ["user", "assistant", "user"]
    assert all("id" not in item for item in parsed["input"])


def test_build_replay_input_snapshot_is_self_contained_and_strips_ids() -> None:
    snapshot = build_replay_input_snapshot(
        [],
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "stale",
                "input": [{"type": "message", "role": "user", "content": "hello", "id": "in_1"}],
            }
        ),
        response_output_items_json=json.dumps(
            [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "world"}],
                },
                {"type": "reasoning", "id": "rs_1", "summary": []},
            ]
        ),
    )

    assert snapshot is not None
    items = json.loads(snapshot)
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert all("id" not in item for item in items)


def test_build_replay_input_snapshot_rejects_bounds() -> None:
    assert (
        build_replay_input_snapshot(
            [],
            request_text=json.dumps(
                {"type": "response.create", "input": [{"type": "message", "role": "user", "content": "hello"}]}
            ),
            response_output_items_json=json.dumps(
                [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}]
            ),
            max_input_items=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_transcript_prefers_snapshot_when_parent_chain_is_missing() -> None:
    row = SimpleNamespace(
        operation_id="op_snapshot",
        session_id="session",
        request_fingerprint="fingerprint",
        account_id="account",
        model="gpt-test",
        parent_response_id="purged-parent",
        state="completed",
        response_id="resp_snapshot",
        recovery_dispatch_count=0,
        request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "purged-parent",
                "input": [{"type": "message", "role": "user", "content": "old delta"}],
            }
        ),
        event_spool_complete=True,
        transcript_version=1,
        response_output_items_json="[]",
        response_output_items_complete=True,
        response_replay_input_json=json.dumps(
            [
                {"type": "message", "role": "user", "content": "first"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
            ]
        ),
        response_replay_input_complete=True,
    )

    class _Session:
        async def scalar(self, statement: object) -> object:
            return row

    turns = await DurableBridgeRepository(_Session()).get_complete_transcript(response_id="resp_snapshot")

    assert turns is not None
    assert len(turns) == 1
    replay = build_complete_replay_payload(
        turns,
        continuation_request_text=json.dumps(
            {
                "type": "response.create",
                "model": "gpt-test",
                "previous_response_id": "resp_snapshot",
                "input": [{"type": "message", "role": "user", "content": "continue"}],
            }
        ),
    )
    assert replay is not None
    assert [item["role"] for item in json.loads(replay)["input"]] == ["user", "assistant", "user"]


def test_build_complete_replay_payload_rejects_broken_parent_continuation() -> None:
    turns = [
        _turn(
            1,
            parent_response_id=None,
            response_id="resp_1",
            request_input=[{"type": "message", "role": "user", "content": "first"}],
            output=[
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "answer"}],
                }
            ],
        ),
    ]

    assert (
        build_complete_replay_payload(
            turns,
            continuation_request_text=json.dumps(
                {
                    "type": "response.create",
                    "model": "gpt-test",
                    "previous_response_id": "not_resp_1",
                    "input": [{"type": "message", "role": "user", "content": "follow up"}],
                }
            ),
        )
        is None
    )


def test_materialize_output_items_prefers_output_item_done_over_empty_completed_output() -> None:
    events = [
        (
            'event: response.output_item.done\ndata: '
            '{"type":"response.output_item.done","output_index":1,"item":'
            '{"id":"msg_1","type":"message","role":"assistant","status":"completed",'
            '"content":[{"type":"output_text","text":"answer"}]}}\n\n'
        ),
        (
            'event: response.output_item.done\ndata: '
            '{"type":"response.output_item.done","output_index":0,"item":'
            '{"id":"rs_1","type":"reasoning","summary":[]}}\n\n'
        ),
        (
            'event: response.completed\ndata: '
            '{"type":"response.completed","response":{"id":"resp_1","status":"completed",'
            '"output":[]}}\n\n'
        ),
    ]

    output = materialize_output_items_from_events(events)

    assert output is not None
    assert [item["type"] for item in output] == ["reasoning", "message"]


def test_materialize_output_items_requires_terminal_completion() -> None:
    events = [
        (
            'event: response.output_item.done\ndata: '
            '{"type":"response.output_item.done","output_index":0,"item":{"type":"message"}}\n\n'
        ),
    ]

    assert materialize_output_items_from_events(events) is None

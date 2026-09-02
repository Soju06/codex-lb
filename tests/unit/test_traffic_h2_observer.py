from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection

from scripts.traffic_analysis.h2_observer import (
    CLIENT_PREFACE,
    H2ObservationError,
    H2ObserverSession,
    JsonlSink,
    RawH2FrameParser,
    _is_loopback_bind,
    serve,
)
from scripts.traffic_analysis.http2_profile import compare_profiles, render_markdown


def _frame(frame_type: int, flags: int, stream_id: int, payload: bytes) -> bytes:
    return len(payload).to_bytes(3, "big") + bytes((frame_type, flags)) + stream_id.to_bytes(4, "big") + payload


def test_raw_parser_handles_fragmentation_and_safe_metadata() -> None:
    settings_payload = b"\x00\x03\x00\x00\x00d\x00\x04\x00\x0f\xff\xff"
    update_payload = (65_535).to_bytes(4, "big")
    header_payload = b"secret-hpack-fragment"
    wire = (
        CLIENT_PREFACE
        + _frame(4, 0, 0, settings_payload)
        + _frame(8, 0, 0, update_payload)
        + _frame(1, 5, 1, header_payload)
    )
    parser = RawH2FrameParser()

    for offset in range(0, len(wire), 3):
        parser.feed(wire[offset : offset + 3])

    assert parser.preface_seen is True
    assert parser.initial_settings == [{"id": 3, "value": 100}, {"id": 4, "value": 1_048_575}]
    assert parser.frames[1].window_increment == 65_535
    assert parser.frames[2].length == len(header_payload)
    assert parser.frames[2].fragment_sha256 is not None
    assert header_payload.decode() not in json.dumps([frame.as_record() for frame in parser.frames])


def test_raw_parser_rejects_invalid_preface_and_bounds() -> None:
    with pytest.raises(H2ObservationError, match="preface"):
        RawH2FrameParser().feed(b"X")

    parser = RawH2FrameParser(max_frame_payload=3)
    with pytest.raises(H2ObservationError, match="payload limit"):
        parser.feed(CLIENT_PREFACE + _frame(0, 0, 1, b"four"))

    parser = RawH2FrameParser(max_tracked_frames=1)
    with pytest.raises(H2ObservationError, match="frame count"):
        parser.feed(CLIENT_PREFACE + _frame(6, 0, 0, b"12345678") + _frame(6, 0, 0, b"12345678"))


@pytest.mark.asyncio
async def test_h2_session_serves_json_sse_and_records_reuse_without_values(tmp_path: Path) -> None:
    output = tmp_path / "wire.jsonl"
    session = H2ObserverSession("connection-test", JsonlSink(output))
    client = H2Connection(H2Configuration(client_side=True, header_encoding="utf-8"))
    client.initiate_connection()
    client.receive_data(session.initiate())
    server_bytes, records = await session.receive(client.data_to_send())
    assert records == []
    client.receive_data(server_bytes)

    secret = "Bearer must-not-be-recorded"
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":scheme", "https"),
            (":authority", "observer.test"),
            (":path", "/v1/models"),
            ("authorization", secret),
        ],
        end_stream=True,
    )
    server_bytes, model_records = await session.receive(client.data_to_send())
    client.receive_data(server_bytes)

    json_body = b'{"model":"gpt-5.6-luna","stream":false,"input":"private body"}'
    client.send_headers(
        3,
        [
            (":method", "POST"),
            (":scheme", "https"),
            (":authority", "observer.test"),
            (":path", "/v1/responses"),
            ("content-type", "application/json"),
        ],
    )
    client.send_data(3, json_body, end_stream=True)
    server_bytes, json_records = await session.receive(client.data_to_send())
    client.receive_data(server_bytes)

    sse_body = b'{"model":"gpt-5.6-luna","stream":true,"input":"private stream body"}'
    client.send_headers(
        5,
        [
            (":method", "POST"),
            (":scheme", "https"),
            (":authority", "observer.test"),
            (":path", "/backend-api/codex/responses"),
            ("content-type", "application/json"),
        ],
    )
    client.send_data(5, sse_body, end_stream=True)
    server_bytes, sse_records = await session.receive(client.data_to_send())
    client.receive_data(server_bytes)

    records = model_records + json_records + sse_records
    assert [record["response"]["transport"] for record in records] == ["http_json", "http_json", "http_sse"]
    assert [record["stream_id"] for record in records] == [1, 3, 5]
    assert [record["connection_reused"] for record in records] == [False, True, True]
    assert records[0]["request"]["header_names"][-1] == "authorization"
    assert any(frame["type"] == "DATA" and frame["length"] == len(json_body) for frame in records[1]["request_frames"])
    persisted = output.read_text()
    assert secret not in persisted
    assert "private body" not in persisted
    assert "private stream body" not in persisted
    assert "observer.test" not in persisted


def _record(
    *,
    settings: list[dict[str, int]] | None = None,
    headers: list[str] | None = None,
    stream_id: int = 1,
    sequence: int = 1,
    connection_id: str = "connection-1",
    data_lengths: list[int] | None = None,
) -> dict[str, object]:
    request_frames: list[dict[str, object]] = [
        {"type": "HEADERS", "flags": 4, "length": 20, "fragment_sha256": "opaque", "stream_id": stream_id}
    ]
    for index, length in enumerate(data_lengths or []):
        request_frames.append(
            {
                "type": "DATA",
                "flags": 1 if index == len(data_lengths or []) - 1 else 0,
                "length": length,
                "stream_id": stream_id,
            }
        )
    return {
        "kind": "http2_wire_request",
        "connection_id": connection_id,
        "request_sequence": sequence,
        "stream_id": stream_id,
        "connection_reused": sequence > 1,
        "initial_settings": settings if settings is not None else [{"id": 3, "value": 100}],
        "connection_control_frames": [{"type": "SETTINGS", "flags": 0, "stream_id": 0, "length": 6}],
        "request_frames": request_frames,
        "request": {"header_names": headers if headers is not None else [":method", "user-agent"]},
    }


def test_profile_comparator_separates_stable_gates_and_hpack_information() -> None:
    direct = [_record(), _record(stream_id=3, sequence=2)]
    routed = [_record(), _record(stream_id=3, sequence=2)]

    result = compare_profiles(direct, routed, direct)

    assert result["a_vs_c"]["all_observed_match"] is True
    assert result["a_reference_vs_a"]["all_observed_match"] is True
    assert result["hpack_informational"]["path_a"][0]["sha256"] == "opaque"
    assert "Informational HPACK" in render_markdown(result)


def test_profile_comparator_ignores_server_reactive_settings_ack_timing() -> None:
    direct = [_record()]
    reference = [_record()]
    reference[0]["connection_control_frames"] = [
        {"type": "SETTINGS", "flags": 0, "stream_id": 0, "length": 6},
        {"type": "SETTINGS", "flags": 1, "stream_id": 0, "length": 0},
    ]

    result = compare_profiles(direct, direct, reference)

    assert result["a_reference_vs_a"]["dimensions"]["connection_control_shape"]["match"] is True


def test_profile_comparator_reports_independent_differences_and_missing_evidence() -> None:
    direct = [_record(headers=[":method", "User-Agent"])]
    routed = [_record(settings=[{"id": 3, "value": 10}], headers=[":method", "user-agent"])]

    result = compare_profiles(direct, routed)

    dimensions = result["a_vs_c"]["dimensions"]
    assert dimensions["initial_settings"]["match"] is False
    assert dimensions["header_name_order"]["match"] is True
    assert dimensions["header_name_casing"]["match"] is False

    missing = compare_profiles([], routed)
    assert missing["a_vs_c"]["dimensions"]["initial_settings"]["observed"] is False
    assert missing["a_vs_c"]["dimensions"]["initial_settings"]["match"] is None
    assert "N/A" in render_markdown(missing)


def test_profile_casing_is_independent_from_lowercase_header_order() -> None:
    direct = [_record(headers=[":method", "accept", "user-agent"])]
    reordered = [_record(headers=[":method", "user-agent", "accept", "version"])]

    result = compare_profiles(direct, reordered)

    dimensions = result["a_vs_c"]["dimensions"]
    assert dimensions["header_name_order"]["match"] is False
    assert dimensions["header_name_casing"]["match"] is True


def test_profile_normalizes_variable_data_tail_but_detects_chunking_policy() -> None:
    direct = [_record(data_lengths=[16_384, 16_384, 7_008])]
    routed = [_record(data_lengths=[16_384, 16_384, 5_321])]

    matched = compare_profiles(direct, routed)

    dimension = matched["a_vs_c"]["dimensions"]["request_data_segmentation"]
    assert dimension["match"] is True
    assert dimension["left"] == [
        [
            {"size_class": "max", "end_stream": False, "padded": False},
            {"size_class": "max", "end_stream": False, "padded": False},
            {"size_class": "partial", "end_stream": True, "padded": False},
        ]
    ]

    smaller_chunks = [_record(data_lengths=[8_192, 8_192, 5_321])]
    mismatched = compare_profiles(direct, smaller_chunks)

    assert mismatched["a_vs_c"]["dimensions"]["request_data_segmentation"]["match"] is False


def test_public_bind_requires_explicit_acknowledgement() -> None:
    assert _is_loopback_bind("127.0.0.1") is True
    assert _is_loopback_bind("::1") is True
    assert _is_loopback_bind("localhost") is True
    assert _is_loopback_bind("0.0.0.0") is False


@pytest.mark.asyncio
async def test_public_bind_is_rejected_before_tls_setup(tmp_path: Path) -> None:
    args = argparse.Namespace(
        host="0.0.0.0",
        port=19443,
        cert=tmp_path / "missing.crt",
        key=tmp_path / "missing.key",
        output=tmp_path / "wire.jsonl",
        allow_public_bind=False,
    )

    with pytest.raises(SystemExit, match="allow-public-bind"):
        await serve(args)

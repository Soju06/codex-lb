from __future__ import annotations

import zlib
from hashlib import sha256
from typing import Any, cast

import pytest

from app.modules.proxy.durable_bridge_transcript_codec import (
    DURABLE_BRIDGE_CHUNK_CODEC,
    DURABLE_BRIDGE_CHUNK_MAX_EVENTS,
    DurableBridgeTranscriptDecodeError,
    decode_durable_bridge_transcript_chunk,
    encode_durable_bridge_transcript_chunk,
)

pytestmark = pytest.mark.unit


def _decode(encoded, *, max_uncompressed_bytes: int | None = None) -> tuple[str, ...]:
    return decode_durable_bridge_transcript_chunk(
        codec=encoded.codec,
        payload=encoded.payload,
        event_count=encoded.event_count,
        uncompressed_bytes=encoded.uncompressed_bytes,
        payload_sha256=encoded.payload_sha256,
        max_uncompressed_bytes=max_uncompressed_bytes or encoded.uncompressed_bytes,
    )


def test_chunk_codec_round_trips_exact_events_and_duplicates() -> None:
    events = (
        'event: response.output_text.delta\ndata: {"delta":"안녕"}\n\n',
        "",
        'data: {"type":"response.completed"}\n\n',
        'data: {"type":"response.completed"}\n\n',
    )

    encoded = encode_durable_bridge_transcript_chunk(events)

    assert encoded.codec == DURABLE_BRIDGE_CHUNK_CODEC
    assert encoded.event_count == len(events)
    assert _decode(encoded) == events


def test_chunk_codec_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        encode_durable_bridge_transcript_chunk(())

    with pytest.raises(ValueError, match="event-count limit"):
        encode_durable_bridge_transcript_chunk(tuple("event" for _ in range(DURABLE_BRIDGE_CHUNK_MAX_EVENTS + 1)))


def test_chunk_codec_rejects_unknown_codec_and_oversized_output() -> None:
    encoded = encode_durable_bridge_transcript_chunk(("event",))

    with pytest.raises(DurableBridgeTranscriptDecodeError, match="unknown"):
        decode_durable_bridge_transcript_chunk(
            codec="unknown",
            payload=encoded.payload,
            event_count=encoded.event_count,
            uncompressed_bytes=encoded.uncompressed_bytes,
            payload_sha256=encoded.payload_sha256,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )
    with pytest.raises(DurableBridgeTranscriptDecodeError, match="byte limit"):
        _decode(encoded, max_uncompressed_bytes=encoded.uncompressed_bytes - 1)


def test_chunk_codec_rejects_hash_mismatch_and_trailing_stream() -> None:
    encoded = encode_durable_bridge_transcript_chunk(("event",))

    with pytest.raises(DurableBridgeTranscriptDecodeError, match="hash mismatch"):
        decode_durable_bridge_transcript_chunk(
            codec=encoded.codec,
            payload=encoded.payload,
            event_count=encoded.event_count,
            uncompressed_bytes=encoded.uncompressed_bytes,
            payload_sha256="0" * 64,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )
    with pytest.raises(DurableBridgeTranscriptDecodeError, match="byte count mismatch"):
        decode_durable_bridge_transcript_chunk(
            codec=encoded.codec,
            payload=encoded.payload + zlib.compress(b"extra"),
            event_count=encoded.event_count,
            uncompressed_bytes=encoded.uncompressed_bytes,
            payload_sha256=encoded.payload_sha256,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )

    with pytest.raises(DurableBridgeTranscriptDecodeError, match="not hexadecimal"):
        decode_durable_bridge_transcript_chunk(
            codec=encoded.codec,
            payload=encoded.payload,
            event_count=encoded.event_count,
            uncompressed_bytes=encoded.uncompressed_bytes,
            payload_sha256="가" * 64,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )


def test_chunk_codec_rejects_malformed_framing_and_invalid_utf8() -> None:
    malformed = b"\x00\x00\x00\x05abc"
    invalid_utf8 = b"\x00\x00\x00\x01\xff"

    for canonical, message in (
        (malformed, "truncated event"),
        (invalid_utf8, "invalid UTF-8"),
    ):
        with pytest.raises(DurableBridgeTranscriptDecodeError, match=message):
            decode_durable_bridge_transcript_chunk(
                codec=DURABLE_BRIDGE_CHUNK_CODEC,
                payload=zlib.compress(canonical),
                event_count=1,
                uncompressed_bytes=len(canonical),
                payload_sha256=sha256(canonical).hexdigest(),
                max_uncompressed_bytes=len(canonical),
            )


def test_chunk_codec_rejects_declared_event_count_with_trailing_bytes() -> None:
    encoded = encode_durable_bridge_transcript_chunk(("one", "two"))

    with pytest.raises(DurableBridgeTranscriptDecodeError, match="trailing bytes"):
        decode_durable_bridge_transcript_chunk(
            codec=encoded.codec,
            payload=encoded.payload,
            event_count=1,
            uncompressed_bytes=encoded.uncompressed_bytes,
            payload_sha256=encoded.payload_sha256,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )


@pytest.mark.parametrize(
    ("event_count", "uncompressed_bytes"),
    [
        (1.5, 8),
        (True, 8),
        (1, 8.5),
        (1, False),
    ],
)
def test_chunk_codec_rejects_non_integral_numeric_metadata(
    event_count: object,
    uncompressed_bytes: object,
) -> None:
    encoded = encode_durable_bridge_transcript_chunk(("test",))

    with pytest.raises(DurableBridgeTranscriptDecodeError, match="must be integral"):
        decode_durable_bridge_transcript_chunk(
            codec=encoded.codec,
            payload=encoded.payload,
            event_count=cast(Any, event_count),
            uncompressed_bytes=cast(Any, uncompressed_bytes),
            payload_sha256=encoded.payload_sha256,
            max_uncompressed_bytes=encoded.uncompressed_bytes,
        )

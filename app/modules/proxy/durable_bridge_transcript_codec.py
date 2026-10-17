from __future__ import annotations

import hmac
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

DURABLE_BRIDGE_CHUNK_CODEC = "zlib-length-prefixed-utf8-v1"
DURABLE_BRIDGE_CHUNK_MAX_EVENTS = 256
DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS = 65_536
_FRAME_LENGTH = struct.Struct(">I")


class DurableBridgeTranscriptDecodeError(ValueError):
    """Raised when a durable chunk cannot be decoded without ambiguity."""


@dataclass(frozen=True, slots=True)
class EncodedDurableBridgeTranscriptChunk:
    codec: str
    payload: bytes
    event_count: int
    uncompressed_bytes: int
    payload_sha256: str


def encode_durable_bridge_transcript_chunk(
    events: Sequence[str],
) -> EncodedDurableBridgeTranscriptChunk:
    if not events:
        raise ValueError("durable transcript chunks require at least one event")
    if len(events) > DURABLE_BRIDGE_CHUNK_MAX_EVENTS:
        raise ValueError("durable transcript chunk exceeds event-count limit")
    framed = bytearray()
    for event in events:
        encoded = event.encode("utf-8")
        if len(encoded) > 0xFFFFFFFF:
            raise ValueError("durable transcript event exceeds framing limit")
        framed.extend(_FRAME_LENGTH.pack(len(encoded)))
        framed.extend(encoded)
    canonical = bytes(framed)
    return EncodedDurableBridgeTranscriptChunk(
        codec=DURABLE_BRIDGE_CHUNK_CODEC,
        payload=zlib.compress(canonical),
        event_count=len(events),
        uncompressed_bytes=len(canonical),
        payload_sha256=sha256(canonical).hexdigest(),
    )


def decode_durable_bridge_transcript_chunk(
    *,
    codec: str,
    payload: bytes,
    event_count: int,
    uncompressed_bytes: int,
    payload_sha256: str,
    max_uncompressed_bytes: int,
) -> tuple[str, ...]:
    if codec != DURABLE_BRIDGE_CHUNK_CODEC:
        raise DurableBridgeTranscriptDecodeError("unknown durable transcript chunk codec")
    if type(event_count) is not int or type(uncompressed_bytes) is not int:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk numeric metadata must be integral")
    if event_count <= 0:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk event count must be positive")
    if event_count > DURABLE_BRIDGE_CHUNK_MAX_EVENTS:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk exceeds event-count limit")
    if uncompressed_bytes < 0 or uncompressed_bytes > max_uncompressed_bytes:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk exceeds decode byte limit")

    decompressor = zlib.decompressobj()
    try:
        canonical = decompressor.decompress(payload, uncompressed_bytes + 1)
    except zlib.error as exc:
        raise DurableBridgeTranscriptDecodeError("invalid durable transcript compressed payload") from exc
    if (
        len(canonical) != uncompressed_bytes
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk byte count mismatch")
    try:
        expected_digest = bytes.fromhex(payload_sha256)
    except (TypeError, ValueError) as exc:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk hash is not hexadecimal") from exc
    if len(expected_digest) != sha256().digest_size:
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk hash has invalid length")
    if not hmac.compare_digest(sha256(canonical).digest(), expected_digest):
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk hash mismatch")

    events: list[str] = []
    offset = 0
    for _ in range(event_count):
        header_end = offset + _FRAME_LENGTH.size
        if header_end > len(canonical):
            raise DurableBridgeTranscriptDecodeError("durable transcript chunk has truncated frame header")
        (event_size,) = _FRAME_LENGTH.unpack(canonical[offset:header_end])
        event_end = header_end + event_size
        if event_end > len(canonical):
            raise DurableBridgeTranscriptDecodeError("durable transcript chunk has truncated event")
        try:
            events.append(canonical[header_end:event_end].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise DurableBridgeTranscriptDecodeError("durable transcript chunk contains invalid UTF-8") from exc
        offset = event_end
    if offset != len(canonical):
        raise DurableBridgeTranscriptDecodeError("durable transcript chunk contains trailing bytes")
    return tuple(events)


__all__ = [
    "DURABLE_BRIDGE_CHUNK_CODEC",
    "DURABLE_BRIDGE_CHUNK_MAX_EVENTS",
    "DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS",
    "DurableBridgeTranscriptDecodeError",
    "EncodedDurableBridgeTranscriptChunk",
    "decode_durable_bridge_transcript_chunk",
    "encode_durable_bridge_transcript_chunk",
]

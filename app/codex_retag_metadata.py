from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

METADATA_BYTE_LIMIT = 64 * 1024


@dataclass(frozen=True)
class Metadata:
    records: tuple[dict[str, object], ...]
    end: int

    def counts(self) -> Counter[str]:
        return Counter(provider for record in self.records if (provider := record_provider(record)) is not None)


def record_provider(record: dict[str, object]) -> str | None:
    provider = record.get("model_provider")
    if isinstance(provider, str):
        return provider
    payload = record.get("payload")
    if record.get("type") == "session_meta" and isinstance(payload, dict):
        provider = payload.get("model_provider")
        if isinstance(provider, str):
            return provider
    return None


def read_metadata(path: Path) -> Metadata:
    records: list[dict[str, object]] = []
    end = 0
    with path.open("rb") as source:
        prefix = source.read(METADATA_BYTE_LIMIT)
    with BytesIO(prefix) as handle:
        while handle.tell() < METADATA_BYTE_LIMIT:
            remaining = METADATA_BYTE_LIMIT - handle.tell()
            raw = handle.readline(remaining)
            if not raw:
                break
            if not raw.endswith(b"\n") and len(raw) == remaining:
                if records:
                    break
                raise ValueError(f"Session metadata exceeds {METADATA_BYTE_LIMIT} bytes: {path}")
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                if records:
                    break
                raise ValueError(f"Invalid initial session metadata: {path}") from exc
            if not isinstance(record, dict) or record_provider(record) is None:
                break
            records.append(record)
            end = handle.tell()
            if record.get("type") == "session_meta":
                break
    return Metadata(tuple(records), end)

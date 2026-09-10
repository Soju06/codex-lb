from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from scripts.traffic_analysis.artifacts import (
    atomic_write_json,
    atomic_write_text,
    file_attestation,
    file_digest,
    read_json,
)


def test_digest_and_attestation_stream_the_same_file(tmp_path: Path) -> None:
    source = tmp_path / "evidence.jsonl"
    payload = b"a" * (1024 * 1024 + 17)
    source.write_bytes(payload)

    digest = file_digest(source)
    attestation = file_attestation("semantic_path_b", source)

    assert digest == {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    assert attestation == {
        "label": "semantic_path_b",
        "path": str(source),
        **digest,
        "available": True,
        "error": None,
    }


def test_attestation_fails_closed_without_exposing_exception(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"

    result = file_attestation("path_c", missing)

    assert result["available"] is False
    assert result["error"] == "FileNotFoundError"
    assert result["sha256"] is None


def test_atomic_writes_replace_content_and_honor_mode(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "state.json"
    atomic_write_json(output, {"old": True}, mode=0o600)
    atomic_write_json(output, {"new": True}, mode=0o600)

    assert read_json(output) == {"new": True}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert list(output.parent.glob(".*.tmp")) == []

    text_output = tmp_path / "report.md"
    atomic_write_text(text_output, "# PASS\n")
    assert text_output.read_text() == "# PASS\n"
    assert json.loads(json.dumps(read_json(output))) == {"new": True}

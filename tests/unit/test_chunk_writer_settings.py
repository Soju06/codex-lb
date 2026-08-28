from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


def test_chunk_writer_format_defaults_off_and_validates(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_FORMAT", raising=False)

    assert Settings(_env_file=None).http_responses_session_bridge_operation_spool_format == "rows_v1"
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_FORMAT", "chunks_v2")
    assert Settings(_env_file=None).http_responses_session_bridge_operation_spool_format == "chunks_v2"
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_FORMAT", "unknown")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

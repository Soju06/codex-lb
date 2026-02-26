from __future__ import annotations

import pytest

from app.db.models import ModelOverride
from app.modules.model_overrides.service import ModelOverridesService, RequestActorContext

pytestmark = pytest.mark.unit


class StubRepo:
    def __init__(self, rows: dict[tuple[str, str], ModelOverride]) -> None:
        self._rows = rows

    async def find_first_enabled(self, match_type: str, match_value: str) -> ModelOverride | None:
        return self._rows.get((match_type, match_value))


def _row(
    *,
    row_id: int,
    match_type: str,
    match_value: str,
    forced_model: str,
    forced_reasoning_effort: str | None = None,
) -> ModelOverride:
    row = ModelOverride(
        id=row_id,
        match_type=match_type,
        match_value=match_value,
        forced_model=forced_model,
        forced_reasoning_effort=forced_reasoning_effort,
        enabled=True,
        note=None,
    )
    return row


@pytest.mark.asyncio
async def test_resolve_prefers_api_key_then_app_then_ip() -> None:
    repo = StubRepo(
        {
            ("api_key", "hash:abc"): _row(
                row_id=1,
                match_type="api_key",
                match_value="hash:abc",
                forced_model="gpt-5.3-codex",
            ),
            ("app", "openclaw"): _row(
                row_id=2,
                match_type="app",
                match_value="openclaw",
                forced_model="gpt-5.1-codex",
            ),
            ("ip", "192.168.2.10"): _row(
                row_id=3,
                match_type="ip",
                match_value="192.168.2.10",
                forced_model="gpt-4o-mini",
            ),
        }
    )
    service = ModelOverridesService(repo)  # type: ignore[arg-type]

    resolved = await service.resolve(
        RequestActorContext(
            client_ip="192.168.2.10",
            client_app="OpenClaw",
            api_key_identifier="HASH:ABC",
        )
    )
    assert resolved is not None
    assert resolved.match_type == "api_key"
    assert resolved.forced_model == "gpt-5.3-codex"


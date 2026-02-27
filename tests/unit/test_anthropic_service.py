from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from app.core.auth.anthropic_credentials import AnthropicCredentials
from app.core.clients.anthropic_usage import AnthropicUsageSnapshot, AnthropicUsageWindow
from app.modules.anthropic.repository import AnthropicRepository
from app.modules.anthropic.service import AnthropicService, _StreamAccumulator, _usage_from_message_payload

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class _StubAccount:
    id: str


class _StubAnthropicRepository:
    def __init__(self) -> None:
        self.usage_entries: list[tuple[str, float, str, int | None, int]] = []

    async def ensure_provider_account(self, *, account_id: str, email: str, plan_type: str) -> _StubAccount:
        return _StubAccount(id=account_id)

    async def add_usage_entry(
        self,
        *,
        account_id: str,
        used_percent: float,
        window: str,
        reset_at: int | None,
        window_minutes: int,
    ):
        self.usage_entries.append((account_id, used_percent, window, reset_at, window_minutes))
        return None


def test_stream_accumulator_collects_usage_and_terminal() -> None:
    accumulator = _StreamAccumulator(model="claude-sonnet-4-20250514")

    accumulator.observe(
        {
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 12, "cache_read_input_tokens": 3},
            },
        }
    )
    accumulator.observe({"type": "message_delta", "usage": {"output_tokens": 8}})
    accumulator.observe({"type": "message_stop"})
    accumulator.mark_stream_end()

    usage = accumulator.to_usage()
    assert accumulator.status == "success"
    assert usage.input_tokens == 15
    assert usage.output_tokens == 8
    assert usage.cached_input_tokens == 3


def test_stream_accumulator_marks_incomplete_without_terminal() -> None:
    accumulator = _StreamAccumulator(model="claude-sonnet-4-20250514")
    accumulator.observe({"type": "message_start", "message": {"usage": {"input_tokens": 1}}})
    accumulator.mark_stream_end()

    assert accumulator.status == "error"
    assert accumulator.error_code == "stream_incomplete"


def test_usage_from_message_payload_normalizes_cached_over_input() -> None:
    usage = _usage_from_message_payload(
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 51,
                "cache_read_input_tokens": 16018,
            }
        }
    )

    assert usage.input_tokens == 16028
    assert usage.output_tokens == 51
    assert usage.cached_input_tokens == 16018


def test_usage_from_message_payload_includes_cache_creation_tokens() -> None:
    usage = _usage_from_message_payload(
        {
            "usage": {
                "input_tokens": 11,
                "cache_creation_input_tokens": 7,
                "cache_read_input_tokens": 3,
                "output_tokens": 2,
            }
        }
    )

    assert usage.input_tokens == 21
    assert usage.output_tokens == 2
    assert usage.cached_input_tokens == 3


@pytest.mark.asyncio
async def test_refresh_usage_windows_writes_primary_and_secondary(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_USAGE_REFRESH_ENABLED", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()

    async def _stub_credentials(*, force_refresh: bool = False):
        return AnthropicCredentials(
            bearer_token="sk-ant-oat01-test",
            org_id="org_test",
            source="test",
        )

    async def _stub_usage_snapshot(*, bearer_token: str, base_url=None, session=None):
        assert bearer_token == "sk-ant-oat01-test"
        return AnthropicUsageSnapshot(
            five_hour=AnthropicUsageWindow(used_percent=12.5, reset_at_epoch=1000, window_minutes=300),
            seven_day=AnthropicUsageWindow(used_percent=45.0, reset_at_epoch=2000, window_minutes=10080),
        )

    monkeypatch.setattr("app.modules.anthropic.service.resolve_anthropic_credentials", _stub_credentials)
    monkeypatch.setattr("app.modules.anthropic.service.fetch_usage_snapshot", _stub_usage_snapshot)

    repository = _StubAnthropicRepository()
    service = AnthropicService(cast(AnthropicRepository, repository))

    refreshed = await service.refresh_usage_windows()

    assert refreshed is True
    assert len(repository.usage_entries) == 2
    by_window = {entry[2]: entry for entry in repository.usage_entries}
    assert by_window["primary"][1] == 12.5
    assert by_window["primary"][4] == 300
    assert by_window["secondary"][1] == 45.0
    assert by_window["secondary"][4] == 10080

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import app.modules.team.service as team_service_module
from app.core.exceptions import (
    TeamMemberOverCapError,
    TeamMemberSuspendedError,
    TeamModelNotAllowedError,
)
from app.core.utils.time import utcnow
from app.db.models import TeamMember, TeamMemberStatus
from app.modules.api_keys.service import ApiKeyData
from app.modules.team.repository import TeamUsageTotals
from app.modules.team.service import TeamService, reset_team_usage_cache
from app.modules.team.windows import window_end, window_start

pytestmark = pytest.mark.unit


class _FakeTeamRepository:
    def __init__(self, member: TeamMember | None, totals: TeamUsageTotals | None = None) -> None:
        self._member = member
        self._totals = totals or TeamUsageTotals(cost_usd=0.0, tokens=0)
        self.get_calls = 0
        self.aggregate_calls = 0

    async def get_by_id(self, member_id: str) -> TeamMember | None:
        self.get_calls += 1
        if self._member is not None and self._member.id == member_id:
            return self._member
        return None

    async def aggregate_usage(self, member_id: str, *, since: datetime) -> TeamUsageTotals:
        self.aggregate_calls += 1
        return self._totals


def _make_member(**overrides) -> TeamMember:
    values = {
        "id": "member-1",
        "name": "Ada",
        "email": None,
        "status": TeamMemberStatus.ACTIVE,
        "cost_cap_day_usd": None,
        "cost_cap_week_usd": None,
        "cost_cap_month_usd": None,
        "token_cap_day": None,
        "token_cap_week": None,
        "token_cap_month": None,
        "allowed_models": None,
        "notes": None,
    }
    values.update(overrides)
    return TeamMember(**values)


def _make_api_key(member_id: str | None) -> ApiKeyData:
    return ApiKeyData(
        id="key-1",
        name="key",
        key_prefix="sk-clb-abcdefg",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 9, 18, 12, 0, 0),
        last_used_at=None,
        member_id=member_id,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_team_usage_cache()
    yield
    reset_team_usage_cache()


@pytest.mark.asyncio
async def test_no_member_is_a_no_op():
    repository = _FakeTeamRepository(_make_member())
    service = TeamService(repository)

    await service.check_member_gate(_make_api_key(None), "model-alpha")

    assert repository.get_calls == 0
    assert repository.aggregate_calls == 0


@pytest.mark.asyncio
async def test_suspended_member_is_rejected():
    repository = _FakeTeamRepository(_make_member(status=TeamMemberStatus.SUSPENDED))
    service = TeamService(repository)

    with pytest.raises(TeamMemberSuspendedError) as excinfo:
        await service.check_member_gate(_make_api_key("member-1"), "model-alpha")

    assert excinfo.value.status_code == 403
    assert excinfo.value.error_type == "team_member_suspended"


@pytest.mark.asyncio
async def test_model_outside_member_allowlist_is_rejected():
    repository = _FakeTeamRepository(_make_member(allowed_models=json.dumps(["model-alpha"])))
    service = TeamService(repository)

    with pytest.raises(TeamModelNotAllowedError) as excinfo:
        await service.check_member_gate(_make_api_key("member-1"), "model-beta")

    assert excinfo.value.status_code == 403
    assert excinfo.value.error_type == "team_model_not_allowed"

    await service.check_member_gate(_make_api_key("member-1"), "model-alpha")


@pytest.mark.asyncio
async def test_over_day_cost_cap_raises_429_with_window_headers():
    member = _make_member(cost_cap_day_usd=5.0)
    repository = _FakeTeamRepository(member, TeamUsageTotals(cost_usd=5.25, tokens=10))
    service = TeamService(repository)

    with pytest.raises(TeamMemberOverCapError) as excinfo:
        await service.check_member_gate(_make_api_key("member-1"), "model-alpha")

    error = excinfo.value
    assert error.status_code == 429
    assert error.error_type == "team_member_over_cap"
    assert error.window == "day"
    assert error.headers["X-Team-Window"] == "day"
    reset_header = error.headers["X-Team-Reset"]
    assert reset_header.endswith("Z")
    assert reset_header == error.reset_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    assert datetime.fromisoformat(reset_header) == window_end("day", utcnow()).replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_over_day_token_cap_raises_429():
    member = _make_member(token_cap_day=1_000)
    repository = _FakeTeamRepository(member, TeamUsageTotals(cost_usd=0.0, tokens=1_000))
    service = TeamService(repository)

    with pytest.raises(TeamMemberOverCapError) as excinfo:
        await service.check_member_gate(_make_api_key("member-1"), "model-alpha")

    assert excinfo.value.window == "day"


@pytest.mark.asyncio
async def test_under_cap_passes():
    member = _make_member(cost_cap_day_usd=5.0, token_cap_month=1_000_000)
    repository = _FakeTeamRepository(member, TeamUsageTotals(cost_usd=1.25, tokens=500))
    service = TeamService(repository)

    await service.check_member_gate(_make_api_key("member-1"), "model-alpha")

    assert repository.aggregate_calls == 2


@pytest.mark.asyncio
async def test_aggregate_cache_is_reused_then_expires(monkeypatch):
    member = _make_member(cost_cap_day_usd=100.0)
    repository = _FakeTeamRepository(member, TeamUsageTotals(cost_usd=1.0, tokens=1))
    service = TeamService(repository)

    clock = {"now": 1_000.0}
    monkeypatch.setattr(team_service_module, "_monotonic", lambda: clock["now"])

    await service.check_member_gate(_make_api_key("member-1"), "model-alpha")
    assert repository.aggregate_calls == 1

    clock["now"] += 0.5
    await service.check_member_gate(_make_api_key("member-1"), "model-alpha")
    assert repository.aggregate_calls == 1

    clock["now"] += 0.6  # past the 1s TTL
    await service.check_member_gate(_make_api_key("member-1"), "model-alpha")
    assert repository.aggregate_calls == 2


def test_utc_calendar_windows():
    now = datetime(2026, 9, 18, 13, 45, 12)  # a Friday

    assert window_start("day", now) == datetime(2026, 9, 18, 0, 0, 0)
    assert window_end("day", now) == datetime(2026, 9, 19, 0, 0, 0)
    assert window_start("week", now) == datetime(2026, 9, 14, 0, 0, 0)
    assert window_end("week", now) == datetime(2026, 9, 21, 0, 0, 0)
    assert window_start("month", now) == datetime(2026, 9, 1, 0, 0, 0)
    assert window_end("month", now) == datetime(2026, 10, 1, 0, 0, 0)
    assert window_end("month", datetime(2026, 12, 31, 23, 0, 0)) == datetime(2027, 1, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_proxy_enforce_request_limits_runs_the_gate_before_reserving(monkeypatch):
    """The gate sits ahead of enforce_limits_for_request, so a blocked member never reserves."""

    import app.modules.proxy.api as proxy_api

    calls: list[tuple[str | None, str | None]] = []

    async def _gate(api_key, *, model):
        calls.append((api_key.id, model))
        raise TeamMemberSuspendedError("blocked")

    def _explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("enforce_limits_for_request must not be reached")

    monkeypatch.setattr(proxy_api, "check_member_gate", _gate)
    monkeypatch.setattr(proxy_api, "get_background_session", _explode)

    with pytest.raises(TeamMemberSuspendedError):
        await proxy_api._enforce_request_limits(
            _make_api_key("member-1"),
            request_model="model-alpha",
            request_service_tier=None,
        )

    assert calls == [("key-1", "model-alpha")]


@pytest.mark.asyncio
async def test_websocket_reservation_runs_the_gate_before_reserving(monkeypatch):
    import app.modules.proxy._service.api_key_usage as api_key_usage_module

    calls: list[str | None] = []

    async def _gate(api_key, *, model):
        calls.append(model)
        raise TeamMemberSuspendedError("blocked")

    monkeypatch.setattr(api_key_usage_module, "check_member_gate", _gate)

    class _Proxy(api_key_usage_module._ApiKeyUsageMixin):
        def _repo_factory(self):  # pragma: no cover - must never run
            raise AssertionError("reservation must not be reached")

    with pytest.raises(TeamMemberSuspendedError):
        await _Proxy()._reserve_websocket_api_key_usage(
            _make_api_key("member-1"),
            request_model="model-beta",
            request_service_tier=None,
        )

    assert calls == ["model-beta"]

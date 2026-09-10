"""Dashboard-managed background job toggles (M2): resolver, settings-cache read and topology gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import background_jobs
from app.core.config.background_jobs import (
    BACKGROUND_JOB_SETTINGS,
    auth_guardian_blocked_by_topology,
    background_job_enabled,
    resolve_background_job_toggle,
)
from app.core.config.settings import Settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", BACKGROUND_JOB_SETTINGS)
def test_resolver_precedence_dashboard_over_env_over_default(name: str) -> None:
    env_off = SimpleNamespace(**{name: False})
    env_on = SimpleNamespace(**{name: True})

    # NULL column: the deprecated env alias decides, then the code default (True).
    assert resolve_background_job_toggle(SimpleNamespace(**{name: None}), name, startup_settings=env_off) is False
    assert resolve_background_job_toggle(SimpleNamespace(**{name: None}), name, startup_settings=env_on) is True
    # A missing attribute (older snapshot double) reads as NULL.
    assert resolve_background_job_toggle(SimpleNamespace(), name, startup_settings=env_on) is True
    assert resolve_background_job_toggle(None, name, startup_settings=SimpleNamespace()) is True
    # Dashboard False is a value, not "unset": it wins over an env True.
    assert resolve_background_job_toggle(SimpleNamespace(**{name: False}), name, startup_settings=env_on) is False
    assert resolve_background_job_toggle(SimpleNamespace(**{name: True}), name, startup_settings=env_off) is True


@pytest.mark.asyncio
async def test_background_job_enabled_reads_the_shared_settings_cache_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config.settings_cache as settings_cache_module

    reads = 0

    class _Cache:
        async def get(self):
            nonlocal reads
            reads += 1
            return SimpleNamespace(automations_scheduler_enabled=False, auth_guardian_enabled=None)

    monkeypatch.setattr(settings_cache_module, "get_settings_cache", lambda: _Cache())
    monkeypatch.setattr(background_jobs, "get_settings", lambda: SimpleNamespace(auth_guardian_enabled=True))

    assert await background_job_enabled("automations_scheduler_enabled") is False
    assert await background_job_enabled("auth_guardian_enabled") is True
    # One snapshot read per call: the caller invokes it once per cycle entry.
    assert reads == 2


def _settings(*, leader_election_enabled: bool, ring: list[str]) -> Settings:
    return Settings(
        _env_file=None,
        leader_election_enabled=leader_election_enabled,
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=ring,
    )


@pytest.mark.parametrize(
    ("leader_election_enabled", "ring", "blocked"),
    [
        pytest.param(False, [], False, id="single-replica"),
        pytest.param(False, ["pod-a"], False, id="single-member-ring"),
        pytest.param(True, ["pod-a", "pod-b"], False, id="multi-replica-with-election"),
        pytest.param(False, ["pod-a", "pod-b"], True, id="multi-replica-without-election"),
    ],
)
def test_auth_guardian_topology_gate_matrix(leader_election_enabled: bool, ring: list[str], blocked: bool) -> None:
    assert (
        auth_guardian_blocked_by_topology(_settings(leader_election_enabled=leader_election_enabled, ring=ring))
        is blocked
    )

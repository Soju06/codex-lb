"""Pin the test harness's background-loop seam.

The ambient app lifespan must never start a real background loop inside the
suite (SQLite lock flakes, no-op planner decision rows, public GitHub/npm
catalog lookups). tests/conftest.py achieves that by patching the
``build_*_scheduler`` seams in ``app.main`` — not by exporting
``CODEX_LB_*_ENABLED=false`` — so the seam has to be complete: every builder
the lifespan imports is either replaced with a no-op or explicitly listed here
as an always-on maintenance loop the suite tolerates.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

import app.main as main_module
from app.core.config.settings import Settings
from tests.conftest import BACKGROUND_LOOP_BUILDERS, _NoopScheduler

# Always-on database maintenance loops the suite deliberately leaves running:
# they only touch the isolated per-test database and several integration tests
# depend on their lifespan wiring. A new ``build_*_scheduler`` import in
# ``app.main`` must be added to exactly one of the two tuples.
LIVE_MAINTENANCE_LOOP_BUILDERS: tuple[str, ...] = (
    "build_api_key_limit_reset_scheduler",
    "build_api_key_last_used_flush_scheduler",
    "build_account_deletion_scheduler",
)

# ``CODEX_LB_*_ENABLED`` toggles the harness used to export as ``false``. They
# stay unset so ``Settings()`` keeps its production defaults inside the suite;
# most of them no longer exist as settings at all (constantize-core-tunables).
# The request-path usage refresh is neutralised by the
# ``_disable_request_path_usage_refresh`` fixture seam instead.
RETIRED_HARNESS_ENV_OVERRIDES: tuple[str, ...] = (
    "CODEX_LB_USAGE_REFRESH_ENABLED",
    "CODEX_LB_MODEL_REGISTRY_ENABLED",
    "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED",
    "CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED",
    "CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED",
    "CODEX_LB_AUTH_GUARDIAN_ENABLED",
    "CODEX_LB_LIVE_USAGE_INGESTION_ENABLED",
)


def _lifespan_scheduler_builders() -> set[str]:
    return {
        name
        for name, value in vars(main_module).items()
        if name.startswith("build_") and name.endswith("_scheduler") and callable(value)
    }


def test_background_loop_seam_is_explicit_and_complete() -> None:
    assert BACKGROUND_LOOP_BUILDERS == (
        "build_metadata_refresh_scheduler",
        "build_usage_refresh_scheduler",
        "build_model_refresh_scheduler",
        "build_sticky_session_cleanup_scheduler",
        "build_quota_planner_scheduler",
        "build_auth_guardian_scheduler",
        "build_automations_scheduler",
        "build_rate_limit_reset_credits_scheduler",
        "build_account_usage_rollup_scheduler",
        "build_data_retention_scheduler",
        "build_telemetry_scheduler",
    )
    patched = set(BACKGROUND_LOOP_BUILDERS)
    live = set(LIVE_MAINTENANCE_LOOP_BUILDERS)
    assert not patched & live
    assert _lifespan_scheduler_builders() == patched | live, (
        "app.main imports a build_*_scheduler that tests/conftest.py neither patches nor "
        "classifies as an always-on maintenance loop"
    )


def test_every_patched_builder_is_started_by_the_lifespan() -> None:
    source = inspect.getsource(main_module)
    for builder_name in BACKGROUND_LOOP_BUILDERS + LIVE_MAINTENANCE_LOOP_BUILDERS:
        assert f"{builder_name}()" in source, f"{builder_name} is imported but never built by the lifespan"


@pytest.mark.asyncio
async def test_autouse_fixture_replaces_every_background_loop_builder(
    _disable_background_loop_schedulers: tuple[str, ...],
) -> None:
    assert _disable_background_loop_schedulers == BACKGROUND_LOOP_BUILDERS
    for builder_name in BACKGROUND_LOOP_BUILDERS:
        scheduler = getattr(main_module, builder_name)()
        assert isinstance(scheduler, _NoopScheduler), builder_name
        assert await scheduler.start() is None
        assert await scheduler.stop() is None


def test_harness_does_not_export_background_loop_env_kill_switches() -> None:
    """The retired toggles must be unset in the whole test process, not just absent from conftest.

    The suite already relies on a clean process environment for these names
    (test_settings_multi_replica.py asserts the production default of
    ``auth_guardian_enabled``), so an inherited shell export is a harness
    misconfiguration and is reported as such rather than surfacing as an
    unrelated default assertion elsewhere.
    """
    exported = sorted(name for name in RETIRED_HARNESS_ENV_OVERRIDES if name in os.environ)
    assert exported == [], (
        "tests must disable loops through the fixture seam, not env; unset these in the shell "
        f"or the pytest launcher before running the suite: {exported}"
    )
    conftest_source = (Path(__file__).parents[1] / "conftest.py").read_text(encoding="utf-8")
    leaked = sorted(name for name in RETIRED_HARNESS_ENV_OVERRIDES if f'os.environ["{name}"]' in conftest_source)
    assert leaked == []


def test_usage_refresh_env_export_still_has_a_settings_field_behind_it() -> None:
    """Hand-off guard for the follow-up that constantizes ``usage_refresh_enabled``.

    tests/conftest.py still exports ``CODEX_LB_USAGE_REFRESH_ENABLED=false``
    because the field also gates request-path refreshes (``UsageUpdater.
    refresh_accounts`` on account import, ``request_refresh`` after a streamed
    ``usage_limit_reached``); without it every account-importing test spends
    20-30 s failing to reach ``example.invalid``. ``Settings`` ignores unknown
    env names, so the day the field is removed this export becomes a silent
    no-op and the suite slows ~30x with no failing test. Fail loudly instead.
    """
    conftest_source = (Path(__file__).parents[1] / "conftest.py").read_text(encoding="utf-8")
    if 'os.environ["CODEX_LB_USAGE_REFRESH_ENABLED"]' not in conftest_source:
        return
    assert "usage_refresh_enabled" in Settings.model_fields, (
        "usage_refresh_enabled was removed from Settings but tests/conftest.py still exports "
        "CODEX_LB_USAGE_REFRESH_ENABLED: replace the export with a request-path seam (autouse patch of "
        "UsageUpdater.refresh_accounts / request_refresh, or a stubbed usage client) before deleting the env line"
    )

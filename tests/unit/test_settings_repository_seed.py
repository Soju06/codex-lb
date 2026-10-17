from __future__ import annotations

import pytest

from app.core.config.settings import get_settings
from app.db.session import SessionLocal
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import SettingsService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_first_boot_seed_leaves_account_capacity_overrides_null(db_setup, monkeypatch) -> None:
    """A fresh settings row must inherit env caps, not freeze them as overrides."""
    del db_setup
    monkeypatch.setenv("CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT", "13")
    get_settings.cache_clear()
    try:
        async with SessionLocal() as session:
            row = await SettingsRepository(session).get_or_create()
            assert row.proxy_account_response_create_limit is None
            assert row.proxy_account_stream_limit is None
            assert row.proxy_account_stream_recovery_reserve is None
            assert row.proxy_api_key_fair_share_congestion_threshold_pct is None

            effective = await SettingsService(SettingsRepository(session)).get_settings()
            assert effective.proxy_account_stream_limit == 13
            assert effective.proxy_account_stream_limit_override is None

        # A later environment change is honoured without touching the row.
        monkeypatch.setenv("CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT", "21")
        get_settings.cache_clear()
        async with SessionLocal() as session:
            effective = await SettingsService(SettingsRepository(session)).get_settings()
            assert effective.proxy_account_stream_limit == 21
            assert effective.proxy_account_stream_limit_override is None
    finally:
        get_settings.cache_clear()

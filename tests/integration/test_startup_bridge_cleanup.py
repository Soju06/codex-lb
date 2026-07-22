from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import HttpBridgeSessionRecord, HttpBridgeSessionState
from app.db.session import SessionLocal
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_lifespan_startup_purges_abandoned_ownerless_bridge_rows(db_setup, monkeypatch) -> None:
    del db_setup

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", "startup-instance")
    get_settings.cache_clear()

    now = utcnow()
    stale_time = now - timedelta(hours=3)
    async with SessionLocal() as session:
        session.add_all(
            [
                HttpBridgeSessionRecord(
                    session_key_kind="session_header",
                    session_key_value="sid-stale-ownerless-startup",
                    session_key_hash="hash-stale-ownerless-startup",
                    api_key_scope="__anonymous__",
                    owner_instance_id=None,
                    owner_epoch=1,
                    lease_expires_at=stale_time,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=None,
                    model="gpt-5.4",
                    last_seen_at=stale_time,
                    closed_at=None,
                ),
                HttpBridgeSessionRecord(
                    session_key_kind="session_header",
                    session_key_value="sid-recent-ownerless-startup",
                    session_key_hash="hash-recent-ownerless-startup",
                    api_key_scope="__anonymous__",
                    owner_instance_id=None,
                    owner_epoch=1,
                    lease_expires_at=now + timedelta(minutes=5),
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=None,
                    model="gpt-5.4",
                    last_seen_at=now,
                    closed_at=None,
                ),
            ]
        )
        await session.commit()

    app = create_app()
    async with app.router.lifespan_context(app):
        async with SessionLocal() as session:
            remaining_keys = set(
                await session.scalars(
                    select(HttpBridgeSessionRecord.session_key_value).where(
                        HttpBridgeSessionRecord.session_key_value.in_(
                            [
                                "sid-stale-ownerless-startup",
                                "sid-recent-ownerless-startup",
                            ]
                        )
                    )
                )
            )

    assert remaining_keys == {"sid-recent-ownerless-startup"}

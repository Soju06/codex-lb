"""Explicit opt-in to real deletion work through the app lifespan."""

from __future__ import annotations

import asyncio

import pytest

import app.main as main_module
import app.modules.accounts.deletion as deletion_module
from app.db.models import Account
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from tests.integration.test_account_deletion_background import _seed_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_lifespan_can_opt_into_real_account_deletion(app_instance, monkeypatch):
    # The real builder publishes a wake target. Restore it after this test too.
    monkeypatch.setattr(deletion_module, "_scheduler", None)
    schedulers: list[deletion_module.AccountDeletionScheduler] = []

    def build_scheduler() -> deletion_module.AccountDeletionScheduler:
        scheduler = deletion_module.build_account_deletion_scheduler()
        schedulers.append(scheduler)
        return scheduler

    monkeypatch.setattr(main_module, "build_account_deletion_scheduler", build_scheduler)
    account_id = "acc_lifespan_deletion"
    await _seed_account(account_id, log_count=1, usage_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete(account_id)

    async with app_instance.router.lifespan_context(app_instance):
        assert len(schedulers) == 1
        async with asyncio.timeout(5):
            while True:
                async with SessionLocal() as session:
                    if await session.get(Account, account_id) is None:
                        break
                await asyncio.sleep(0.01)

    assert schedulers[0]._task is None

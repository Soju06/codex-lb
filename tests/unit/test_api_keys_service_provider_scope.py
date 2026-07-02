"""Unit tests for service-layer ``provider_scope`` round-trip.

The DB stores ``provider_scope`` as a CSV string. The service layer is
responsible for:

* writing CSV strings on create/update;
* reading CSV strings back to ``list[str]`` on read paths.

These tests use a fake repository so they exercise the service in isolation
from SQLAlchemy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.api_keys.repository import _UNSET
from app.modules.api_keys.service import (
    ApiKeysService,
    ApiKeyCreateData,
    ApiKeyUpdateData,
)


pytestmark = pytest.mark.unit


@dataclass
class _FakeRepo:
    rows: dict[str, Any] = field(default_factory=dict)

    async def create(self, row, *, commit: bool = True):
        self.rows[row.id] = row
        return await self.get_by_id(row.id)

    async def get_by_id(self, key_id: str):
        return self.rows.get(key_id)

    async def get_by_hash(self, key_hash: str):
        for row in self.rows.values():
            if getattr(row, "key_hash", None) == key_hash:
                return row
        return None

    async def list_all(self):
        return list(self.rows.values())

    async def list_usage_summary_by_key(self):
        return {}

    async def get_usage_summary_by_key_id(self, key_id: str):  # pragma: no cover
        raise NotImplementedError

    async def get_limit_usage_value(self, key_id: str, **kwargs):  # pragma: no cover
        return 0

    async def list_accounts_by_ids(self, account_ids):  # pragma: no cover
        return []

    async def list_all_accounts(self):  # pragma: no cover
        return []

    async def update(self, key_id, **kwargs):
        row = self.rows.get(key_id)
        if row is None:
            return None
        for name, value in kwargs.items():
            if value is _UNSET:
                continue
            setattr(row, name, value)
        return await self.get_by_id(key_id)

    async def delete(self, key_id):
        return self.rows.pop(key_id, None) is not None

    async def update_last_used(self, key_id, *, commit: bool = True):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def get_limits_by_key(self, key_id):
        return []

    async def replace_limits(self, key_id, limits, *, commit=True):
        return []

    async def upsert_limits(self, key_id, limits, *, commit=True):
        return []

    async def replace_account_assignments(self, key_id, account_ids, *, commit=True):
        pass

    async def increment_limit_usage(self, key_id, **kwargs):
        pass

    async def reset_limit(self, limit_id, **kwargs):  # pragma: no cover
        return True

    async def try_reserve_usage(self, *args, **kwargs):  # pragma: no cover
        return SimpleNamespace(success=True, limit_id=0, current_value=0, max_value=0, reset_at=None)

    async def adjust_reserved_usage(self, *args, **kwargs):  # pragma: no cover
        return True

    async def create_usage_reservation(self, reservation_id, **kwargs):
        pass

    async def get_usage_reservation(self, reservation_id):  # pragma: no cover
        return None

    async def transition_usage_reservation_status(self, *args, **kwargs):  # pragma: no cover
        return True

    async def upsert_reservation_item_actual(self, *args, **kwargs):
        pass

    async def settle_usage_reservation(self, *args, **kwargs):
        pass

    async def touch_usage_reservation(self, *args, **kwargs):  # pragma: no cover
        return True

    async def trends_by_key(self, *args, **kwargs):  # pragma: no cover
        return []

    async def usage_7d(self, *args, **kwargs):  # pragma: no cover
        return SimpleNamespace(
            total_tokens=0,
            total_cost_usd=0.0,
            total_requests=0,
            cached_input_tokens=0,
            account_costs=[],
        )


class _UnsetType:
    pass


@pytest.fixture()
def fake_repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture()
def service(fake_repo: _FakeRepo) -> ApiKeysService:
    return ApiKeysService(repository=fake_repo, usage_repository=None)


@pytest.mark.asyncio
async def test_create_key_persists_provider_scope_as_csv(service, fake_repo):
    """Service writes provider_scope as a sorted CSV string to the DB."""
    created = await service.create_key(
        ApiKeyCreateData(
            name="csv-write",
            allowed_models=None,
            provider_scope=["claude", "codex"],
        )
    )
    persisted = fake_repo.rows[created.id]
    assert persisted.provider_scope == "claude,codex"


@pytest.mark.asyncio
async def test_create_key_default_provider_scope_is_codex(service, fake_repo):
    """Omitted provider_scope defaults to ['codex'] → DB stores 'codex'."""
    created = await service.create_key(
        ApiKeyCreateData(
            name="default",
            allowed_models=None,
        )
    )
    persisted = fake_repo.rows[created.id]
    assert persisted.provider_scope == "codex"
    # ApiKeyData round-trips the scope back to list form.
    fetched = await service.get_key_by_id(created.id)
    assert fetched.provider_scope == ["codex"]


@pytest.mark.asyncio
async def test_update_key_persists_provider_scope_change(service, fake_repo):
    """PATCH updates provider_scope on the underlying row."""
    created = await service.create_key(
        ApiKeyCreateData(
            name="updatable",
            allowed_models=None,
        )
    )
    updated = await service.update_key(
        created.id,
        ApiKeyUpdateData(
            provider_scope=["claude"],
            provider_scope_set=True,
        ),
    )
    assert updated.provider_scope == ["claude"]
    persisted = fake_repo.rows[created.id]
    assert persisted.provider_scope == "claude"


@pytest.mark.asyncio
async def test_get_key_reads_provider_scope_from_csv(service, fake_repo):
    """Read path converts CSV → list."""
    created = await service.create_key(
        ApiKeyCreateData(
            name="round-trip",
            allowed_models=None,
            provider_scope=["codex", "claude"],
        )
    )
    fetched = await service.get_key_by_id(created.id)
    assert fetched.provider_scope == ["claude", "codex"]
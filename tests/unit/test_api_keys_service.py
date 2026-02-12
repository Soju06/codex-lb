from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import ApiKey
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
)

pytestmark = pytest.mark.unit


class _FakeApiKeysRepository:
    def __init__(self) -> None:
        self.rows: dict[str, ApiKey] = {}

    async def create(self, row: ApiKey) -> ApiKey:
        self.rows[row.id] = row
        return row

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        return self.rows.get(key_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for row in self.rows.values():
            if row.key_hash == key_hash:
                return row
        return None

    async def list_all(self) -> list[ApiKey]:
        return sorted(self.rows.values(), key=lambda row: row.created_at, reverse=True)

    async def update(self, key_id: str, **kwargs) -> ApiKey | None:
        row = self.rows.get(key_id)
        if row is None:
            return None
        for field, value in kwargs.items():
            setattr(row, field, value)
        return row

    async def delete(self, key_id: str) -> bool:
        if key_id not in self.rows:
            return False
        self.rows.pop(key_id)
        return True

    async def increment_weekly_usage(self, key_id: str, token_count: int) -> None:
        row = self.rows[key_id]
        row.weekly_tokens_used += token_count
        row.last_used_at = utcnow()

    async def reset_weekly_usage(self, key_id: str, *, expected_reset_at, new_reset_at) -> bool:
        row = self.rows.get(key_id)
        if row is None:
            return False
        if row.weekly_reset_at != expected_reset_at:
            return False
        row.weekly_tokens_used = 0
        row.weekly_reset_at = new_reset_at
        return True


@pytest.mark.asyncio
async def test_create_key_stores_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="dev-key",
            allowed_models=["o3-pro"],
            weekly_token_limit=1_000_000,
            expires_at=None,
        )
    )

    assert created.key.startswith("sk-clb-")
    assert created.key_prefix == created.key[:15]
    assert created.allowed_models == ["o3-pro"]

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.key_hash != created.key
    assert stored.key_prefix == created.key[:15]


@pytest.mark.asyncio
async def test_validate_key_checks_expiry_limit_and_weekly_reset() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="limited-key",
            allowed_models=None,
            weekly_token_limit=10,
            expires_at=None,
        )
    )

    row = await repo.get_by_id(created.id)
    assert row is not None
    row.weekly_tokens_used = 10
    row.weekly_reset_at = utcnow() + timedelta(days=1)
    with pytest.raises(ApiKeyRateLimitExceededError):
        await service.validate_key(created.key)

    row.weekly_tokens_used = 5
    row.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(ApiKeyInvalidError):
        await service.validate_key(created.key)

    row.expires_at = None
    row.weekly_tokens_used = 9
    row.weekly_reset_at = utcnow() - timedelta(days=8)
    validated = await service.validate_key(created.key)
    assert validated.id == created.id
    assert row.weekly_tokens_used == 0
    assert row.weekly_reset_at > utcnow()


@pytest.mark.asyncio
async def test_regenerate_key_rotates_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(name="regen-key", allowed_models=None, weekly_token_limit=None, expires_at=None)
    )

    row_before = await repo.get_by_id(created.id)
    assert row_before is not None
    old_hash = row_before.key_hash
    old_prefix = row_before.key_prefix

    regenerated = await service.regenerate_key(created.id)
    row_after = await repo.get_by_id(created.id)
    assert row_after is not None

    assert regenerated.key.startswith("sk-clb-")
    assert row_after.key_hash != old_hash
    assert row_after.key_prefix != old_prefix

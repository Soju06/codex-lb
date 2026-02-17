from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import ApiKey, ApiKeyLimit, LimitType
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    LimitRuleInput,
)

pytestmark = pytest.mark.unit


class _FakeApiKeysRepository:
    def __init__(self) -> None:
        self.rows: dict[str, ApiKey] = {}
        self._limits: dict[str, list[ApiKeyLimit]] = {}
        self._limit_id_seq = 0

    async def create(self, row: ApiKey) -> ApiKey:
        self.rows[row.id] = row
        row.limits = []
        return row

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = self._limits.get(key_id, [])
        return row

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for row in self.rows.values():
            if row.key_hash == key_hash:
                row.limits = self._limits.get(row.id, [])
                return row
        return None

    async def list_all(self) -> list[ApiKey]:
        result = sorted(self.rows.values(), key=lambda row: row.created_at, reverse=True)
        for row in result:
            row.limits = self._limits.get(row.id, [])
        return result

    async def update(self, key_id: str, **kwargs: object) -> ApiKey | None:
        row = self.rows.get(key_id)
        if row is None:
            return None
        for field, value in kwargs.items():
            setattr(row, field, value)
        row.limits = self._limits.get(key_id, [])
        return row

    async def delete(self, key_id: str) -> bool:
        if key_id not in self.rows:
            return False
        self.rows.pop(key_id)
        self._limits.pop(key_id, None)
        return True

    async def update_last_used(self, key_id: str) -> None:
        row = self.rows.get(key_id)
        if row is not None:
            row.last_used_at = utcnow()

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]:
        return list(self._limits.get(key_id, []))

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        for limit in limits:
            self._limit_id_seq += 1
            limit.id = self._limit_id_seq
            limit.api_key_id = key_id
        self._limits[key_id] = list(limits)
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = self._limits[key_id]
        return self._limits[key_id]

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None:
        limits = self._limits.get(key_id, [])
        for limit in limits:
            if limit.model_filter is not None and limit.model_filter != model:
                continue
            increment = _compute_increment(limit, input_tokens, output_tokens, cost_microdollars)
            if increment > 0:
                limit.current_value += increment
        row = self.rows.get(key_id)
        if row is not None:
            row.last_used_at = utcnow()

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        for limits in self._limits.values():
            for limit in limits:
                if limit.id == limit_id and limit.reset_at == expected_reset_at:
                    limit.current_value = 0
                    limit.reset_at = new_reset_at
                    return True
        return False


def _compute_increment(limit: ApiKeyLimit, input_tokens: int, output_tokens: int, cost_microdollars: int) -> int:
    if limit.limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit.limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit.limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit.limit_type == LimitType.COST_USD:
        return cost_microdollars
    return 0


@pytest.mark.asyncio
async def test_create_key_stores_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="dev-key",
            allowed_models=["o3-pro"],
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
async def test_create_key_with_limits() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="limited-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=5_000_000),
            ],
        )
    )

    assert len(created.limits) == 2
    token_limit = next(lim for lim in created.limits if lim.limit_type == "total_tokens")
    cost_limit = next(lim for lim in created.limits if lim.limit_type == "cost_usd")
    assert token_limit.max_value == 1_000_000
    assert token_limit.limit_window == "weekly"
    assert token_limit.current_value == 0
    assert cost_limit.max_value == 5_000_000
    assert cost_limit.limit_window == "daily"


@pytest.mark.asyncio
async def test_validate_key_checks_expiry_and_limit() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="limited-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )

    # Exceed the limit
    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 10
    limits[0].reset_at = utcnow() + timedelta(days=1)
    with pytest.raises(ApiKeyRateLimitExceededError):
        await service.validate_key(created.key)

    # Test expiry
    limits[0].current_value = 5
    row = await repo.get_by_id(created.id)
    assert row is not None
    row.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(ApiKeyInvalidError):
        await service.validate_key(created.key)


@pytest.mark.asyncio
async def test_validate_key_lazy_resets_expired_limit() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reset-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )

    # Set limit as expired
    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 9
    limits[0].reset_at = utcnow() - timedelta(days=8)

    validated = await service.validate_key(created.key)
    assert validated.id == created.id

    # Verify lazy reset occurred
    updated_limits = await repo.get_limits_by_key(created.id)
    assert updated_limits[0].current_value == 0
    assert updated_limits[0].reset_at > utcnow()


@pytest.mark.asyncio
async def test_validate_key_advances_reset_strictly_into_future(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="boundary-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )
    fixed_now = utcnow()
    monkeypatch.setattr("app.modules.api_keys.service.utcnow", lambda: fixed_now)

    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 7
    limits[0].reset_at = fixed_now - timedelta(days=14)

    await service.validate_key(created.key)

    updated_limits = await repo.get_limits_by_key(created.id)
    assert updated_limits[0].current_value == 0
    assert updated_limits[0].reset_at == fixed_now + timedelta(days=7)


@pytest.mark.asyncio
async def test_validate_key_multi_limit_all_must_pass() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="multi-limit-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
                LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=5_000_000),
            ],
        )
    )

    limits = await repo.get_limits_by_key(created.id)
    token_limit = next(lim for lim in limits if lim.limit_type == LimitType.TOTAL_TOKENS)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)

    # Token within range, cost exceeded → should fail
    token_limit.current_value = 50
    cost_limit.current_value = 5_000_000
    token_limit.reset_at = utcnow() + timedelta(days=1)
    cost_limit.reset_at = utcnow() + timedelta(days=1)

    with pytest.raises(ApiKeyRateLimitExceededError) as exc_info:
        await service.validate_key(created.key)
    assert "cost_usd" in str(exc_info.value)


@pytest.mark.asyncio
async def test_regenerate_key_rotates_hash_and_prefix() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(ApiKeyCreateData(name="regen-key", allowed_models=None, expires_at=None))

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


@pytest.mark.asyncio
async def test_record_usage_increments_matching_limits() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="usage-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=500_000),
                LimitRuleInput(limit_type="output_tokens", limit_window="weekly", max_value=500_000),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        cached_input_tokens=20,
    )

    limits = await repo.get_limits_by_key(created.id)
    total_limit = next(lim for lim in limits if lim.limit_type == LimitType.TOTAL_TOKENS)
    input_limit = next(lim for lim in limits if lim.limit_type == LimitType.INPUT_TOKENS)
    output_limit = next(lim for lim in limits if lim.limit_type == LimitType.OUTPUT_TOKENS)

    assert total_limit.current_value == 150  # input + output
    assert input_limit.current_value == 100
    assert output_limit.current_value == 50


@pytest.mark.asyncio
async def test_record_usage_model_filter_matching() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="model-filter-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(
                    limit_type="total_tokens", limit_window="weekly", max_value=500_000, model_filter="gpt-5.1"
                ),
            ],
        )
    )

    # Record usage for gpt-5.1 → both limits should increment
    await service.record_usage(
        created.id,
        model="gpt-5.1",
        input_tokens=100,
        output_tokens=50,
    )

    limits = await repo.get_limits_by_key(created.id)
    global_limit = next(lim for lim in limits if lim.model_filter is None)
    model_limit = next(lim for lim in limits if lim.model_filter == "gpt-5.1")
    assert global_limit.current_value == 150
    assert model_limit.current_value == 150

    # Record usage for different model → only global limit increments
    await service.record_usage(
        created.id,
        model="gpt-4o-mini",
        input_tokens=200,
        output_tokens=100,
    )

    limits = await repo.get_limits_by_key(created.id)
    global_limit = next(lim for lim in limits if lim.model_filter is None)
    model_limit = next(lim for lim in limits if lim.model_filter == "gpt-5.1")
    assert global_limit.current_value == 450  # 150 + 300
    assert model_limit.current_value == 150  # unchanged

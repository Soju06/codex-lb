from datetime import timedelta

import pytest

from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeyRateLimitExceededError,
    ApiKeysService,
    LimitRuleInput,
)


@pytest.mark.asyncio
async def test_limit_enforcement_with_pre_exceeded_value():
    """
    Test that limit enforcement works correctly when current_value already exceeds max_value.
    This tests the fix for the bug where stale limit data was used after reset.
    """
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        service = ApiKeysService(repo)

        # Create API key with limit of 1
        created = await service.create_key(
            ApiKeyCreateData(
                name="test-key",
                allowed_models=None,
                limits=[
                    LimitRuleInput(
                        limit_type="total_tokens",
                        limit_window="weekly",
                        max_value=1,
                    ),
                ],
            )
        )

        # Manually set current_value to 1 (exceeding the limit)
        limits = await repo.get_limits_by_key(created.id)
        assert len(limits) == 1
        limits[0].current_value = 1
        await session.commit()

        # Verify the limit is enforced
        with pytest.raises(ApiKeyRateLimitExceededError) as exc_info:
            await service.enforce_limits_for_request(
                created.id,
                request_model="gpt-5",
            )

        assert "limit exceeded" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_cost_limit_enforcement_with_pre_exceeded_value():
    """
    Test that cost limit enforcement works correctly when current_value already exceeds max_value.
    This tests the same bug fix applies to both token and cost limits.
    """
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        service = ApiKeysService(repo)

        # Create API key with cost limit of 10_000_000 microdollars ($0.01)
        created = await service.create_key(
            ApiKeyCreateData(
                name="test-key",
                allowed_models=None,
                limits=[
                    LimitRuleInput(
                        limit_type="cost_usd",
                        limit_window="weekly",
                        max_value=10_000_000,
                    ),
                ],
            )
        )

        # Manually set current_value to 10_000_000 (exceeding the limit)
        limits = await repo.get_limits_by_key(created.id)
        assert len(limits) == 1
        limits[0].current_value = 10_000_000
        await session.commit()

        # Verify the limit is enforced
        with pytest.raises(ApiKeyRateLimitExceededError) as exc_info:
            await service.enforce_limits_for_request(
                created.id,
                request_model="gpt-5",
                request_service_tier="auto",
            )

        assert "limit exceeded" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_limit_reset_allows_requests_after_expiration():
    """
    Test that when a limit's reset_at has passed, the limit is properly reset
    and new requests are allowed.
    """
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        service = ApiKeysService(repo)

        from app.core.utils.time import utcnow

        now = utcnow()

        # Create API key with expired reset time
        created = await service.create_key(
            ApiKeyCreateData(
                name="test-key",
                allowed_models=None,
                limits=[
                    LimitRuleInput(
                        limit_type="total_tokens",
                        limit_window="weekly",
                        max_value=1,
                    ),
                ],
            )
        )

        # Manually set current_value to 1 and reset_at to past
        limits = await repo.get_limits_by_key(created.id)
        assert len(limits) == 1
        limits[0].current_value = 1
        limits[0].reset_at = now - timedelta(days=1)  # Expired
        await session.commit()

        # This should NOT raise - limit should be reset automatically
        reservation = await service.enforce_limits_for_request(
            created.id,
            request_model="gpt-5",
        )

        assert reservation is not None
        assert reservation.key_id == created.id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

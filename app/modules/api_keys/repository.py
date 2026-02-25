from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.utils.time import utcnow
from app.db.models import (
    ApiKey,
    ApiKeyLimit,
    ApiKeyUsageReservation,
    ApiKeyUsageReservationItem,
    LimitType,
    LimitWindow,
)


@dataclass(frozen=True, slots=True)
class ReservationResult:
    success: bool
    limit_id: int
    current_value: int | None
    max_value: int | None
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class UsageReservationItemData:
    limit_id: int
    limit_type: LimitType
    reserved_delta: int
    expected_reset_at: datetime
    actual_delta: int | None = None


@dataclass(frozen=True, slots=True)
class UsageReservationData:
    reservation_id: str
    api_key_id: str
    model: str
    status: str
    items: list[UsageReservationItemData]


class _Unset(Enum):
    UNSET = "UNSET"


_UNSET = _Unset.UNSET


class ApiKeysRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, row: ApiKey) -> ApiKey:
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, key_id: str, *, owner_user_id: str | None = None) -> ApiKey | None:
        row = await self._session.get(ApiKey, key_id)
        if row is None:
            return None
        if owner_user_id is not None and row.owner_user_id != owner_user_id:
            return None
        return row

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey).options(selectinload(ApiKey.limits)).where(ApiKey.key_hash == key_hash)
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, owner_user_id: str | None = None) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
        if owner_user_id is not None:
            stmt = stmt.where(ApiKey.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def update(
        self,
        key_id: str,
        *,
        owner_user_id: str | _Unset = _UNSET,
        name: str | _Unset = _UNSET,
        allowed_models: str | None | _Unset = _UNSET,
        expires_at: datetime | None | _Unset = _UNSET,
        is_active: bool | _Unset = _UNSET,
        key_hash: str | _Unset = _UNSET,
        key_prefix: str | _Unset = _UNSET,
        owner_scope_user_id: str | None = None,
    ) -> ApiKey | None:
        row = await self.get_by_id(key_id, owner_user_id=owner_scope_user_id)
        if row is None:
            return None
        if owner_user_id is not _UNSET:
            assert owner_user_id is None or isinstance(owner_user_id, str)
            row.owner_user_id = owner_user_id
        if name is not _UNSET:
            assert isinstance(name, str)
            row.name = name
        if allowed_models is not _UNSET:
            assert allowed_models is None or isinstance(allowed_models, str)
            row.allowed_models = allowed_models
        if expires_at is not _UNSET:
            assert expires_at is None or isinstance(expires_at, datetime)
            row.expires_at = expires_at
        if is_active is not _UNSET:
            assert isinstance(is_active, bool)
            row.is_active = is_active
        if key_hash is not _UNSET:
            assert isinstance(key_hash, str)
            row.key_hash = key_hash
        if key_prefix is not _UNSET:
            assert isinstance(key_prefix, str)
            row.key_prefix = key_prefix
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, key_id: str, *, owner_scope_user_id: str | None = None) -> bool:
        row = await self.get_by_id(key_id, owner_user_id=owner_scope_user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def update_last_used(self, key_id: str) -> None:
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        await self._session.commit()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # ── Limit operations ──

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]:
        result = await self._session.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id))
        return list(result.scalars().all())

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        existing = await self.get_limits_by_key(key_id)
        for limit in existing:
            await self._session.delete(limit)
        for limit in limits:
            limit.api_key_id = key_id
            self._session.add(limit)
        await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["limits"])
        return await self.get_limits_by_key(key_id)

    async def upsert_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        existing = await self.get_limits_by_key(key_id)
        existing_by_key = {_limit_key(limit): limit for limit in existing}
        incoming_keys = {_limit_key(limit) for limit in limits}

        for incoming in limits:
            key = _limit_key(incoming)
            matched = existing_by_key.get(key)
            if matched is None:
                incoming.api_key_id = key_id
                self._session.add(incoming)
                continue
            matched.max_value = incoming.max_value
            matched.current_value = incoming.current_value
            matched.reset_at = incoming.reset_at

        for old_limit in existing:
            if _limit_key(old_limit) not in incoming_keys:
                await self._session.delete(old_limit)

        await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["limits"])
        return await self.get_limits_by_key(key_id)

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None:
        limits = await self.get_limits_by_key(key_id)
        for limit in limits:
            if limit.model_filter is not None and limit.model_filter != model:
                continue
            increment = _compute_increment(limit, input_tokens, output_tokens, cost_microdollars)
            if increment > 0:
                await self._session.execute(
                    update(ApiKeyLimit)
                    .where(ApiKeyLimit.id == limit.id)
                    .values(current_value=ApiKeyLimit.current_value + increment)
                )
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        await self._session.commit()

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        result = await self._session.execute(
            update(ApiKeyLimit)
            .where(ApiKeyLimit.id == limit_id)
            .where(ApiKeyLimit.reset_at == expected_reset_at)
            .values(current_value=0, reset_at=new_reset_at)
            .returning(ApiKeyLimit.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def try_reserve_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> ReservationResult:
        if delta <= 0:
            snapshot = await self._session.get(ApiKeyLimit, limit_id)
            return ReservationResult(
                success=True,
                limit_id=limit_id,
                current_value=snapshot.current_value if snapshot is not None else None,
                max_value=snapshot.max_value if snapshot is not None else None,
                reset_at=snapshot.reset_at if snapshot is not None else None,
            )

        result = await self._session.execute(
            update(ApiKeyLimit)
            .where(ApiKeyLimit.id == limit_id)
            .where(ApiKeyLimit.reset_at == expected_reset_at)
            .where(ApiKeyLimit.current_value + delta <= ApiKeyLimit.max_value)
            .values(current_value=ApiKeyLimit.current_value + delta)
            .returning(
                ApiKeyLimit.id,
                ApiKeyLimit.current_value,
                ApiKeyLimit.max_value,
                ApiKeyLimit.reset_at,
            )
        )
        row = result.first()
        if row is not None:
            return ReservationResult(
                success=True,
                limit_id=int(row.id),
                current_value=int(row.current_value),
                max_value=int(row.max_value),
                reset_at=row.reset_at,
            )

        snapshot_result = await self._session.execute(
            select(
                ApiKeyLimit.current_value,
                ApiKeyLimit.max_value,
                ApiKeyLimit.reset_at,
            ).where(ApiKeyLimit.id == limit_id)
        )
        snapshot = snapshot_result.first()
        return ReservationResult(
            success=False,
            limit_id=limit_id,
            current_value=int(snapshot.current_value) if snapshot is not None else None,
            max_value=int(snapshot.max_value) if snapshot is not None else None,
            reset_at=snapshot.reset_at if snapshot is not None else None,
        )

    async def adjust_reserved_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> bool:
        stmt = update(ApiKeyLimit).where(ApiKeyLimit.id == limit_id).where(ApiKeyLimit.reset_at == expected_reset_at)
        if delta < 0:
            stmt = stmt.where(ApiKeyLimit.current_value >= -delta)
        result = await self._session.execute(
            stmt.values(current_value=ApiKeyLimit.current_value + delta).returning(ApiKeyLimit.id)
        )
        return result.scalar_one_or_none() is not None

    async def create_usage_reservation(
        self,
        reservation_id: str,
        *,
        key_id: str,
        model: str,
        items: list[UsageReservationItemData],
    ) -> None:
        reservation = ApiKeyUsageReservation(
            id=reservation_id,
            api_key_id=key_id,
            model=model,
            status="reserved",
        )
        self._session.add(reservation)
        for item in items:
            self._session.add(
                ApiKeyUsageReservationItem(
                    reservation_id=reservation_id,
                    limit_id=item.limit_id,
                    limit_type=item.limit_type.value,
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                )
            )

    async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None:
        result = await self._session.execute(
            select(ApiKeyUsageReservation)
            .options(selectinload(ApiKeyUsageReservation.items))
            .where(ApiKeyUsageReservation.id == reservation_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return UsageReservationData(
            reservation_id=row.id,
            api_key_id=row.api_key_id,
            model=row.model,
            status=row.status,
            items=[
                UsageReservationItemData(
                    limit_id=item.limit_id,
                    limit_type=LimitType(item.limit_type),
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                    actual_delta=item.actual_delta,
                )
                for item in row.items
            ],
        )

    async def transition_usage_reservation_status(
        self,
        reservation_id: str,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool:
        result = await self._session.execute(
            update(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.id == reservation_id)
            .where(ApiKeyUsageReservation.status == expected_status)
            .values(status=new_status)
            .returning(ApiKeyUsageReservation.id)
        )
        return result.scalar_one_or_none() is not None

    async def upsert_reservation_item_actual(
        self,
        reservation_id: str,
        *,
        item: UsageReservationItemData,
        actual_delta: int,
    ) -> None:
        bind = self._session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else "sqlite"
        if dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(ApiKeyUsageReservationItem).values(
                reservation_id=reservation_id,
                limit_id=item.limit_id,
                limit_type=item.limit_type.value,
                reserved_delta=item.reserved_delta,
                expected_reset_at=item.expected_reset_at,
                actual_delta=actual_delta,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    ApiKeyUsageReservationItem.reservation_id,
                    ApiKeyUsageReservationItem.limit_id,
                ],
                set_={
                    "actual_delta": actual_delta,
                    "updated_at": utcnow(),
                },
            )
            await self._session.execute(stmt)
            return
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as postgresql_insert

            stmt = postgresql_insert(ApiKeyUsageReservationItem).values(
                reservation_id=reservation_id,
                limit_id=item.limit_id,
                limit_type=item.limit_type.value,
                reserved_delta=item.reserved_delta,
                expected_reset_at=item.expected_reset_at,
                actual_delta=actual_delta,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    ApiKeyUsageReservationItem.reservation_id,
                    ApiKeyUsageReservationItem.limit_id,
                ],
                set_={
                    "actual_delta": actual_delta,
                    "updated_at": utcnow(),
                },
            )
            await self._session.execute(stmt)
            return
        await self._session.execute(
            update(ApiKeyUsageReservationItem)
            .where(ApiKeyUsageReservationItem.reservation_id == reservation_id)
            .where(ApiKeyUsageReservationItem.limit_id == item.limit_id)
            .values(actual_delta=actual_delta)
        )

    async def settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_microdollars: int | None,
    ) -> None:
        await self._session.execute(
            update(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.id == reservation_id)
            .values(
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cost_microdollars=cost_microdollars,
            )
        )


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


def _limit_key(limit: ApiKeyLimit) -> tuple[LimitType, LimitWindow, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)

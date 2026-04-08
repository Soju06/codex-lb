from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountStatus, OpenAIPlatformIdentity
from app.modules.upstream_identities.types import PlatformRouteFamily


@dataclass(frozen=True, slots=True)
class OpenAIPlatformIdentityCreate:
    id: str
    label: str
    api_key_encrypted: bytes
    organization_id: str | None
    project_id: str | None
    eligible_route_families: tuple[PlatformRouteFamily, ...]
    status: AccountStatus
    last_validated_at: datetime | None
    last_auth_failure_reason: str | None
    deactivation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OpenAIPlatformIdentityUpdate:
    label: str
    api_key_encrypted: bytes
    organization_id: str | None
    project_id: str | None
    eligible_route_families: tuple[PlatformRouteFamily, ...]
    status: AccountStatus
    last_validated_at: datetime | None
    last_auth_failure_reason: str | None
    deactivation_reason: str | None = None


class OpenAIPlatformIdentityConflictError(ValueError):
    pass


class OpenAIPlatformIdentitiesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, identity_id: str) -> OpenAIPlatformIdentity | None:
        return await self._session.get(OpenAIPlatformIdentity, identity_id)

    async def list_identities(self) -> list[OpenAIPlatformIdentity]:
        result = await self._session.execute(select(OpenAIPlatformIdentity).order_by(OpenAIPlatformIdentity.label))
        return list(result.scalars().all())

    async def list_eligible_identities(
        self,
        route_family: PlatformRouteFamily,
    ) -> list[OpenAIPlatformIdentity]:
        identities = await self.list_identities()
        return [
            identity
            for identity in identities
            if identity.status not in (AccountStatus.PAUSED, AccountStatus.DEACTIVATED)
            and route_family in _split_route_families(identity.eligible_route_families)
        ]

    async def create_identity(self, create: OpenAIPlatformIdentityCreate) -> OpenAIPlatformIdentity:
        await self._acquire_singleton_lock()
        if await self._has_any_identity():
            await self._session.rollback()
            raise OpenAIPlatformIdentityConflictError("Only one OpenAI Platform fallback key can be registered.")

        identity = OpenAIPlatformIdentity(
            id=create.id,
            label=create.label,
            api_key_encrypted=create.api_key_encrypted,
            organization_id=create.organization_id,
            project_id=create.project_id,
            eligible_route_families=_join_route_families(create.eligible_route_families),
            status=create.status,
            last_validated_at=create.last_validated_at,
            last_auth_failure_reason=create.last_auth_failure_reason,
            deactivation_reason=create.deactivation_reason,
        )
        self._session.add(identity)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise OpenAIPlatformIdentityConflictError(
                "Only one OpenAI Platform fallback key can be registered."
            ) from exc
        await self._session.refresh(identity)
        return identity

    async def update_status(
        self,
        identity_id: str,
        status: AccountStatus,
        *,
        deactivation_reason: str | None = None,
    ) -> bool:
        result = await self._session.execute(
            update(OpenAIPlatformIdentity)
            .where(OpenAIPlatformIdentity.id == identity_id)
            .values(status=status, deactivation_reason=deactivation_reason)
            .returning(OpenAIPlatformIdentity.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_validation_state(
        self,
        identity_id: str,
        *,
        last_validated_at: datetime | None,
        last_auth_failure_reason: str | None,
        status: AccountStatus | None = None,
    ) -> bool:
        values: dict[str, object] = {
            "last_validated_at": last_validated_at,
            "last_auth_failure_reason": last_auth_failure_reason,
        }
        if status is not None:
            values["status"] = status
        result = await self._session.execute(
            update(OpenAIPlatformIdentity)
            .where(OpenAIPlatformIdentity.id == identity_id)
            .values(**values)
            .returning(OpenAIPlatformIdentity.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_identity(
        self,
        identity_id: str,
        update_payload: OpenAIPlatformIdentityUpdate,
    ) -> OpenAIPlatformIdentity | None:
        result = await self._session.execute(
            update(OpenAIPlatformIdentity)
            .where(OpenAIPlatformIdentity.id == identity_id)
            .values(
                label=update_payload.label,
                api_key_encrypted=update_payload.api_key_encrypted,
                organization_id=update_payload.organization_id,
                project_id=update_payload.project_id,
                eligible_route_families=_join_route_families(update_payload.eligible_route_families),
                status=update_payload.status,
                last_validated_at=update_payload.last_validated_at,
                last_auth_failure_reason=update_payload.last_auth_failure_reason,
                deactivation_reason=update_payload.deactivation_reason,
            )
            .returning(OpenAIPlatformIdentity.id)
        )
        updated_identity_id = result.scalar_one_or_none()
        await self._session.commit()
        if updated_identity_id is None:
            return None
        return await self.get_by_id(identity_id)

    async def delete(self, identity_id: str) -> bool:
        result = await self._session.execute(
            delete(OpenAIPlatformIdentity)
            .where(OpenAIPlatformIdentity.id == identity_id)
            .returning(OpenAIPlatformIdentity.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def _has_any_identity(self) -> bool:
        result = await self._session.execute(select(OpenAIPlatformIdentity.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def _acquire_singleton_lock(self) -> None:
        dialect_name = self._session.get_bind().dialect.name
        if dialect_name == "sqlite":
            await self._acquire_sqlite_singleton_lock()
            return
        if dialect_name == "postgresql":
            await self._acquire_postgresql_singleton_lock()

    async def _acquire_sqlite_singleton_lock(self) -> None:
        try:
            await self._session.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as exc:
            message = str(exc).lower()
            if "within a transaction" not in message:
                raise
            await self._session.execute(text("UPDATE openai_platform_identities SET id = id WHERE 1 = 0"))

    async def _acquire_postgresql_singleton_lock(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _advisory_lock_key("openai-platform-identity", "singleton")},
        )


def split_route_families(value: str | None) -> tuple[str, ...]:
    return _split_route_families(value)


def normalize_route_families(values: Iterable[str]) -> tuple[PlatformRouteFamily, ...]:
    normalized = sorted({value.strip() for value in values if value.strip()})
    return tuple(normalized)  # type: ignore[return-value]


def _split_route_families(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part for part in (item.strip() for item in value.split(",")) if part)


def _join_route_families(values: Iterable[str]) -> str:
    return ",".join(sorted({value.strip() for value in values if value.strip()}))


def _advisory_lock_key(scope: str, value: str) -> int:
    digest = hashlib.sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)

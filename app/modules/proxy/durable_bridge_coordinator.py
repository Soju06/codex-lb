from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HttpBridgeSessionState
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeRepository,
    DurableBridgeSessionSnapshot,
    durable_bridge_api_key_scope,
)

_DURABLE_TURN_STATE_ALIAS = "turn_state"
_DURABLE_PREVIOUS_RESPONSE_ALIAS = "previous_response_id"
_DURABLE_SESSION_HEADER_ALIAS = "session_header"


@dataclass(frozen=True, slots=True)
class DurableBridgeLookup:
    session_id: str
    canonical_kind: str
    canonical_key: str
    api_key_scope: str
    owner_instance_id: str | None
    owner_epoch: int
    lease_expires_at: datetime | None
    state: HttpBridgeSessionState
    latest_turn_state: str | None
    latest_response_id: str | None

    def lease_is_active(self, *, now: datetime) -> bool:
        if self.owner_instance_id is None:
            return False
        if self.lease_expires_at is None:
            return False
        return self.lease_expires_at > now


class DurableBridgeSessionCoordinator:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def lookup_request_targets(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        turn_state: str | None,
        session_header: str | None,
        previous_response_id: str | None,
    ) -> DurableBridgeLookup | None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            for alias_kind, alias_value in (
                (_DURABLE_TURN_STATE_ALIAS, turn_state),
                (_DURABLE_PREVIOUS_RESPONSE_ALIAS, previous_response_id),
                (_DURABLE_SESSION_HEADER_ALIAS, session_header),
            ):
                if alias_value is None:
                    continue
                snapshot = await repository.resolve_alias(
                    alias_kind=alias_kind,
                    alias_value=alias_value,
                    api_key_scope=api_key_scope,
                )
                if snapshot is not None:
                    return _to_lookup(snapshot)
            snapshot = await repository.get_session(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=api_key_scope,
            )
            if snapshot is None:
                return None
            return _to_lookup(snapshot)

    async def claim_live_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_id: str | None,
        instance_id: str,
        lease_ttl_seconds: float,
        account_id: str | None,
        model: str | None,
        service_tier: str | None,
        latest_turn_state: str | None,
        latest_response_id: str | None,
        allow_takeover: bool,
    ) -> DurableBridgeLookup:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).claim_session(
                session_key_kind=session_key_kind,
                session_key_value=session_key_value,
                api_key_scope=api_key_scope,
                instance_id=instance_id,
                lease_ttl_seconds=lease_ttl_seconds,
                account_id=account_id,
                model=model,
                service_tier=service_tier,
                latest_turn_state=latest_turn_state,
                latest_response_id=latest_response_id,
                allow_takeover=allow_takeover,
            )
        return _to_lookup(snapshot)

    async def renew_live_session(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        state: HttpBridgeSessionState | None = None,
    ) -> DurableBridgeLookup | None:
        del api_key_id
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).renew_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_turn_state=latest_turn_state,
                latest_response_id=latest_response_id,
                state=state,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def release_live_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeLookup | None:
        async with self._session() as session:
            snapshot = await DurableBridgeRepository(session).release_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                draining=draining,
            )
        if snapshot is None:
            return None
        return _to_lookup(snapshot)

    async def mark_instance_draining(self, *, instance_id: str) -> int:
        async with self._session() as session:
            return await DurableBridgeRepository(session).mark_owner_draining(instance_id=instance_id)

    async def register_turn_state(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        turn_state: str,
        lease_ttl_seconds: float,
    ) -> None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            await repository.upsert_alias(
                session_id=session_id,
                alias_kind=_DURABLE_TURN_STATE_ALIAS,
                alias_value=turn_state,
                api_key_scope=api_key_scope,
            )
            await repository.renew_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_turn_state=turn_state,
            )

    async def register_previous_response_id(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        instance_id: str,
        owner_epoch: int,
        response_id: str,
        lease_ttl_seconds: float,
    ) -> None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            repository = DurableBridgeRepository(session)
            await repository.upsert_alias(
                session_id=session_id,
                alias_kind=_DURABLE_PREVIOUS_RESPONSE_ALIAS,
                alias_value=response_id,
                api_key_scope=api_key_scope,
            )
            await repository.renew_session(
                session_id=session_id,
                instance_id=instance_id,
                owner_epoch=owner_epoch,
                lease_ttl_seconds=lease_ttl_seconds,
                latest_response_id=response_id,
            )

    async def register_session_header(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        session_header: str,
    ) -> None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async with self._session() as session:
            await DurableBridgeRepository(session).upsert_alias(
                session_id=session_id,
                alias_kind=_DURABLE_SESSION_HEADER_ALIAS,
                alias_value=session_header,
                api_key_scope=api_key_scope,
            )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
        finally:
            await session.close()


def _to_lookup(snapshot: DurableBridgeSessionSnapshot) -> DurableBridgeLookup:
    return DurableBridgeLookup(
        session_id=snapshot.id,
        canonical_kind=snapshot.session_key_kind,
        canonical_key=snapshot.session_key_value,
        api_key_scope=snapshot.api_key_scope,
        owner_instance_id=snapshot.owner_instance_id,
        owner_epoch=snapshot.owner_epoch,
        lease_expires_at=snapshot.lease_expires_at,
        state=snapshot.state,
        latest_turn_state=snapshot.latest_turn_state,
        latest_response_id=snapshot.latest_response_id,
    )

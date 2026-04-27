from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HttpBridgeSessionState
from app.db.sqlite_retry import retry_sqlite_lock
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeRepository,
    DurableBridgeSessionSnapshot,
    durable_bridge_api_key_scope,
)

_DURABLE_TURN_STATE_ALIAS = "turn_state"
_DURABLE_PREVIOUS_RESPONSE_ALIAS = "previous_response_id"
_DURABLE_SESSION_HEADER_ALIAS = "session_header"
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DurableBridgeLookup:
    session_id: str
    canonical_kind: str
    canonical_key: str
    api_key_scope: str
    account_id: str | None
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
                if turn_state is not None:
                    snapshot = await repository.find_session_by_latest_turn_state(
                        turn_state=turn_state,
                        api_key_scope=api_key_scope,
                    )
                if snapshot is None and previous_response_id is not None:
                    snapshot = await repository.find_session_by_latest_response_id(
                        response_id=previous_response_id,
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
        async def _claim_once() -> DurableBridgeLookup:
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

        return await self._write_with_sqlite_retry("durable_bridge_claim", _claim_once)

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
        async def _renew_once() -> DurableBridgeLookup | None:
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

        return await self._write_with_sqlite_retry("durable_bridge_renew", _renew_once)

    async def release_live_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeLookup | None:
        async def _release_once() -> DurableBridgeLookup | None:
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

        return await self._write_with_sqlite_retry("durable_bridge_release", _release_once)

    async def mark_instance_draining(self, *, instance_id: str) -> int:
        async def _mark_once() -> int:
            async with self._session() as session:
                return await DurableBridgeRepository(session).mark_owner_draining(instance_id=instance_id)

        return await self._write_with_sqlite_retry("durable_bridge_mark_draining", _mark_once)

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
        async def _register_once() -> None:
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

        await self._write_with_sqlite_retry("durable_bridge_turn_state", _register_once)

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
        async def _register_once() -> None:
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

        await self._write_with_sqlite_retry("durable_bridge_previous_response", _register_once)

    async def register_session_header(
        self,
        *,
        session_id: str,
        api_key_id: str | None,
        session_header: str,
    ) -> None:
        api_key_scope = durable_bridge_api_key_scope(api_key_id)
        async def _register_once() -> None:
            async with self._session() as session:
                await DurableBridgeRepository(session).upsert_alias(
                    session_id=session_id,
                    alias_kind=_DURABLE_SESSION_HEADER_ALIAS,
                    alias_value=session_header,
                    api_key_scope=api_key_scope,
                )

        await self._write_with_sqlite_retry("durable_bridge_session_header", _register_once)

    async def _write_with_sqlite_retry(
        self,
        operation_name: str,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        return await retry_sqlite_lock(operation, operation_name=operation_name, logger=logger)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()


def _to_lookup(snapshot: DurableBridgeSessionSnapshot) -> DurableBridgeLookup:
    return DurableBridgeLookup(
        session_id=snapshot.id,
        canonical_kind=snapshot.session_key_kind,
        canonical_key=snapshot.session_key_value,
        api_key_scope=snapshot.api_key_scope,
        account_id=snapshot.account_id,
        owner_instance_id=snapshot.owner_instance_id,
        owner_epoch=snapshot.owner_epoch,
        lease_expires_at=snapshot.lease_expires_at,
        state=snapshot.state,
        latest_turn_state=snapshot.latest_turn_state,
        latest_response_id=snapshot.latest_response_id,
    )

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from app.modules.peer_fallback_targets.repository import PeerFallbackTargetConflictError


class PeerFallbackTargetLike(Protocol):
    id: str
    base_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PeerFallbackTargetRepositoryPort(Protocol):
    async def list_targets(self) -> Sequence[PeerFallbackTargetLike]: ...

    async def list_enabled_base_urls(self) -> list[str]: ...

    async def get(self, target_id: str) -> PeerFallbackTargetLike | None: ...

    async def create(self, *, base_url: str, enabled: bool) -> PeerFallbackTargetLike: ...

    async def update(
        self,
        target: PeerFallbackTargetLike,
        *,
        base_url: str | None = None,
        enabled: bool | None = None,
    ) -> PeerFallbackTargetLike: ...

    async def delete(self, target: PeerFallbackTargetLike) -> None: ...


class PeerFallbackTargetValidationError(ValueError):
    pass


class PeerFallbackTargetAlreadyExistsError(ValueError):
    pass


class PeerFallbackTargetNotFoundError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PeerFallbackTargetData:
    id: str
    base_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PeerFallbackTargetCreateData:
    base_url: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PeerFallbackTargetUpdateData:
    base_url: str | None = None
    enabled: bool | None = None


class PeerFallbackTargetService:
    def __init__(self, repository: PeerFallbackTargetRepositoryPort) -> None:
        self._repository = repository

    async def list_targets(self) -> list[PeerFallbackTargetData]:
        rows = await self._repository.list_targets()
        return [_target_data(row) for row in rows]

    async def list_enabled_base_urls(self) -> list[str]:
        return await self._repository.list_enabled_base_urls()

    async def create_target(self, payload: PeerFallbackTargetCreateData) -> PeerFallbackTargetData:
        base_url = normalize_peer_fallback_base_url(payload.base_url)
        try:
            row = await self._repository.create(base_url=base_url, enabled=payload.enabled)
        except PeerFallbackTargetConflictError as exc:
            raise PeerFallbackTargetAlreadyExistsError("Peer fallback target already exists") from exc
        return _target_data(row)

    async def update_target(
        self,
        target_id: str,
        payload: PeerFallbackTargetUpdateData,
    ) -> PeerFallbackTargetData:
        target = await self._repository.get(target_id)
        if target is None:
            raise PeerFallbackTargetNotFoundError("Peer fallback target not found")
        base_url = normalize_peer_fallback_base_url(payload.base_url) if payload.base_url is not None else None
        try:
            row = await self._repository.update(target, base_url=base_url, enabled=payload.enabled)
        except PeerFallbackTargetConflictError as exc:
            raise PeerFallbackTargetAlreadyExistsError("Peer fallback target already exists") from exc
        return _target_data(row)

    async def delete_target(self, target_id: str) -> None:
        target = await self._repository.get(target_id)
        if target is None:
            raise PeerFallbackTargetNotFoundError("Peer fallback target not found")
        await self._repository.delete(target)


def normalize_peer_fallback_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw:
        raise PeerFallbackTargetValidationError("Peer fallback target URL is required")
    if any(char.isspace() for char in raw):
        raise PeerFallbackTargetValidationError("Peer fallback target must be an absolute http(s) URL")
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise PeerFallbackTargetValidationError("Peer fallback target must be an absolute http(s) URL") from exc
    if parsed.scheme not in {"http", "https"} or hostname is None:
        raise PeerFallbackTargetValidationError("Peer fallback target must be an absolute http(s) URL")
    if parsed.params or parsed.query or parsed.fragment:
        raise PeerFallbackTargetValidationError("Peer fallback target URL must not include params, query, or fragment")
    return raw


def _target_data(target: PeerFallbackTargetLike) -> PeerFallbackTargetData:
    return PeerFallbackTargetData(
        id=target.id,
        base_url=target.base_url,
        enabled=target.enabled,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )

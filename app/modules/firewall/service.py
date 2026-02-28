from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.modules.firewall.repository import FirewallRepositoryConflictError


class FirewallEntryLike(Protocol):
    ip_address: str
    created_at: datetime


class FirewallRepositoryPort(Protocol):
    async def list_entries(self) -> Sequence[FirewallEntryLike]: ...

    async def list_ip_addresses(self) -> set[str]: ...

    async def exists(self, ip_address: str) -> bool: ...

    async def add(self, ip_address: str) -> FirewallEntryLike: ...

    async def delete(self, ip_address: str) -> bool: ...


class FirewallValidationError(ValueError):
    pass


class FirewallIpAlreadyExistsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FirewallIpEntryData:
    ip_address: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FirewallListData:
    mode: Literal["allow_all", "allowlist_active"]
    entries: list[FirewallIpEntryData]


class FirewallService:
    def __init__(self, repository: FirewallRepositoryPort) -> None:
        self._repository = repository

    async def list_ips(self) -> FirewallListData:
        rows = await self._repository.list_entries()
        entries = [FirewallIpEntryData(ip_address=row.ip_address, created_at=row.created_at) for row in rows]
        mode: Literal["allow_all", "allowlist_active"] = "allow_all" if not entries else "allowlist_active"
        return FirewallListData(mode=mode, entries=entries)

    async def add_ip(self, ip_address: str) -> FirewallIpEntryData:
        normalized = normalize_ip_address(ip_address)
        if await self._repository.exists(normalized):
            raise FirewallIpAlreadyExistsError("IP address already exists")
        try:
            row = await self._repository.add(normalized)
        except FirewallRepositoryConflictError as exc:
            raise FirewallIpAlreadyExistsError("IP address already exists") from exc
        return FirewallIpEntryData(ip_address=row.ip_address, created_at=row.created_at)

    async def remove_ip(self, ip_address: str) -> bool:
        normalized = normalize_ip_address(ip_address)
        return await self._repository.delete(normalized)

    async def is_ip_allowed(self, ip_address: str | None) -> bool:
        allowlist = await self._repository.list_ip_addresses()
        if not allowlist:
            return True
        if ip_address is None:
            return False
        try:
            normalized = normalize_ip_address(ip_address)
        except FirewallValidationError:
            return False
        return normalized in allowlist


def normalize_ip_address(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise FirewallValidationError("IP address is required")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError as exc:
        raise FirewallValidationError("Invalid IP address") from exc

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.modules.firewall.repository import FirewallRepositoryConflictError
from app.modules.firewall.service import (
    FirewallIpAlreadyExistsError,
    FirewallService,
    FirewallValidationError,
    normalize_ip_address,
)

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class _Entry:
    ip_address: str
    created_at: datetime


class _Repo:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    async def list_entries(self) -> list[_Entry]:
        return sorted(self._entries.values(), key=lambda entry: (entry.created_at, entry.ip_address))

    async def list_ip_addresses(self) -> set[str]:
        return set(self._entries.keys())

    async def exists(self, ip_address: str) -> bool:
        return ip_address in self._entries

    async def add(self, ip_address: str) -> _Entry:
        entry = _Entry(ip_address=ip_address, created_at=datetime.now(UTC))
        self._entries[ip_address] = entry
        return entry

    async def delete(self, ip_address: str) -> bool:
        return self._entries.pop(ip_address, None) is not None


def test_normalize_ip_address_rejects_invalid_value():
    with pytest.raises(FirewallValidationError):
        normalize_ip_address("invalid")


def test_normalize_ip_address_normalizes_ipv6():
    assert normalize_ip_address("2001:0db8:0000:0000:0000:ff00:0042:8329") == "2001:db8::ff00:42:8329"


@pytest.mark.asyncio
async def test_add_ip_rejects_duplicates():
    service = FirewallService(_Repo())
    await service.add_ip("127.0.0.1")
    with pytest.raises(FirewallIpAlreadyExistsError):
        await service.add_ip("127.0.0.1")


@pytest.mark.asyncio
async def test_add_ip_maps_repository_conflict_to_exists_error():
    class _ConflictRepo(_Repo):
        async def exists(self, ip_address: str) -> bool:
            return False

        async def add(self, ip_address: str) -> _Entry:
            raise FirewallRepositoryConflictError("duplicate")

    service = FirewallService(_ConflictRepo())
    with pytest.raises(FirewallIpAlreadyExistsError):
        await service.add_ip("127.0.0.1")


@pytest.mark.asyncio
async def test_is_ip_allowed_follows_allowlist_mode():
    service = FirewallService(_Repo())

    assert await service.is_ip_allowed("192.168.0.1") is True

    await service.add_ip("127.0.0.1")

    assert await service.is_ip_allowed("127.0.0.1") is True
    assert await service.is_ip_allowed("192.168.0.1") is False
    assert await service.is_ip_allowed("invalid-ip") is False

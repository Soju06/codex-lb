from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from app.modules.shared.schemas import DashboardModel


class RoleMappingResponse(DashboardModel):
    """One rule; the list is ordered by descending priority (the winner first)."""

    id: str
    provider: str
    provider_key: str
    claim_name: str
    claim_value: str
    role_id: str
    priority: int
    created_at: datetime
    updated_at: datetime


class AssignableRoleResponse(DashboardModel):
    """A role the caller may point a rule (or a provider default) at.

    Deliberately smaller than the ``users:manage`` roles list: a picker needs
    to name a role, not to know how many people hold it.
    """

    id: str
    slug: str
    name: str
    description: str | None = None
    kind: str
    locked: bool


class RoleMappingCreateRequest(DashboardModel):
    """A new rule; the server appends it at the lowest priority (clients never send one)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    provider_key: str = Field(min_length=1, max_length=64)
    claim_name: str = Field(min_length=1, max_length=32)
    claim_value: str = Field(min_length=1, max_length=320)
    role_id: str = Field(min_length=1, max_length=36)


class RoleMappingUpdateRequest(DashboardModel):
    """Fields left out are untouched. The order is changed with ``PUT /api/role-mappings/order``."""

    model_config = ConfigDict(extra="forbid")

    claim_value: str | None = Field(default=None, min_length=1, max_length=320)
    role_id: str | None = Field(default=None, min_length=1, max_length=36)


class RoleMappingOrderRequest(DashboardModel):
    """The full order of one provider's rules, winner first."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=32)
    provider_key: str = Field(min_length=1, max_length=64)
    ids: list[str] = Field(min_length=1)

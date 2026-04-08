from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel
from app.modules.upstream_identities.types import (
    PHASE1_PLATFORM_ROUTE_FAMILIES,
    PlatformRouteFamily,
)


class PlatformIdentityCreateRequest(DashboardModel):
    label: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    organization: str | None = None
    project: str | None = None
    eligible_route_families: list[str] = Field(default_factory=list)

    @field_validator("label", "api_key", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("organization", "project", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("eligible_route_families")
    @classmethod
    def _validate_route_families(cls, value: list[str]) -> list[str]:
        invalid = [item for item in value if item not in PHASE1_PLATFORM_ROUTE_FAMILIES]
        if invalid:
            raise ValueError(f"Unsupported route families: {', '.join(sorted(invalid))}")
        return value


class PlatformIdentityUpdateRequest(DashboardModel):
    label: str | None = None
    api_key: str | None = None
    organization: str | None = None
    project: str | None = None
    eligible_route_families: list[str] | None = None

    @field_validator("label", "api_key", mode="before")
    @classmethod
    def _strip_optional_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("label", "api_key")
    @classmethod
    def _reject_blank_required_strings(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("Field cannot be blank")
        return value

    @field_validator("organization", "project", mode="before")
    @classmethod
    def _strip_optional_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("eligible_route_families")
    @classmethod
    def _validate_optional_route_families(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        invalid = [item for item in value if item not in PHASE1_PLATFORM_ROUTE_FAMILIES]
        if invalid:
            raise ValueError(f"Unsupported route families: {', '.join(sorted(invalid))}")
        return value


class PlatformIdentityValidationResult(DashboardModel):
    valid: bool
    last_validated_at: datetime | None = None
    failure_reason: str | None = None
    models: list[str] = Field(default_factory=list)


class PlatformIdentitySummary(DashboardModel):
    account_id: str
    provider_kind: str
    routing_subject_id: str
    label: str
    status: str
    email: str
    display_name: str
    plan_type: str
    eligible_route_families: list[str] = Field(default_factory=list)
    last_validated_at: datetime | None = None
    last_auth_failure_reason: str | None = None
    organization: str | None = None
    project: str | None = None


def route_family_list(values: tuple[str, ...]) -> list[PlatformRouteFamily]:
    return [value for value in values if value in PHASE1_PLATFORM_ROUTE_FAMILIES]  # type: ignore[list-item]

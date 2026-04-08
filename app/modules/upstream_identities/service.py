from __future__ import annotations

from dataclasses import dataclass

from app.core.auth import generate_unique_account_id
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import AccountStatus, OpenAIPlatformIdentity
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.upstream_identities.repository import (
    OpenAIPlatformIdentitiesRepository,
    OpenAIPlatformIdentityCreate,
    OpenAIPlatformIdentityUpdate,
    normalize_route_families,
    split_route_families,
)
from app.modules.upstream_identities.schemas import (
    PlatformIdentityCreateRequest,
    PlatformIdentitySummary,
    PlatformIdentityUpdateRequest,
    PlatformIdentityValidationResult,
)
from app.modules.upstream_identities.types import OPENAI_PLATFORM_PROVIDER_KIND


@dataclass(frozen=True, slots=True)
class PlatformIdentityCreateResult:
    identity: OpenAIPlatformIdentity
    validation: PlatformIdentityValidationResult


@dataclass(frozen=True, slots=True)
class PlatformIdentityUpdateResult:
    identity: OpenAIPlatformIdentity
    validation: PlatformIdentityValidationResult | None


class OpenAIPlatformIdentitiesService:
    def __init__(
        self,
        repo: OpenAIPlatformIdentitiesRepository,
        sticky_repository: StickySessionsRepository | None = None,
    ) -> None:
        self._repo = repo
        self._sticky_repository = sticky_repository
        self._encryptor = TokenEncryptor()

    async def list_identities(self) -> list[PlatformIdentitySummary]:
        identities = await self._repo.list_identities()
        return [self._to_summary(identity) for identity in identities]

    async def get_identity(self, identity_id: str) -> OpenAIPlatformIdentity | None:
        return await self._repo.get_by_id(identity_id)

    async def create_identity(
        self,
        payload: PlatformIdentityCreateRequest,
        *,
        validation: PlatformIdentityValidationResult,
    ) -> PlatformIdentityCreateResult:
        label = payload.label.strip()
        identity_id = generate_unique_account_id(label, label)
        route_families = normalize_route_families(payload.eligible_route_families)
        identity = await self._repo.create_identity(
            OpenAIPlatformIdentityCreate(
                id=identity_id,
                label=label,
                api_key_encrypted=self._encryptor.encrypt(payload.api_key),
                organization_id=payload.organization,
                project_id=payload.project,
                eligible_route_families=route_families,
                status=AccountStatus.ACTIVE if validation.valid else AccountStatus.DEACTIVATED,
                last_validated_at=validation.last_validated_at,
                last_auth_failure_reason=validation.failure_reason,
                deactivation_reason=validation.failure_reason if not validation.valid else None,
            )
        )
        return PlatformIdentityCreateResult(identity=identity, validation=validation)

    async def update_identity(
        self,
        identity: OpenAIPlatformIdentity,
        payload: PlatformIdentityUpdateRequest,
        *,
        validation: PlatformIdentityValidationResult | None,
    ) -> PlatformIdentityUpdateResult:
        label = payload.label.strip() if payload.label is not None else identity.label
        api_key = payload.api_key if payload.api_key is not None else self.decrypt_api_key(identity)
        route_families = normalize_route_families(
            payload.eligible_route_families
            if payload.eligible_route_families is not None
            else self.route_families(identity)
        )
        fields_set = payload.model_fields_set
        organization = payload.organization if "organization" in fields_set else identity.organization_id
        project = payload.project if "project" in fields_set else identity.project_id
        status = identity.status
        last_validated_at = identity.last_validated_at
        last_auth_failure_reason = identity.last_auth_failure_reason
        deactivation_reason = identity.deactivation_reason

        if validation is not None:
            last_validated_at = validation.last_validated_at
            last_auth_failure_reason = validation.failure_reason
            if identity.status == AccountStatus.PAUSED:
                status = AccountStatus.PAUSED
                deactivation_reason = None
            elif validation.valid:
                status = AccountStatus.ACTIVE
                deactivation_reason = None
            else:
                status = AccountStatus.DEACTIVATED
                deactivation_reason = validation.failure_reason

        updated_identity = await self._repo.update_identity(
            identity.id,
            OpenAIPlatformIdentityUpdate(
                label=label,
                api_key_encrypted=self._encryptor.encrypt(api_key),
                organization_id=organization,
                project_id=project,
                eligible_route_families=route_families,
                status=status,
                last_validated_at=last_validated_at,
                last_auth_failure_reason=last_auth_failure_reason,
                deactivation_reason=deactivation_reason,
            ),
        )
        if updated_identity is None:
            raise ValueError(f"Platform identity not found: {identity.id}")
        return PlatformIdentityUpdateResult(identity=updated_identity, validation=validation)

    async def reactivate_identity(self, identity_id: str) -> bool:
        return await self._repo.update_status(identity_id, AccountStatus.ACTIVE, deactivation_reason=None)

    async def pause_identity(self, identity_id: str) -> bool:
        return await self._repo.update_status(identity_id, AccountStatus.PAUSED, deactivation_reason=None)

    async def delete_identity(self, identity_id: str) -> bool:
        deleted = await self._repo.delete(identity_id)
        if deleted and self._sticky_repository is not None:
            await self._sticky_repository.delete_by_routing_subject(
                provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
                routing_subject_id=identity_id,
            )
        return deleted

    async def mark_auth_failure(self, identity_id: str, reason: str) -> bool:
        return await self._repo.update_validation_state(
            identity_id,
            last_validated_at=None,
            last_auth_failure_reason=reason,
            status=AccountStatus.DEACTIVATED,
        )

    async def mark_validated(self, identity_id: str) -> bool:
        return await self._repo.update_validation_state(
            identity_id,
            last_validated_at=utcnow(),
            last_auth_failure_reason=None,
            status=AccountStatus.ACTIVE,
        )

    def decrypt_api_key(self, identity: OpenAIPlatformIdentity) -> str:
        return self._encryptor.decrypt(identity.api_key_encrypted)

    def route_families(self, identity: OpenAIPlatformIdentity) -> tuple[str, ...]:
        return split_route_families(identity.eligible_route_families)

    def summarize_identity(self, identity: OpenAIPlatformIdentity) -> PlatformIdentitySummary:
        return self._to_summary(identity)

    def _to_summary(self, identity: OpenAIPlatformIdentity) -> PlatformIdentitySummary:
        return PlatformIdentitySummary(
            account_id=identity.id,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=identity.id,
            label=identity.label,
            status=identity.status.value,
            email=identity.label,
            display_name=identity.label,
            plan_type=OPENAI_PLATFORM_PROVIDER_KIND,
            eligible_route_families=list(self.route_families(identity)),
            last_validated_at=identity.last_validated_at,
            last_auth_failure_reason=identity.last_auth_failure_reason,
            organization=identity.organization_id,
            project=identity.project_id,
        )

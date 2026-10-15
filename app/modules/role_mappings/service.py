"""Group-to-role rules: the list an operator edits, the order the resolver reads.

Every write applies the same role rules as inviting a person: the role must be
assignable and within the caller's own grants — checked on the role the rule
would hand out next *and* on the one it hands out today, so a caller who may
not delegate admin can neither write, retarget, delete nor promote an
admin-granting rule. Every write also renumbers the provider's rules so their
priorities stay the contiguous ``N..1`` the resolver relies on, audits
``role_mapping_changed``, and invalidates the provider registry — a new rule
that only took effect after a cache TTL would look like it did nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditTarget
from app.core.auth.dashboard_access import (
    DashboardPrincipal,
    InsufficientDelegationError,
    assert_can_delegate,
)
from app.core.auth.providers.registry import get_auth_provider_registry
from app.db.models import DashboardRoleMapping, DashboardRoleRecord
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import (
    resolve_assignable_role,
    resolve_role_grants,
    role_assignable_to_users,
)
from app.modules.role_mappings.matching import SUPPORTED_CLAIMS, normalize_claim_value
from app.modules.role_mappings.repository import MAX_MAPPINGS_PER_PROVIDER, RoleMappingsRepository
from app.modules.role_mappings.schemas import (
    RoleMappingCreateRequest,
    RoleMappingOrderRequest,
    RoleMappingUpdateRequest,
)


class MappingNotFoundError(LookupError):
    pass


class MappingProviderNotFoundError(LookupError):
    pass


class UnknownClaimError(ValueError):
    pass


class MappingExistsError(ValueError):
    pass


class MappingLimitReachedError(ValueError):
    pass


class OrderStaleError(ValueError):
    pass


class RoleMappingsService:
    def __init__(self, repository: RoleMappingsRepository, roles: DashboardRolesRepository) -> None:
        self._repo = repository
        self._roles = roles

    async def list_mappings(
        self, *, provider: str | None = None, provider_key: str | None = None
    ) -> list[DashboardRoleMapping]:
        return list(await self._repo.list_mappings(provider=provider, provider_key=provider_key))

    async def create_mapping(
        self, principal: DashboardPrincipal, payload: RoleMappingCreateRequest, *, actor_ip: str | None
    ) -> DashboardRoleMapping:
        claim_name = payload.claim_name.strip().casefold()
        if claim_name not in SUPPORTED_CLAIMS:
            raise UnknownClaimError(f"Unsupported claim '{payload.claim_name}'")
        claim_value = normalize_claim_value(claim_name, payload.claim_value)
        if not claim_value:
            raise UnknownClaimError("The rule needs a value to match on")
        if not await self._repo.provider_exists(payload.provider, payload.provider_key):
            raise MappingProviderNotFoundError("Provider not found")
        role_slug = await self._delegated_role_slug(principal, payload.role_id)
        existing = list(await self._repo.list_for_provider(payload.provider, payload.provider_key))
        if len(existing) >= MAX_MAPPINGS_PER_PROVIDER:
            raise MappingLimitReachedError(f"A sign-in method takes at most {MAX_MAPPINGS_PER_PROVIDER} rules")
        if any(row.claim_name == claim_name and row.claim_value == claim_value for row in existing):
            raise MappingExistsError("A rule for that value already exists")
        mapping = DashboardRoleMapping(
            id=str(uuid.uuid4()),
            provider=payload.provider,
            provider_key=payload.provider_key,
            claim_name=claim_name,
            claim_value=claim_value,
            role_id=payload.role_id,
            # Parked outside the live range; ``renumber`` writes the real value.
            priority=0,
        )
        mapping_id = mapping.id
        self._repo.add(mapping)
        # Appended at the bottom: a new rule never outranks a rule that is already there.
        await self._renumber_and_commit([row.id for row in existing] + [mapping_id])
        stored = await self._require(mapping_id)
        self._audit(
            principal,
            stored,
            actor_ip,
            operation="created",
            role_slug=role_slug,
        )
        return stored

    async def update_mapping(
        self,
        principal: DashboardPrincipal,
        mapping_id: str,
        payload: RoleMappingUpdateRequest,
        *,
        actor_ip: str | None,
    ) -> DashboardRoleMapping:
        mapping = await self._require(mapping_id)
        current = await self._assert_may_hand_out(principal, mapping.role_id)
        fields = payload.model_fields_set
        changed = False
        # The rule keeps handing out its current role unless the payload moves it,
        # so the audit row can name the role either way.
        role_slug = current.slug if current is not None else None
        if "role_id" in fields and payload.role_id is not None:
            role_slug = await self._delegated_role_slug(principal, payload.role_id)
            if mapping.role_id != payload.role_id:
                mapping.role_id = payload.role_id
                changed = True
        if "claim_value" in fields and payload.claim_value is not None:
            claim_value = normalize_claim_value(mapping.claim_name, payload.claim_value)
            if not claim_value:
                raise UnknownClaimError("The rule needs a value to match on")
            if claim_value != mapping.claim_value:
                siblings = await self._repo.list_for_provider(mapping.provider, mapping.provider_key)
                if any(
                    row.id != mapping.id and row.claim_name == mapping.claim_name and row.claim_value == claim_value
                    for row in siblings
                ):
                    raise MappingExistsError("A rule for that value already exists")
                mapping.claim_value = claim_value
                changed = True
        if not changed:
            return mapping
        await self._commit()
        stored = await self._require(mapping_id)
        self._audit(principal, stored, actor_ip, operation="updated", role_slug=role_slug)
        return stored

    async def delete_mapping(self, principal: DashboardPrincipal, mapping_id: str, *, actor_ip: str | None) -> None:
        mapping = await self._require(mapping_id)
        current = await self._assert_may_hand_out(principal, mapping.role_id)
        provider, provider_key = mapping.provider, mapping.provider_key
        details: AuditDetails = {
            "operation": "deleted",
            "provider": provider,
            "provider_key": provider_key,
            "claim_name": mapping.claim_name,
            "claim_value": mapping.claim_value,
        }
        # Read off the row before it goes: afterwards nothing can say what the rule granted.
        if current is not None:
            details = {**details, "role": current.slug}
        siblings = await self._repo.list_for_provider(provider, provider_key)
        remaining = [row.id for row in siblings if row.id != mapping_id]
        await self._repo.delete_mapping(mapping_id)
        # Closing the gap keeps the priorities contiguous, so the next create appends at 1.
        await self._renumber_and_commit(remaining)
        AuditService.log_async(
            "role_mapping_changed",
            actor_ip=actor_ip,
            details=details,
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("role_mapping", mapping_id),
        )

    async def reorder_mappings(
        self, principal: DashboardPrincipal, payload: RoleMappingOrderRequest, *, actor_ip: str | None
    ) -> list[DashboardRoleMapping]:
        current = await self._repo.list_for_provider(payload.provider, payload.provider_key)
        if not current:
            raise MappingProviderNotFoundError("That sign-in method has no rules to order")
        if sorted(payload.ids) != sorted(row.id for row in current) or len(set(payload.ids)) != len(payload.ids):
            raise OrderStaleError("The order must list every rule of that sign-in method exactly once")
        await self._assert_may_move(principal, current, payload.ids)
        await self._renumber_and_commit(list(payload.ids))
        AuditService.log_async(
            "role_mapping_changed",
            actor_ip=actor_ip,
            details={
                "operation": "reordered",
                "provider": payload.provider,
                "provider_key": payload.provider_key,
                "ids": list(payload.ids),
            },
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("role_mapping", payload.ids[0]),
        )
        return list(await self._repo.list_for_provider(payload.provider, payload.provider_key))

    async def assignable_roles(self, principal: DashboardPrincipal) -> list[DashboardRoleRecord]:
        """The roles this caller may point a rule (or a provider default) at.

        The same two rules every write applies — assignable to people, within
        the caller's own grants — evaluated once for the picker, so a caller
        holding ``security:write`` without ``users:manage`` can still name the
        roles it hands out and is never offered one the server would refuse.
        """

        offered: list[DashboardRoleRecord] = []
        for role in await self._roles.list_roles():
            if not role_assignable_to_users(role):
                continue
            try:
                assert_can_delegate(principal.grants, resolve_role_grants(role))
            except InsufficientDelegationError:
                continue
            offered.append(role)
        return offered

    # --- helpers ---

    async def _delegated_role_slug(self, principal: DashboardPrincipal, role_id: str) -> str:
        role = await resolve_assignable_role(self._roles, role_id)
        assert_can_delegate(principal.grants, resolve_role_grants(role))
        return role.slug

    async def _assert_may_hand_out(self, principal: DashboardPrincipal, role_id: str) -> DashboardRoleRecord | None:
        """Gate a write on the role the rule ALREADY hands out, and name it for the audit.

        Editing the value of an admin-granting rule is the same delegation as
        writing one: without this a ``security:write`` holder could retarget it
        at a group it belongs to, or delete it, without ever naming the role.
        Deliberately ``get_role`` and not :func:`resolve_assignable_role`: a
        rule whose role later stopped being assignable must still refuse the
        caller who may not delegate it (403), not answer 422 as if the rule
        itself were the problem.
        """

        role = await self._roles.get_role(role_id)
        if role is not None:
            assert_can_delegate(principal.grants, resolve_role_grants(role))
        return role

    async def _assert_may_move(
        self, principal: DashboardPrincipal, current: Sequence[DashboardRoleMapping], ids: Sequence[str]
    ) -> None:
        """Every rule whose position this call changes must be one the caller may hand out.

        Promoting an admin-granting rule above one's own is the same act as
        writing it, and demoting one silently disarms it.
        """

        position = {mapping_id: index for index, mapping_id in enumerate(ids)}
        moved = {row.role_id for index, row in enumerate(current) if position[row.id] != index}
        for role_id in sorted(moved):
            await self._assert_may_hand_out(principal, role_id)

    async def _renumber_and_commit(self, ordered_ids: Sequence[str]) -> None:
        """Write the dense order and commit, as one refusal-mapped step.

        Both renumbering paths decide the new order from a snapshot they read
        first, so a second writer landing in between makes that snapshot stale
        and the write trips ``uq_dashboard_role_mappings_priority``. That is a
        reload, not a 500: it surfaces as the same stale-order refusal a
        rejected reorder gets.
        """

        try:
            await self._repo.renumber(ordered_ids)
            await self._commit()
        except IntegrityError as exc:
            await self._repo.rollback()
            raise OrderStaleError("The rules changed while you were editing; reload and try again") from exc

    async def _require(self, mapping_id: str) -> DashboardRoleMapping:
        mapping = await self._repo.get_mapping(mapping_id)
        if mapping is None:
            raise MappingNotFoundError("Rule not found")
        return mapping

    async def _commit(self) -> None:
        await self._repo.commit()
        await invalidate_identity_resolution()

    @staticmethod
    def _audit(
        principal: DashboardPrincipal,
        mapping: DashboardRoleMapping,
        actor_ip: str | None,
        *,
        operation: str,
        role_slug: str | None,
    ) -> None:
        details: AuditDetails = {
            "operation": operation,
            "provider": mapping.provider,
            "provider_key": mapping.provider_key,
            "claim_name": mapping.claim_name,
            "claim_value": mapping.claim_value,
            "priority": mapping.priority,
        }
        if role_slug is not None:
            details = {**details, "role": role_slug}
        AuditService.log_async(
            "role_mapping_changed",
            actor_ip=actor_ip,
            details=details,
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("role_mapping", mapping.id),
        )


async def invalidate_identity_resolution() -> None:
    """Drop the caches that decide how an identity resolves, here and on peers.

    The registry bump reaches other replicas through the invalidation
    namespace; this replica's own identity cache is cleared directly so the
    operator's very next request already sees the new rule (a cached refusal
    must not keep refusing someone a rule now admits).
    """

    from app.modules.dashboard_users.identity_resolver import get_identity_resolution_cache

    await get_auth_provider_registry().invalidate()
    get_identity_resolution_cache().clear()

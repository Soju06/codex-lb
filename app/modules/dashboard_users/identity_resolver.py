"""Identity -> account: the one path every sign-in provider ends in.

Order of resolution (PLAN.md 4.6): an existing identity row; an ``invited``
account pre-created for exactly this identity; an active account with the
same e-mail when the provider allows e-mail linking; and finally just-in-time
provisioning with the role its highest-priority matching rule names, falling
back to the provider's ``unknown_identity_role_id`` (``NULL`` means refuse).
Accounts are never resolved by username. With no role mappings on the
provider, existing accounts are never re-evaluated (D10: an upgraded
trusted-header install keeps its admins); once a provider has rules, the
accounts it created (``role_source=mapping``) are re-evaluated on the same run
and only written when something actually changed.

A header-style provider presents the identity on every request, so results
are cached per identity for the users-cache TTL: the database path (and its
writes) runs once per TTL per identity, and status or role changes reach the
request path through the users cache as usual.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import anyio
from sqlalchemy.exc import IntegrityError

import app.modules.dashboard_users.repository as users_repository
from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditTarget
from app.core.audit.types import AuditSeverity
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers import ExternalIdentity
from app.core.utils.time import utcnow
from app.db.models import (
    COMPAT_ADMIN_USERNAME,
    DashboardAuthProvider,
    DashboardIdentity,
    DashboardRoleMapping,
    DashboardRoleRecord,
    DashboardUser,
    DashboardUserInvite,
    DashboardUserRoleSource,
    DashboardUserStatus,
)
from app.db.session import SessionLocal
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_users.repository import DashboardUsersRepository, normalize_email
from app.modules.role_mappings.matching import match_role_id
from app.modules.role_mappings.repository import RoleMappingsRepository

logger = logging.getLogger(__name__)

DenialCode = Literal["identity_not_provisioned", "account_disabled"]

_SLUG_MAX_LENGTH = 56
_SLUG_DISALLOWED = re.compile(r"[^a-z0-9._-]")
_JIT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ResolvedAccount:
    user_id: str


@dataclass(frozen=True, slots=True)
class Denied:
    code: DenialCode


Resolution = ResolvedAccount | Denied


def slugify_subject(subject: str) -> str:
    """The username stem of a just-in-time account (PLAN.md 4.6 JIT rules).

    Lower-cased, ``@`` becomes ``.``, everything outside ``[a-z0-9._-]`` is
    dropped, cut to 56 characters (leaving room for a collision suffix). An
    empty result falls back to ``th-<sha256 8hex>`` of the subject so any
    subject yields a valid, stable name.
    """

    lowered = subject.casefold().replace("@", ".")
    slug = _SLUG_DISALLOWED.sub("", lowered)[:_SLUG_MAX_LENGTH]
    if not slug:
        return f"th-{hashlib.sha256(subject.encode('utf-8')).hexdigest()[:8]}"
    return slug


def jit_username_candidates(stem: str) -> Iterator[str]:
    """``stem``, then ``stem-2``, ``stem-3``, ... for UNIQUE collisions."""

    yield stem
    suffix = 2
    while True:
        yield f"{stem}-{suffix}"
        suffix += 1


def _now() -> datetime:
    """Aware clock for the tz-aware invite columns (``expires_at`` comparisons)."""

    return users_repository.utc_now()


def _naive_now() -> datetime:
    """Naive UTC for the naive account/identity timestamp columns (PostgreSQL rejects aware values there)."""

    return utcnow()


def _user_actor(user: DashboardUser, auth_method: str) -> AuditActor:
    return AuditActor(user_id=user.id, username=user.username, role_slug=user.role.slug, auth_method=auth_method)


def _groups_snapshot(groups: tuple[str, ...]) -> str | None:
    """The identity's group set as stored on the identity row; ``None`` when it has none."""

    return json.dumps(list(groups)) if groups else None


def _identity_details(identity: ExternalIdentity) -> AuditDetails:
    return {
        "provider": identity.provider,
        "provider_key": identity.provider_key,
        "subject": identity.subject,
        "email": identity.email,
        "groups": list(identity.groups),
    }


class IdentityResolver:
    def __init__(
        self,
        repository: DashboardUsersRepository,
        roles: DashboardRolesRepository,
        mappings: RoleMappingsRepository,
    ) -> None:
        self._repo = repository
        self._roles = roles
        self._mappings = mappings

    async def resolve(
        self, identity: ExternalIdentity, provider: DashboardAuthProvider, *, actor_ip: str | None
    ) -> Resolution:
        now = _now()
        existing = await self._repo.get_identity(identity.provider, identity.provider_key, identity.subject)
        if existing is not None:
            return await self._seen(existing, identity, provider, now)
        invite = await self._repo.find_invite_expecting_identity(
            identity.provider, identity.provider_key, identity.subject, now
        )
        if invite is not None:
            return await self._link_invited(invite, identity, provider, now, actor_ip)
        if provider.link_by_email and identity.email is not None:
            candidate = await self._repo.get_by_email(normalize_email(identity.email) or "")
            if candidate is not None and candidate.status == DashboardUserStatus.ACTIVE.value:
                return await self._link(candidate, identity, provider, now, actor_ip, via="email")
        return await self._provision(identity, provider, now, actor_ip)

    # --- step 1: known identity ---

    async def _seen(
        self, row: DashboardIdentity, identity: ExternalIdentity, provider: DashboardAuthProvider, now: datetime
    ) -> Resolution:
        user = row.user
        if user.status == DashboardUserStatus.DISABLED.value:
            return Denied("account_disabled")
        if user.status != DashboardUserStatus.ACTIVE.value:
            return Denied("identity_not_provisioned")
        # One write per TTL: this is the trusted-header "login".
        if row.email != identity.email:
            row.email = identity.email
        if row.display_name != identity.display_name:
            row.display_name = identity.display_name
        snapshot = _groups_snapshot(identity.groups)
        if row.groups_json != snapshot:
            row.groups_json = snapshot
        seen = _naive_now()
        row.last_seen_at = seen
        user.last_login_at = seen
        await self._repo.commit_user(user.id)
        return await self._reevaluate(user, identity, provider) or ResolvedAccount(user_id=user.id)

    # --- re-evaluation of an account the provider manages (PLAN.md 4.6) ---

    async def _reevaluate(
        self, user: DashboardUser, identity: ExternalIdentity, provider: DashboardAuthProvider
    ) -> Resolution | None:
        """Move a managed account onto the role its rules name; ``None`` = nothing to do.

        Skipped for an account a person decided (``role_source=manual``), for a
        provider whose role sync is off, and — the D10 guard — for a provider
        with no rules at all: an upgraded install that adds no rule keeps every
        account exactly as it was.
        """

        if provider.skip_role_sync or user.role_source != DashboardUserRoleSource.MAPPING.value:
            return None
        rows = await self._mappings.list_for_provider(identity.provider, identity.provider_key)
        if not rows:
            return None
        winner = match_role_id(rows, groups=identity.groups, email=identity.email)
        if winner is not None:
            await self._apply_mapped_role(user, identity, winner)
            return None
        return await self._demote(user, identity, provider)

    async def _apply_mapped_role(self, user: DashboardUser, identity: ExternalIdentity, role_id: str) -> None:
        if user.role_id == role_id:
            return
        if await self._pin_last_admin(user, identity, role_id=role_id, status=user.status):
            return
        role = await self._roles.get_role(role_id)
        if role is None:  # pragma: no cover - RESTRICT keeps a rule's role alive
            logger.warning("role_mapping_role_missing provider=%s role_id=%s", identity.provider, role_id)
            return
        previous_slug, new_slug = user.role.slug, role.slug
        user.role_id = role_id
        user = await self._repo.commit_user(user.id, bump_generation=True)
        await get_dashboard_users_cache().invalidate()
        AuditService.log_async(
            "user_role_changed",
            actor_ip=None,
            details={"username": user.username, "from": previous_slug, "to": new_slug, "source": "mapping"},
            actor=_user_actor(user, identity.provider),
            target=AuditTarget("user", user.id),
        )

    async def _demote(
        self, user: DashboardUser, identity: ExternalIdentity, provider: DashboardAuthProvider
    ) -> Resolution | None:
        """No rule matches: park the account on ``no_match_role_id``, or disable it when that is NULL."""

        fallback = provider.no_match_role_id
        if fallback is not None and fallback == user.role_id:
            return None
        status = user.status if fallback is not None else DashboardUserStatus.DISABLED.value
        role_id = fallback if fallback is not None else user.role_id
        if await self._pin_last_admin(user, identity, role_id=role_id, status=status):
            return None
        previous_slug = user.role.slug
        new_slug: str | None = None
        if fallback is not None:
            role = await self._roles.get_role(fallback)
            if role is None:
                logger.warning("auth_provider_no_match_role_missing provider=%s role_id=%s", provider.kind, fallback)
                return None
            new_slug = role.slug
            user.role_id = fallback
        else:
            user.status = DashboardUserStatus.DISABLED.value
        user = await self._repo.commit_user(user.id, bump_generation=True)
        await get_dashboard_users_cache().invalidate()
        AuditService.log_async(
            "role_demoted_no_mapping",
            actor_ip=None,
            details={
                "username": user.username,
                "from": previous_slug,
                "to": new_slug,
                "provider": provider.kind,
                "provider_key": provider.provider_key,
            },
            actor=_user_actor(user, identity.provider),
            target=AuditTarget("user", user.id),
            severity=AuditSeverity.WARNING,
        )
        return None if fallback is not None else Denied("account_disabled")

    async def _pin_last_admin(
        self, user: DashboardUser, identity: ExternalIdentity, *, role_id: str, status: str
    ) -> bool:
        """Refuse a change that would remove the last admin, once and for good.

        The account keeps its role and becomes ``manual``, so the next run does
        not retry the same refusal (and does not write the same audit row every
        TTL). Locking an install out of its own reverse proxy is worse than a
        rule that does not apply to one account. The count is read under the
        accounts write intent, so the answer cannot be stale by the time the
        demotion commits.
        """

        admin_preset = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
        leaves_admin = (
            user.status == DashboardUserStatus.ACTIVE.value
            and user.role_id == admin_preset
            and not (role_id == admin_preset and status == DashboardUserStatus.ACTIVE.value)
        )
        if not leaves_admin:
            return False
        # Serialise before reading the invariant, exactly like the admin path:
        # two replicas demoting two mapping-managed admins at once would
        # otherwise each see the other survive and both commit, leaving an
        # install with no admin at all. The lock is taken on this resolver's
        # own session, which is also the session ``commit_user`` commits, so
        # it is still held when the demotion is written.
        await self._repo.acquire_write_intent()
        if await self._repo.count_active_admins(exclude_user_id=user.id) > 0:
            return False
        previous_source = user.role_source
        user.role_source = DashboardUserRoleSource.MANUAL.value
        user = await self._repo.commit_user(user.id)
        await get_dashboard_users_cache().invalidate()
        AuditService.log_async(
            "role_source_overridden",
            actor_ip=None,
            details={
                "username": user.username,
                "from_source": previous_source,
                "to_source": DashboardUserRoleSource.MANUAL.value,
                "reason": "last_admin_protected",
                "provider": identity.provider,
            },
            actor=_user_actor(user, identity.provider),
            target=AuditTarget("user", user.id),
            severity=AuditSeverity.WARNING,
        )
        return True

    # --- step 1.5: pre-created account waiting for this identity ---

    async def _link_invited(
        self,
        invite: DashboardUserInvite,
        identity: ExternalIdentity,
        provider: DashboardAuthProvider,
        now: datetime,
        actor_ip: str | None,
    ) -> Resolution:
        user = invite.user
        self._repo.add(self._identity_row(user, identity))
        user.status = DashboardUserStatus.ACTIVE.value
        user.last_login_at = _naive_now()
        try:
            if not await self._repo.consume_invite_by_identity(invite.id, now=now):
                await self._repo.rollback()
                return Denied("identity_not_provisioned")
            user = await self._repo.commit_user(user.id, bump_generation=True)
        except IntegrityError:
            # The identity row landed from a concurrent request; that request's answer stands.
            await self._repo.rollback()
            return await self._retry_seen(identity, provider, now)
        await get_dashboard_users_cache().invalidate()
        self._audit_linked(user, identity, actor_ip, via="invite")
        return ResolvedAccount(user_id=user.id)

    # --- step 2: e-mail link ---

    async def _link(
        self,
        user: DashboardUser,
        identity: ExternalIdentity,
        provider: DashboardAuthProvider,
        now: datetime,
        actor_ip: str | None,
        *,
        via: str,
    ) -> Resolution:
        self._repo.add(self._identity_row(user, identity))
        user.last_login_at = _naive_now()
        try:
            user = await self._repo.commit_user(user.id)
        except IntegrityError:
            await self._repo.rollback()
            return await self._retry_seen(identity, provider, now)
        await get_dashboard_users_cache().invalidate()
        self._audit_linked(user, identity, actor_ip, via=via)
        return ResolvedAccount(user_id=user.id)

    # --- step 3: just-in-time provisioning ---

    async def _provision(
        self, identity: ExternalIdentity, provider: DashboardAuthProvider, now: datetime, actor_ip: str | None
    ) -> Resolution:
        role = await self._jit_role(provider, identity)
        if role is None:
            AuditService.log_async(
                "login_failed",
                actor_ip=actor_ip,
                details={"method": identity.provider, "reason": "unknown_identity", **_identity_details(identity)},
                severity=AuditSeverity.WARNING,
            )
            return Denied("identity_not_provisioned")
        # Scalars only: a rollback below expires the ORM row and a lazy reload
        # outside the session's greenlet would fail (MissingGreenlet).
        role_id, role_slug = role.id, role.slug
        email = identity.email
        if email is not None and await self._repo.get_by_email(email) is not None:
            # Same e-mail, linking off: the address stays on the identity row only.
            email = None
        for attempt in range(_JIT_ATTEMPTS):
            username = await self._free_username(identity.subject)
            user = DashboardUser(
                id=str(uuid.uuid4()),
                username=username,
                display_name=identity.display_name,
                email=email,
                role_id=role_id,
                role_source=DashboardUserRoleSource.MAPPING.value,
                status=DashboardUserStatus.ACTIVE.value,
                last_login_at=_naive_now(),
            )
            self._repo.add(user, self._identity_row(user, identity))
            try:
                user = await self._repo.commit_user(user.id)
            except IntegrityError:
                await self._repo.rollback()
                existing = await self._repo.get_identity(identity.provider, identity.provider_key, identity.subject)
                if existing is not None:
                    return await self._seen(existing, identity, provider, now)
                if attempt == _JIT_ATTEMPTS - 1:
                    raise
                continue
            await get_dashboard_users_cache().invalidate()
            AuditService.log_async(
                "user_created",
                actor_ip=actor_ip,
                details={"username": user.username, "role": role_slug, "jit": True, **_identity_details(identity)},
                actor=AuditActor(user_id=None, username=None, role_slug=None, auth_method=identity.provider),
                target=AuditTarget("user", user.id),
            )
            self._audit_linked(user, identity, actor_ip, via="jit")
            return ResolvedAccount(user_id=user.id)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _jit_role(
        self, provider: DashboardAuthProvider, identity: ExternalIdentity
    ) -> DashboardRoleRecord | None:
        """The role a new account gets: its highest-priority matching rule, else the provider's default.

        The rules are consulted even when role sync is off — ``skip_role_sync``
        stops the resolver from moving accounts that already exist, not from
        giving a new one the role it is entitled to.
        """

        rows: Sequence[DashboardRoleMapping] = await self._mappings.list_for_provider(
            identity.provider, identity.provider_key
        )
        role_id = match_role_id(rows, groups=identity.groups, email=identity.email)
        if role_id is None:
            role_id = provider.unknown_identity_role_id
        if role_id is None:
            return None
        role = await self._roles.get_role(role_id)
        if role is None:
            logger.warning(
                "auth_provider_jit_role_missing provider=%s role_id=%s",
                provider.kind,
                role_id,
            )
        return role

    async def _free_username(self, subject: str) -> str:
        """First candidate no account holds. ``admin`` is reserved for the local
        break-glass account even before it exists -- and stays reserved after that
        account is renamed away from it -- so a proxy user named admin becomes
        ``admin-2`` and the name the recovery runbooks use never means someone else."""

        for candidate in jit_username_candidates(slugify_subject(subject)):
            if candidate == COMPAT_ADMIN_USERNAME:
                continue
            if await self._repo.get_by_username(candidate) is None:
                return candidate
        raise RuntimeError("unreachable")  # pragma: no cover

    # --- helpers ---

    async def _retry_seen(
        self, identity: ExternalIdentity, provider: DashboardAuthProvider, now: datetime
    ) -> Resolution:
        existing = await self._repo.get_identity(identity.provider, identity.provider_key, identity.subject)
        if existing is None:
            return Denied("identity_not_provisioned")
        return await self._seen(existing, identity, provider, now)

    @staticmethod
    def _identity_row(user: DashboardUser, identity: ExternalIdentity) -> DashboardIdentity:
        return DashboardIdentity(
            id=str(uuid.uuid4()),
            user_id=user.id,
            provider=identity.provider,
            provider_key=identity.provider_key,
            subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            groups_json=_groups_snapshot(identity.groups),
            last_seen_at=_naive_now(),
        )

    @staticmethod
    def _audit_linked(user: DashboardUser, identity: ExternalIdentity, actor_ip: str | None, *, via: str) -> None:
        AuditService.log_async(
            "identity_linked",
            actor_ip=actor_ip,
            details={"username": user.username, "via": via, **_identity_details(identity)},
            actor=_user_actor(user, identity.provider),
            target=AuditTarget("user", user.id),
        )


class IdentityResolutionCache:
    """Per-identity resolution results, valid for the users-cache TTL.

    A cached :class:`ResolvedAccount` only names the account; the request path
    re-reads the account through the users cache, so a disable or role change
    is honoured within that TTL. A cached :class:`Denied` keeps one refused
    identity from spending a database round-trip and an audit row per request.
    """

    def __init__(self, *, ttl_seconds: float = 5.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._entries: dict[tuple[str, str, str], tuple[Resolution, float]] = {}
        self._lock = anyio.Lock()

    async def resolve(
        self, identity: ExternalIdentity, provider: DashboardAuthProvider, *, actor_ip: str | None
    ) -> Resolution:
        key = (identity.provider, identity.provider_key, identity.subject)
        now = time.monotonic()
        entry = self._entries.get(key)
        if entry is not None and now - entry[1] < self._ttl_seconds:
            return entry[0]
        async with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is not None and now - entry[1] < self._ttl_seconds:
                return entry[0]
            async with SessionLocal() as session:
                resolver = IdentityResolver(
                    DashboardUsersRepository(session),
                    DashboardRolesRepository(session),
                    RoleMappingsRepository(session),
                )
                result = await resolver.resolve(identity, provider, actor_ip=actor_ip)
            self._entries[key] = (result, now)
            return result

    def clear(self) -> None:
        self._entries.clear()


_identity_resolution_cache = IdentityResolutionCache()


def get_identity_resolution_cache() -> IdentityResolutionCache:
    return _identity_resolution_cache

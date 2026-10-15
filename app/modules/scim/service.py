"""What a SCIM push means in this product's own terms.

Three rules shape everything below. An account is resolved by its identity
triple and never by a ``userName`` — the pushed name is an input to naming, not
a claim about which account is meant. Every read and write is bound to the
calling token's ``provider_key``, which is the principal-free stand-in for
``assert_can_act_on``. And a pushed role is bounded in code below admin level,
because a bearer token holds no grants and could therefore never satisfy the
delegation check every other role-assignment path in this product passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, NoReturn

from pydantic import ValidationError

from app.core.audit.service import AuditActor, AuditDetails, AuditService, AuditSeverity, AuditTarget
from app.core.audit.types import AuditAuthMethod
from app.core.auth.dashboard_access import PresetRoleSlug, is_admin_level
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers import ExternalIdentity
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import (
    DashboardIdentity,
    DashboardRoleRecord,
    DashboardUser,
    DashboardUserRoleSource,
    DashboardUserStatus,
)
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_roles.service import (
    RoleNotAssignableError,
    resolve_assignable_role,
    resolve_role_grants,
    role_assignable_to_users,
)
from app.modules.dashboard_users.break_glass import LastBreakGlassProtectedError
from app.modules.dashboard_users.identity_resolver import IdentityResolver, InviteLinkFailure
from app.modules.dashboard_users.repository import DashboardUsersRepository, normalize_email, utc_now
from app.modules.dashboard_users.service import (
    DashboardUsersService,
    InvitePendingError,
    LastAdminProtectedError,
    UserNotFoundError,
)
from app.modules.scim import errors
from app.modules.scim.repository import MAX_LIST_COUNT, SCIM_PROVIDER, ScimUsersRepository
from app.modules.scim.schemas import (
    LIST_RESPONSE_SCHEMA,
    USER_SCHEMA,
    ScimMultiValue,
    ScimPatchRequest,
    ScimUserRequest,
    coerce_active,
)

#: Where ``source`` lands in the audit rows the shared lifecycle functions write.
AUDIT_SOURCE = "scim"
#: A ``userName`` that slugifies to nothing becomes this plus a digest, so a
#: local account name says which path provisioned it.
SLUG_PREFIX = "scim-"
#: The role a create with no ``roles`` attribute gets. A stated default for an
#: *absent* attribute — never a fallback for a refused one, which is the
#: failure mode this surface exists not to repeat.
DEFAULT_ROLE_SLUG = PresetRoleSlug.VIEWER.value

#: RFC 7644 §3.4.2.2 defines a whole filter grammar. This surface answers the
#: one expression an identity provider needs to reconcile the resources it
#: created and refuses the rest by name rather than half-implementing it.
_USER_NAME_FILTER = re.compile(r'^\s*userName\s+eq\s+(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)\s*$', re.IGNORECASE)
_SUPPORTED_FILTER = 'userName eq "<value>"'
_PATCH_OPS = frozenset({"add", "replace"})
#: Which resource attribute a ``path`` may name, keyed case-folded. Operations
#: without a path (Okta's spelling) carry the same names as keys of an object
#: instead, and go through the very same table.
_PATCH_ATTRIBUTES: dict[str, str] = {
    "active": "active",
    "username": "userName",
    "displayname": "displayName",
    "externalid": "externalId",
    "emails": "emails",
    "roles": "roles",
    "name": "name",
}
#: The attributes a request may name. ``externalId`` is in the set because a
#: ``PUT`` carries it, but it names no change: it is re-read as an immutability
#: guard and never written.
_ATTRIBUTES = frozenset({"userName", "displayName", "emails", "active", "roles", "externalId", "name"})
#: The two whose value is a list of ``{"value": …}`` entries on the wire.
_MULTI_VALUED = frozenset({"emails", "roles"})

_ROLE_REFUSAL_DETAILS: dict[str, str] = {
    "unknown_role": "No role named '{value}' exists on this install.",
    "role_not_assignable": (
        "The role '{value}' exists but is not one this release assigns to an account, so it cannot be provisioned."
    ),
    "role_not_provisionable": (
        "The role '{value}' is administrative; an administrator assigns it by hand and a provisioning push cannot."
    ),
}
#: The audit action written when a pushed role is deliberately not applied.
#: The push still succeeds; the row exists so an operator can see that the
#: identity provider's role did not win, and it is read back by the next push
#: so that it is written once per account rather than on every sync.
ROLE_KEPT_ACTION = "scim_role_push_ignored"


def scim_actor() -> AuditActor:
    """Who a push is attributed to. There is no account, so nothing pretends there is."""

    return AuditActor(user_id=None, username=None, role_slug=None, auth_method=AuditAuthMethod.SCIM.value)


@dataclass(frozen=True, slots=True)
class ScimChanges:
    """What one request asks to change, whichever verb asked.

    Membership of ``touched`` is the difference between "set this to nothing"
    and "leave this alone", and it is the *only* thing the two verbs disagree
    about: a ``PUT`` replaces the resource, so an attribute it omits is
    cleared, while a ``PATCH`` names the attributes it means and every other
    one survives untouched. Both arrive here through one reading of one
    request model, so no other difference between them is possible.
    """

    touched: frozenset[str] = field(default_factory=frozenset)
    user_name: str | None = None
    display_name: str | None = None
    email: str | None = None
    active: bool | None = None
    role_slug: str | None = None

    def has(self, attribute: str) -> bool:
        return attribute in self.touched

    def with_value(self, attribute: str, **values: Any) -> ScimChanges:
        return replace(self, touched=self.touched | {attribute}, **values)


def _primary(values: list[ScimMultiValue]) -> str | None:
    for entry in values:
        if entry.primary and entry.value:
            return entry.value
    for entry in values:
        if entry.value:
            return entry.value
    return None


def _display_name(payload: ScimUserRequest) -> str | None:
    if payload.display_name:
        return payload.display_name
    if payload.name is None:
        return None
    if payload.name.formatted:
        return payload.name.formatted
    parts = [part for part in (payload.name.given_name, payload.name.family_name) if part]
    return " ".join(parts) or None


def _pushed_role(values: list[ScimMultiValue]) -> str | None:
    """At most one role travels on a push; two would be an ambiguity, not a set."""

    if not values:
        return None
    if len(values) > 1:
        raise errors.invalid_value("Exactly one role may be provisioned for an account.")
    return values[0].value if values[0].value is not None else ""


def _as_multi_valued(value: Any) -> Any:
    """A patched multi-valued attribute in the one shape the request model reads.

    Identity providers spell ``emails`` and ``roles`` three ways in a patch —
    a bare string, one object, or the list the resource schema actually
    declares — and only the third is what a ``PUT`` body carries. Widening the
    first two here rather than parsing them separately is what lets both verbs
    share one model, one set of length caps and one ``primary`` rule.
    """

    if isinstance(value, str):
        return [{"value": value}]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [{"value": entry} if isinstance(entry, str) else entry for entry in value]
    return value


def _record(attributes: dict[str, Any], path: str, value: Any) -> None:
    """Fold one patch operation into the resource attributes it names."""

    attribute = _PATCH_ATTRIBUTES.get(path.strip().casefold())
    if attribute is None:
        # Ignored, not refused — the rule a replace body already follows.
        # `title`, `locale`, phone numbers and an enterprise extension ride on
        # every routine Okta or Entra push, and refusing them would turn that
        # push into a 400 while protecting nothing the byte bound does not.
        return
    if attribute in attributes:
        raise errors.invalid_syntax(f"The attribute '{attribute}' is named more than once in one request.")
    attributes[attribute] = _as_multi_valued(value) if attribute in _MULTI_VALUED else value


def _validated(attributes: dict[str, Any]) -> ScimUserRequest:
    """The folded patch, read by the model a ``PUT`` body is read by.

    This is the whole of "the two verbs agree": a patch is turned into the
    attributes of a user resource and then validated exactly once, by the same
    model, so the length caps, the cardinality caps and the ``primary`` rule
    cannot be present on one verb and missing on the other. A refusal is
    spelled the way the same over-long value is spelled on a ``PUT``.
    """

    try:
        return ScimUserRequest.model_validate(attributes)
    except ValidationError as exc:
        raise errors.invalid_syntax("The request body is not a valid SCIM resource.") from exc


def _stamp(value: datetime | None) -> str | None:
    """A naive-UTC column as an RFC 3339 instant, the way the rest of the API renders them."""

    if value is None:
        return None
    return value.isoformat() + "Z" if value.tzinfo is None else value.isoformat().replace("+00:00", "Z")


class ScimUsersService:
    def __init__(
        self,
        scim_repository: ScimUsersRepository,
        users_repository: DashboardUsersRepository,
        roles: DashboardRolesRepository,
        resolver: IdentityResolver,
        users_service: DashboardUsersService,
    ) -> None:
        self._scim = scim_repository
        self._users = users_repository
        self._roles = roles
        self._resolver = resolver
        self._service = users_service

    # --- projection ---

    def resource(self, identity: DashboardIdentity, *, base_path: str) -> dict[str, Any]:
        """The SCIM user resource.

        ``userName`` is what the identity provider pushed, not the local
        account name: the two differ by the slug rules, and the identity
        provider reconciles on its own spelling.
        """

        user = identity.user
        role_slug = user.role.slug if self._provisionable(user.role) else None
        return {
            "schemas": [USER_SCHEMA],
            "id": user.id,
            "externalId": identity.subject,
            "userName": identity.user_name or user.username,
            "displayName": user.display_name,
            "active": user.status == DashboardUserStatus.ACTIVE.value,
            "emails": [{"value": identity.email, "primary": True}] if identity.email else [],
            "roles": [{"value": role_slug}] if role_slug is not None else [],
            "meta": {
                "resourceType": "User",
                "created": _stamp(user.created_at),
                "lastModified": _stamp(user.updated_at),
                # Root-relative on purpose: an absolute URI would have to be
                # built from a caller-supplied ``Host``, and this install has
                # no configured public base URL to build one from honestly.
                "location": f"{base_path}/Users/{user.id}",
            },
        }

    def list_response(
        self, identities: list[DashboardIdentity], *, total: int, start_index: int, base_path: str
    ) -> dict[str, Any]:
        return {
            "schemas": [LIST_RESPONSE_SCHEMA],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(identities),
            "Resources": [self.resource(identity, base_path=base_path) for identity in identities],
        }

    @staticmethod
    def _provisionable(role: DashboardRoleRecord) -> bool:
        """Whether this surface would hand that role out; it reports no other."""

        return role_assignable_to_users(role) and not is_admin_level(resolve_role_grants(role))

    # --- reads ---

    async def require_identity(self, provider_key: str, user_id: str) -> DashboardIdentity:
        """The resource a SCIM ``id`` names, inside this token's namespace only.

        An account with no ``scim`` identity here answers ``404`` whether it
        does not exist or belongs to another namespace: distinguishing the two
        would turn this surface into an account enumerator, which is the
        ``users:manage`` read the token is denied.
        """

        identity = await self._scim.get_identity_by_user(provider_key, user_id)
        if identity is None:
            raise errors.not_found()
        return identity

    @staticmethod
    def parse_filter(raw: str | None) -> str | None:
        if raw is None or not raw.strip():
            return None
        match = _USER_NAME_FILTER.match(raw)
        if match is None:
            raise errors.invalid_filter(f"The only supported filter is {_SUPPORTED_FILTER}.")
        return match.group("value")

    async def list_users(
        self, provider_key: str, *, user_name: str | None, start_index: int, count: int
    ) -> tuple[list[DashboardIdentity], int]:
        rows, total = await self._scim.list_identities(
            provider_key,
            user_name=user_name,
            offset=max(start_index, 1) - 1,
            limit=min(max(count, 0), MAX_LIST_COUNT),
        )
        return list(rows), total

    # --- the role gate ---

    async def resolve_pushed_role(
        self,
        raw: str | None,
        *,
        token_id: str,
        external_id: str,
        actor_ip: str | None,
    ) -> DashboardRoleRecord:
        """The role a push may hand out, or a refusal naming the value it refused.

        A SCIM bearer token is not a principal and holds no grants, so
        ``assert_can_delegate`` — the gate every other role-assignment path in
        this product passes — can never be satisfied here. The bound is
        therefore in code: an assignable role that is not admin-level. An
        administrative slug is refused with a reason of its own rather than
        quietly downgraded, because an operator told "admin is not
        provisionable" can act, and one whose admins silently became viewers
        cannot.
        """

        slug = DEFAULT_ROLE_SLUG if raw is None else raw.strip().casefold()
        if not slug:
            self._refuse_role("unknown_role", raw or "", token_id, external_id, actor_ip)
        found = await self._roles.get_role_by_slug(slug)
        if found is None:
            self._refuse_role("unknown_role", raw or slug, token_id, external_id, actor_ip)
        try:
            role = await resolve_assignable_role(self._roles, found.id)
        except RoleNotAssignableError:
            self._refuse_role("role_not_assignable", raw or slug, token_id, external_id, actor_ip)
        if is_admin_level(resolve_role_grants(role)):
            self._refuse_role("role_not_provisionable", raw or slug, token_id, external_id, actor_ip)
        return role

    @staticmethod
    def _refuse_role(reason: str, value: str, token_id: str, external_id: str, actor_ip: str | None) -> NoReturn:
        details: AuditDetails = {
            "reason": reason,
            "role": value,
            "external_id": external_id,
            "scim_token_id": token_id,
        }
        AuditService.log_async(
            "scim_provision_refused",
            actor_ip=actor_ip,
            details=details,
            actor=scim_actor(),
            severity=AuditSeverity.WARNING,
        )
        raise errors.invalid_value(_ROLE_REFUSAL_DETAILS[reason].format(value=value))

    # --- create ---

    async def create_user(
        self,
        payload: ScimUserRequest,
        *,
        provider_key: str,
        token_id: str,
        actor_ip: str | None,
    ) -> DashboardIdentity:
        user_name = (payload.user_name or "").strip()
        if not user_name:
            raise errors.invalid_value("userName is required.")
        external_id = (payload.external_id or "").strip()
        if not external_id:
            raise errors.invalid_value("externalId is required: it is the subject the account is keyed by.")
        active = True if payload.active is None else coerce_active(payload.active)
        if active is None:
            raise errors.invalid_value("active must be a boolean.")

        existing = await self._scim.get_identity_by_subject(provider_key, external_id)
        if existing is not None:
            raise errors.already_exists(existing.user_id)
        role = await self.resolve_pushed_role(
            _pushed_role(payload.roles), token_id=token_id, external_id=external_id, actor_ip=actor_ip
        )
        identity = ExternalIdentity(
            provider=SCIM_PROVIDER,
            provider_key=provider_key,
            subject=external_id,
            email=normalize_email(_primary(payload.emails)),
            display_name=_display_name(payload),
        )
        user = await self._provision(identity, role, user_name=user_name, actor_ip=actor_ip)
        AuditService.log_async(
            "scim_user_provisioned",
            actor_ip=actor_ip,
            details={
                "username": user.username,
                "external_id": external_id,
                "role": role.slug,
                "scim_token_id": token_id,
            },
            actor=scim_actor(),
            target=AuditTarget("user", user.id),
        )
        if not active:
            await self._set_active(user.id, False, token_id=token_id, actor_ip=actor_ip)
        return await self.require_identity(provider_key, user.id)

    async def _provision(
        self, identity: ExternalIdentity, role: DashboardRoleRecord, *, user_name: str, actor_ip: str | None
    ) -> DashboardUser:
        """Reuse the resolver's rules rather than keep a second copy of them.

        An invite pre-created for exactly this triple is honoured first, the
        way a first sign-in would honour it, so a push cannot create a second
        account beside one an administrator already prepared.
        """

        now = utc_now()
        invite = await self._users.find_invite_expecting_identity(
            identity.provider, identity.provider_key, identity.subject, now
        )
        if invite is not None:
            linked = await self._resolver.link_expected_invite(
                invite, identity, now=now, actor_ip=actor_ip, user_name=user_name, record_login=False
            )
            if not isinstance(linked, InviteLinkFailure):
                return linked
            if linked.reason == "raced":
                raced = await self._scim.get_identity_by_subject(identity.provider_key, identity.subject)
                if raced is not None:
                    raise errors.already_exists(raced.user_id)
        provisioned = await self._resolver.provision_account(
            identity,
            role_id=role.id,
            role_slug=role.slug,
            role_source=DashboardUserRoleSource.SCIM.value,
            actor_ip=actor_ip,
            user_name=user_name,
            username_source=user_name,
            empty_slug_prefix=SLUG_PREFIX,
            record_login=False,
            via=SCIM_PROVIDER,
        )
        if isinstance(provisioned, DashboardIdentity):
            raise errors.already_exists(provisioned.user_id)
        return provisioned

    # --- update ---

    def changes_from_replace(self, identity: DashboardIdentity, payload: ScimUserRequest) -> ScimChanges:
        """A ``PUT`` replaces the resource, so it names every attribute.

        What it omits is cleared rather than kept, which is the whole of the
        difference between the two verbs: the reading of each named attribute
        is one function they share.
        """

        return self._changes(identity, payload, _ATTRIBUTES)

    def changes_from_patch(self, identity: DashboardIdentity, payload: ScimPatchRequest) -> ScimChanges:
        """Fold a PatchOp into a user resource and read it as a replace body.

        Identity providers spell the same intent two ways — a ``path`` naming
        one attribute, or no path and an object of attributes — so the fold
        accepts both and then hands the result to the one parser. A patch is
        therefore not a second, weaker reading of the same wire format: it
        cannot forget a length cap, a ``primary`` flag or the one-role rule,
        because it does not read any of them itself.

        Two rules make the fold honest. An attribute this resource does not
        model is ignored exactly as a ``PUT`` body ignores it — every identity
        provider sends more than a core user — and an attribute named twice in
        one request is refused, because folding two writes of one attribute
        would have to discard one of them, and the discarded one could be the
        value that should have been refused.
        """

        attributes: dict[str, Any] = {}
        for operation in payload.operations:
            if operation.op.strip().casefold() not in _PATCH_OPS:
                raise errors.invalid_syntax(f"The operation '{operation.op}' is not supported.")
            if operation.path is not None:
                _record(attributes, operation.path, operation.value)
                continue
            if not isinstance(operation.value, dict):
                raise errors.invalid_path("An operation without a path must carry an object of attributes.")
            for attribute, value in operation.value.items():
                _record(attributes, attribute, value)
        return self._changes(identity, _validated(attributes), frozenset(attributes))

    def _changes(self, identity: DashboardIdentity, payload: ScimUserRequest, named: frozenset[str]) -> ScimChanges:
        """The one reading of a user resource, for both verbs.

        ``named`` is the only thing the two verbs disagree about: a ``PUT``
        names every attribute, a ``PATCH`` names the ones it carries. An
        attribute that is named but carries nothing — a null ``active``, an
        empty ``roles`` array — states no intent and is not a change, which is
        what makes a ``PUT`` that omits ``roles`` and a ``PATCH`` that does not
        mention it behave the same way.
        """

        external_id = (payload.external_id or "").strip()
        if external_id and external_id != identity.subject:
            raise errors.immutable("externalId identifies the account and cannot be changed.")
        changes = ScimChanges()
        if "userName" in named:
            user_name = (payload.user_name or "").strip()
            if not user_name:
                raise errors.invalid_value("userName is required and cannot be cleared.")
            changes = changes.with_value("userName", user_name=user_name)
        if named & {"displayName", "name"}:
            changes = changes.with_value("displayName", display_name=_display_name(payload))
        if "emails" in named:
            changes = changes.with_value("emails", email=normalize_email(_primary(payload.emails)))
        if "active" in named and payload.active is not None:
            active = coerce_active(payload.active)
            if active is None:
                raise errors.invalid_value("active must be a boolean.")
            changes = changes.with_value("active", active=active)
        if "roles" in named and payload.roles:
            changes = changes.with_value("roles", role_slug=_pushed_role(payload.roles))
        return changes

    async def apply(
        self,
        identity: DashboardIdentity,
        changes: ScimChanges,
        *,
        provider_key: str,
        token_id: str,
        actor_ip: str | None,
    ) -> DashboardIdentity:
        """Refuse first, write only what changes, commit once.

        The three phases are the shape of this method and each exists because
        mixing it into the others produced a defect.

        *Refuse first.* Every guard a push can fail — the pushed role, and the
        one direction that grants access — is evaluated before a single
        attribute is written, off a row no commit has expired yet.

        *Write only what changes.* A redelivered push whose values already
        hold writes nothing at all, so the idempotency the lifecycle functions
        promise is not undone by an attribute update committed beside them.

        *Commit once.* The attribute writes stay pending and are carried by
        the lifecycle function's own commit, so a deactivation refused by the
        break-glass or last-admin invariant takes the rest of the request back
        with it instead of leaving half of it applied.
        """

        user = identity.user
        # Read off the rows while they are still loaded: the commit below
        # expires every attribute, and reading one back then is synchronous IO
        # in an async context.
        user_id = user.id
        username = user.username
        subject = identity.subject

        # --- 1. every refusal, before anything is written ---
        moved: DashboardRoleRecord | None = None
        kept: str | None = None
        pushed_slug: str | None = None
        if changes.has("roles"):
            pushed = await self.resolve_pushed_role(
                changes.role_slug, token_id=token_id, external_id=subject, actor_ip=actor_ip
            )
            pushed_slug = pushed.slug
            # A role a person pinned by hand is theirs, and a role this surface
            # would not hand out is not one it may take away either: it moves
            # only on an account this surface both manages and could itself
            # have provisioned. The rest of the push applies either way.
            if user.role_id == pushed.id:
                pushed_slug = None
            elif user.role_source != DashboardUserRoleSource.SCIM.value:
                kept = "role_source_manual"
            elif not self._provisionable(user.role):
                kept = "role_not_provisionable"
            else:
                moved = pushed
        if changes.active is True and user.status != DashboardUserStatus.ACTIVE.value:
            # The asymmetry this closes: deprovision re-checks break-glass and
            # last-admin, a role push re-checks assignability and role_source,
            # and the one direction that *grants* access used to re-check
            # nothing — so ``active: true`` re-enabled an administrator a human
            # had disabled, and revived every key the disable had cascaded.
            # Read here rather than after the write, because the commit below
            # expires every attribute of the row this predicate reads.
            if not self._provisionable(moved if moved is not None else user.role):
                self._refuse_enable(user, token_id=token_id, external_id=subject, actor_ip=actor_ip)

        # --- 2. only what actually changes ---
        written = False
        if moved is not None:
            user.role_id = moved.id
            written = True
        if changes.has("displayName"):
            if user.display_name != changes.display_name:
                user.display_name = changes.display_name
                written = True
            if identity.display_name != changes.display_name:
                identity.display_name = changes.display_name
                written = True
        if changes.has("userName") and identity.user_name != changes.user_name:
            # The local account name is assigned once. A rename at the identity
            # provider changes what it calls the person, never who the account
            # is here: renaming from a machine push would race this install's
            # own uniqueness rules and could move another account's name.
            identity.user_name = changes.user_name
            written = True
        if changes.has("emails") and identity.email != changes.email:
            # On the identity row only. ``dashboard_users.email`` is UNIQUE
            # across the install, so writing a pushed address there would let
            # one identity provider's update fail — or, worse, contend — over
            # an address a local account already holds. The resource reports
            # the identity's address, so the push still round-trips.
            identity.email = changes.email
            written = True
        if written:
            # Naive UTC: the identity timestamp columns are naive on both
            # dialects. Stamped only beside a real write, so "this push
            # changed nothing" stays literally true.
            identity.last_seen_at = utcnow()

        # --- 3. one transaction ---
        committed = False
        active = changes.active
        if active is not None:
            committed = await self._set_active(user_id, active, token_id=token_id, actor_ip=actor_ip)
        if written and not committed:
            await self._users.commit_user(user_id)
        elif not (written or committed):
            # Nothing to write. Release the accounts write intent a no-op
            # lifecycle call took, rather than holding the single writer slot
            # until the response has been built.
            await self._users.rollback()
        if moved is not None:
            await get_dashboard_users_cache().invalidate()
        if kept is not None and pushed_slug is not None:
            await self._audit_role_kept(
                user_id,
                username,
                kept,
                pushed_slug,
                token_id=token_id,
                external_id=subject,
                actor_ip=actor_ip,
            )
        return await self.require_identity(provider_key, user_id)

    def _refuse_enable(self, user: DashboardUser, *, token_id: str, external_id: str, actor_ip: str | None) -> NoReturn:
        """The enable direction, bounded by the predicate the role push uses.

        An account holding a role this surface does not provision is not an
        account it may switch back on: enabling it would restore both the
        account and every key its disable cascaded, and a bearer token holds no
        grant that could justify either. The detail names the account and not
        the role, because the resource deliberately reports no ``roles`` for
        exactly these accounts; the administrator-facing reason is in the
        audit row, which is where an administrator looks.
        """

        details: AuditDetails = {
            "reason": "account_not_provisionable",
            "username": user.username,
            "role": user.role.slug,
            "external_id": external_id,
            "scim_token_id": token_id,
        }
        AuditService.log_async(
            "scim_provision_refused",
            actor_ip=actor_ip,
            details=details,
            actor=scim_actor(),
            target=AuditTarget("user", user.id),
            severity=AuditSeverity.WARNING,
        )
        raise errors.conflict(
            f"The account '{user.username}' holds a role this surface does not provision, "
            "so a provisioning push cannot re-enable it; an administrator enables it by hand."
        )

    async def _audit_role_kept(
        self,
        user_id: str,
        username: str,
        reason: str,
        slug: str,
        *,
        token_id: str,
        external_id: str,
        actor_ip: str | None,
    ) -> None:
        """One row the first time a pushed role is ignored, and none after it.

        An identity provider re-pushes its whole directory on a schedule, so a
        row per push would bury the one an operator needs. The row already
        written is the marker, which is why this one event is awaited rather
        than fired and forgotten: the next push reads it back to decide whether
        to write, so it has to have committed before this one answers. It is
        written outside the request's own transaction, after the commit above,
        for the same reason.
        """

        if await self._scim.audit_row_exists(ROLE_KEPT_ACTION, user_id):
            return
        details: AuditDetails = {
            "reason": reason,
            "username": username,
            "role": slug,
            "external_id": external_id,
            "scim_token_id": token_id,
        }
        await AuditService.log(
            ROLE_KEPT_ACTION,
            actor_ip=actor_ip,
            details=details,
            request_id=get_request_id(),
            actor=scim_actor(),
            target=AuditTarget("user", user_id),
            severity=AuditSeverity.WARNING,
        )

    # --- the lifecycle, through the shared functions ---

    async def _set_active(self, user_id: str, active: bool, *, token_id: str, actor_ip: str | None) -> bool:
        """One cascade, whoever asked for it; ``True`` when it wrote and committed.

        ``deactivate_user`` already emits ``scim_deprovision_refused`` next to
        raising, so this maps the refusal to a ``409`` and deliberately does
        **not** write a second row for it. Both functions return ``False``
        without writing or committing when the account is already in the state
        asked for, and the caller carries its own pending writes accordingly.
        """

        actor = scim_actor()
        try:
            if active:
                return await self._service.reactivate_user(user_id, actor=actor, actor_ip=actor_ip, source=AUDIT_SOURCE)
            changed = await self._service.deactivate_user(user_id, actor=actor, actor_ip=actor_ip, source=AUDIT_SOURCE)
        except LastBreakGlassProtectedError as exc:
            raise errors.conflict(str(exc)) from exc
        except LastAdminProtectedError as exc:
            raise errors.conflict(str(exc)) from exc
        except InvitePendingError as exc:
            raise errors.conflict(str(exc)) from exc
        except UserNotFoundError as exc:
            raise errors.not_found() from exc
        if changed:
            AuditService.log_async(
                "scim_user_deprovisioned",
                actor_ip=actor_ip,
                details={"scim_token_id": token_id},
                actor=actor,
                target=AuditTarget("user", user_id),
            )
        return changed

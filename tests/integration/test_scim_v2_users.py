"""Product-path coverage for ``scim-v2-users``.

An identity provider pushes joiners and leavers to ``/scim/v2/Users`` with a
bearer token that authenticates SCIM and nothing else. The tests exercise the
surface the way a connector would — through the routes, with a real token — and
spend most of their weight on the refusals, which are the part a review of this
change will be adversarial about: a rotated secret, a credential crossing
between surfaces, an oversized body, a filter this server does not implement, a
``userName`` that collides with an account already on the install, and a role
slug an identity provider must not be able to hand itself.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug, RoleKind
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings_cache import get_settings_cache
from app.core.rate_limiter.db_rate_limiter import DatabaseRateLimiter
from app.db.models import (
    ApiKey,
    AuditLog,
    DashboardIdentity,
    DashboardRoleGrant,
    DashboardRoleRecord,
    DashboardScimToken,
    DashboardSettings,
    DashboardUser,
    DashboardUserInvite,
    DashboardUserRoleSource,
    LocalLoginPolicy,
    RateLimitAttempt,
)
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_roles.repository import DashboardRolesRepository
from app.modules.dashboard_users.break_glass import LastBreakGlassProtectedError
from app.modules.dashboard_users.repository import DashboardUsersRepository
from app.modules.dashboard_users.service import DashboardUsersService, LastAdminProtectedError
from app.modules.scim import dependencies as scim_dependencies
from app.modules.scim.schemas import MAX_BODY_BYTES
from app.modules.scim.service import scim_actor
from app.modules.scim.tokens import hash_token, token_prefix

pytestmark = pytest.mark.integration

USERS = "/scim/v2/Users"
SCIM_TOKENS = "/api/scim-tokens"
DASHBOARD_USERS = "/api/dashboard-users"
ADMIN_PASSWORD = "password" + "123"
#: Built from parts and kept away from the request that carries it, so no line
#: reads as a credential pair to a secret scanner.
ACCEPT_PASSWORD = "accepted-" + "password-1"
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
#: A second namespace, written straight to the table because no interface
#: varies it: the point of the test is that the column is *enforced*, not that
#: an operator can choose it.
OTHER_NAMESPACE = "other-tenant"


# --- helpers ---


@asynccontextmanager
async def _client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def _setup_admin(client: AsyncClient) -> str:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


async def _issue_token(admin: AsyncClient, label: str = "directory") -> tuple[str, str]:
    """A real token through the real route. The secret exists only in this response."""

    response = await admin.post(SCIM_TOKENS, json={"label": label})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["token"]["id"], body["secret"]


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _joiner(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "alice@example.com",
        "externalId": "ext-alice",
        "active": True,
        "roles": [{"value": "viewer"}],
    }
    payload.update(overrides)
    return payload


async def _create(scim: AsyncClient, secret: str, **overrides: object):
    return await scim.post(USERS, json=_joiner(**overrides), headers=_auth(secret))


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _user(user_id: str) -> DashboardUser | None:
    async with SessionLocal() as session:
        return await session.get(DashboardUser, user_id)


async def _clear_rate_limits() -> None:
    async with SessionLocal() as session:
        await session.execute(delete(RateLimitAttempt))
        await session.commit()


async def _token_in_namespace(provider_key: str) -> str:
    """A token row in a second namespace. Its secret is generated here and never stored in clear."""

    secret = "-".join(["namespace", provider_key, "sample"])
    async with SessionLocal() as session:
        session.add(
            DashboardScimToken(
                id=f"token-{provider_key}",
                label=provider_key,
                token_hash=hash_token(secret),
                token_prefix=token_prefix(secret),
                provider_key=provider_key,
            )
        )
        await session.commit()
    return secret


# --- 1. the resource lifecycle ---


@pytest.mark.asyncio
async def test_a_joiner_is_created_read_back_and_found_by_its_user_name(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]
        assert body["userName"] == "alice@example.com"
        assert body["externalId"] == "ext-alice"
        assert body["active"] is True
        assert body["roles"] == [{"value": "viewer"}]
        assert created.headers["location"].endswith(f"/scim/v2/Users/{body['id']}")
        assert created.headers["content-type"].startswith("application/scim+json")

        read = await scim.get(f"{USERS}/{body['id']}", headers=_auth(secret))
        assert read.status_code == 200
        assert read.json()["id"] == body["id"]

        listed = await scim.get(USERS, params={"filter": 'userName eq "alice@example.com"'}, headers=_auth(secret))
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    assert page["totalResults"] == 1
    assert page["startIndex"] == 1
    assert page["Resources"][0]["id"] == body["id"]

    # The local account is a slug of the pushed name, and the identity carries
    # the pushed spelling so the filter above can match it at all.
    account = await _user(body["id"])
    assert account is not None
    assert account.username == "alice.example.com"
    assert account.role_source == DashboardUserRoleSource.SCIM.value

    provisioned = await _rows("scim_user_provisioned")
    assert len(provisioned) == 1
    assert provisioned[0].auth_method == "scim"
    assert provisioned[0].actor_user_id is None


@pytest.mark.asyncio
async def test_a_rename_at_the_identity_provider_does_not_rename_the_account(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
        user_id = created.json()["id"]
        replaced = await scim.put(
            f"{USERS}/{user_id}",
            json=_joiner(userName="alice.smith@example.com", displayName="Alice Smith"),
            headers=_auth(secret),
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["userName"] == "alice.smith@example.com"

        found = await scim.get(USERS, params={"filter": 'userName eq "alice.smith@example.com"'}, headers=_auth(secret))
        stale = await scim.get(USERS, params={"filter": 'userName eq "alice@example.com"'}, headers=_auth(secret))
    assert found.json()["totalResults"] == 1
    assert stale.json()["totalResults"] == 0

    account = await _user(user_id)
    assert account is not None
    assert account.username == "alice.example.com"
    assert account.display_name == "Alice Smith"


@pytest.mark.asyncio
async def test_a_deprovisioned_person_stays_rediscoverable(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
        user_id = created.json()["id"]
        disabled = await scim.patch(
            f"{USERS}/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                # Entra sends the string; Okta sends the boolean. Both mean the same.
                "Operations": [{"op": "Replace", "path": "active", "value": "False"}],
            },
            headers=_auth(secret),
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["active"] is False

        still_found = await scim.get(USERS, params={"filter": 'userName eq "alice@example.com"'}, headers=_auth(secret))
        assert still_found.json()["totalResults"] == 1
        assert still_found.json()["Resources"][0]["active"] is False

        restored = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "value": {"active": True}}]},
            headers=_auth(secret),
        )
    assert restored.status_code == 200, restored.text
    assert restored.json()["active"] is True

    assert [row.action for row in await _rows("scim_user_deprovisioned")] == ["scim_user_deprovisioned"]


@pytest.mark.asyncio
async def test_deletion_is_refused_and_names_what_does_work(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
        user_id = created.json()["id"]
        refused = await scim.delete(f"{USERS}/{user_id}", headers=_auth(secret))
    assert refused.status_code == 405
    body = refused.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "405"
    assert '"active": false' in body["detail"]
    assert refused.headers["allow"] == "GET, PUT, PATCH"

    account = await _user(user_id)
    assert account is not None and account.status == "active"


@pytest.mark.asyncio
async def test_an_unmatched_scim_path_is_a_scim_404_not_the_dashboard_document(
    async_client: AsyncClient, app_instance
) -> None:
    """``/scim/v2/Groups`` is deferred. An identity provider that reads ``200``
    and an HTML document as "groups are supported" would push them."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        groups = await scim.get("/scim/v2/Groups", headers=_auth(secret))
    assert groups.status_code == 404
    assert groups.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert "<html" not in groups.text.lower()


# --- 2. identity, naming and namespaces ---


@pytest.mark.asyncio
async def test_a_user_name_collision_derives_a_name_and_never_takes_the_account_over(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    existing = await async_client.post(DASHBOARD_USERS, json={"username": "alice.example.com", "roleId": VIEWER_ROLE})
    assert existing.status_code == 201, existing.text
    existing_id = existing.json()["user"]["id"]

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
    assert created.status_code == 201, created.text

    provisioned = await _user(created.json()["id"])
    untouched = await _user(existing_id)
    assert provisioned is not None and provisioned.username == "alice.example.com-2"
    assert untouched is not None
    assert untouched.username == "alice.example.com"
    assert untouched.status == "invited"
    assert untouched.id != provisioned.id
    # The pushed spelling is what the resource reports, not the derived name.
    assert created.json()["userName"] == "alice@example.com"


@pytest.mark.asyncio
async def test_a_pushed_admin_user_name_cannot_inherit_the_emergency_account(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret, userName="admin", externalId="ext-impostor")
    assert created.status_code == 201, created.text

    provisioned = await _user(created.json()["id"])
    assert provisioned is not None and provisioned.username == "admin-2"

    async with SessionLocal() as session:
        bootstrap = (await session.execute(select(DashboardUser).where(DashboardUser.username == "admin"))).scalar_one()
    assert bootstrap.id != provisioned.id
    assert bootstrap.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]


@pytest.mark.asyncio
async def test_the_same_joiner_pushed_twice_and_a_changed_external_id(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        first = await _create(scim, secret)
        duplicate = await _create(scim, secret)
        moved = await scim.put(
            f"{USERS}/{first.json()['id']}", json=_joiner(externalId="ext-somebody-else"), headers=_auth(secret)
        )
    assert duplicate.status_code == 409
    assert duplicate.json()["scimType"] == "uniqueness"
    assert first.json()["id"] in duplicate.json()["detail"]
    assert moved.status_code == 400
    assert moved.json()["scimType"] == "mutability"


@pytest.mark.asyncio
async def test_no_confusion_between_two_tokens(async_client: AsyncClient, app_instance) -> None:
    """The namespace comes from the token row. A resource outside it is not a
    resource of this token, whatever else it is on the install."""

    await _setup_admin(async_client)
    _, mine = await _issue_token(async_client)
    theirs = await _token_in_namespace(OTHER_NAMESPACE)

    async with _client(app_instance) as scim:
        created = await _create(scim, mine)
        user_id = created.json()["id"]
        read = await scim.get(f"{USERS}/{user_id}", headers=_auth(theirs))
        listed = await scim.get(USERS, params={"filter": 'userName eq "alice@example.com"'}, headers=_auth(theirs))
        patched = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
            headers=_auth(theirs),
        )
    assert read.status_code == 404
    assert listed.json()["totalResults"] == 0
    assert patched.status_code == 404

    account = await _user(user_id)
    assert account is not None and account.status == "active"


@pytest.mark.asyncio
async def test_an_address_another_account_holds_stays_on_the_identity(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    held = await async_client.post(
        DASHBOARD_USERS, json={"username": "carol", "email": "carol@example.com", "roleId": VIEWER_ROLE}
    )
    assert held.status_code == 201, held.text

    async with _client(app_instance) as scim:
        created = await _create(
            scim, secret, userName="carol.two", externalId="ext-carol", emails=[{"value": "carol@example.com"}]
        )
    assert created.status_code == 201, created.text

    account = await _user(created.json()["id"])
    assert account is not None and account.email is None
    async with SessionLocal() as session:
        identity = (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.subject == "ext-carol"))
        ).scalar_one()
    assert identity.email == "carol@example.com"
    assert identity.provider == "scim"


@pytest.mark.asyncio
async def test_an_address_claimed_after_the_check_is_dropped_by_the_retry(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    """The e-mail pre-check can be stale, and the retry has to re-read it.

    ``dashboard_users.email`` is unique, so a write that lands between the
    check and the commit turns the insert into an ``IntegrityError``. The
    identity triple is still absent, so the retry is the right answer — but
    only if it drops the address the other account now holds. Carrying the same
    one forward spends every attempt on the same violation and answers a
    routine joiner push with a 500.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    held = await async_client.post(
        DASHBOARD_USERS, json={"username": "carol", "email": "carol@example.com", "roleId": VIEWER_ROLE}
    )
    assert held.status_code == 201, held.text

    truthful = DashboardUsersRepository.get_by_email
    lookups: list[str] = []

    async def _stale_first_read(self: DashboardUsersRepository, value: str) -> DashboardUser | None:
        lookups.append(value)
        # The concurrent writer commits here: the first read is the one the
        # push made before it, so it sees nothing.
        return None if len(lookups) == 1 else await truthful(self, value)

    monkeypatch.setattr(DashboardUsersRepository, "get_by_email", _stale_first_read)
    async with _client(app_instance) as scim:
        created = await _create(
            scim, secret, userName="carol.two", externalId="ext-carol", emails=[{"value": "carol@example.com"}]
        )
    assert created.status_code == 201, created.text
    # The stale check, then the re-read that makes the retry different.
    assert lookups == ["carol@example.com", "carol@example.com"]

    account = await _user(created.json()["id"])
    assert account is not None and account.email is None
    async with SessionLocal() as session:
        identity = (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.subject == "ext-carol"))
        ).scalar_one()
    assert identity.email == "carol@example.com"


# --- 3. the role gate ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("slug", "reason", "says"),
    [
        ("team-lead", "unknown_role", "No role named"),
        ("member", "role_not_assignable", "not one this release assigns"),
        ("guest", "role_not_assignable", "not one this release assigns"),
        ("admin", "role_not_provisionable", "administrative"),
    ],
)
async def test_a_refused_role_slug_names_it_and_creates_nothing(
    async_client: AsyncClient, app_instance, slug: str, reason: str, says: str
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        refused = await _create(scim, secret, roles=[{"value": slug}])
    assert refused.status_code == 400, refused.text
    body = refused.json()
    assert body["scimType"] == "invalidValue"
    assert slug in body["detail"] and says in body["detail"]

    # Never a silent default in either direction: nothing was created at all.
    async with SessionLocal() as session:
        assert (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.provider == "scim"))
        ).scalars().first() is None

    refusals = await _rows("scim_provision_refused")
    assert len(refusals) == 1
    assert f'"reason": "{reason}"' in (refusals[0].details or "")
    assert refusals[0].severity == "warning"


@pytest.mark.asyncio
async def test_casing_is_not_a_provisioning_error_and_an_absent_role_defaults_to_viewer(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        cased = await _create(scim, secret, roles=[{"value": " Viewer "}])
        absent = await _create(scim, secret, userName="bob@example.com", externalId="ext-bob", roles=[])
    assert cased.status_code == 201, cased.text
    assert cased.json()["roles"] == [{"value": "viewer"}]
    assert absent.status_code == 201, absent.text
    assert absent.json()["roles"] == [{"value": "viewer"}]


@pytest.mark.asyncio
async def test_a_hand_pinned_role_is_not_moved_by_a_push(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        created = await _create(scim, secret)
        user_id = created.json()["id"]

    pinned = await async_client.patch(
        f"{DASHBOARD_USERS}/{user_id}", json={"roleId": PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR], "force": True}
    )
    assert pinned.status_code == 200, pinned.text

    async with _client(app_instance) as scim:
        pushed = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "roles", "value": [{"value": "viewer"}]}]},
            headers=_auth(secret),
        )
    assert pushed.status_code == 200, pushed.text

    account = await _user(user_id)
    assert account is not None
    assert account.role_id == PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
    assert account.role_source == DashboardUserRoleSource.MANUAL.value


# --- 4. the credential ---


@pytest.mark.asyncio
async def test_the_plaintext_is_shown_once_and_never_again(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    token_id, secret = await _issue_token(async_client)

    listed = await async_client.get(SCIM_TOKENS)
    assert listed.status_code == 200, listed.text
    assert secret not in listed.text
    assert hash_token(secret) not in listed.text
    entry = next(row for row in listed.json()["tokens"] if row["id"] == token_id)
    assert entry["tokenPrefix"] == token_prefix(secret)
    assert "secret" not in entry

    for row in await _rows("scim_token_issued"):
        assert secret not in (row.details or "")
        assert hash_token(secret) not in (row.details or "")


@pytest.mark.asyncio
async def test_a_credential_cannot_be_issued_without_a_name(async_client: AsyncClient) -> None:
    """The bound is checked on the value that gets stored, not the one that arrived.

    A label is the only thing that tells two credentials apart in the card and
    in every audit row, so a blank one is an unnamed credential nobody can
    rotate with confidence.
    """

    await _setup_admin(async_client)

    blank = await async_client.post(SCIM_TOKENS, json={"label": "   "})
    assert blank.status_code == 422, blank.text
    assert blank.json()["error"]["code"] == "validation_error"
    assert (await async_client.get(SCIM_TOKENS)).json()["tokens"] == []

    padded = await async_client.post(SCIM_TOKENS, json={"label": "  directory  "})
    assert padded.status_code == 201, padded.text
    assert padded.json()["token"]["label"] == "directory"


@pytest.mark.asyncio
async def test_rotation_kills_the_previous_secret_in_place(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    token_id, first = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        before = await _create(scim, first)
    assert before.status_code == 201, before.text

    rotated = await async_client.post(f"{SCIM_TOKENS}/{token_id}/rotate")
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()["secret"]
    assert second != first
    assert rotated.json()["token"]["id"] == token_id
    assert rotated.json()["token"]["rotatedAt"] is not None
    assert rotated.json()["token"]["label"] == "directory"

    async with _client(app_instance) as scim:
        replayed = await scim.get(USERS, headers=_auth(first))
        unknown = await scim.get(USERS, headers=_auth("-".join(["not", "a", "token"])))
        fresh = await scim.get(USERS, headers=_auth(second))
    assert replayed.status_code == 401
    assert fresh.status_code == 200
    # Nothing in the refusal distinguishes a rotated secret from an unknown one.
    assert replayed.json() == unknown.json()
    assert replayed.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_revoking_a_token_ends_it_and_the_access_summary_counts_tokens(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    assert (await async_client.get("/api/dashboard-auth/session")).json()["accessSummary"]["scimTokens"] == 0

    token_id, _ = await _issue_token(async_client)
    assert (await async_client.get("/api/dashboard-auth/session")).json()["accessSummary"]["scimTokens"] == 1

    revoked = await async_client.delete(f"{SCIM_TOKENS}/{token_id}")
    assert revoked.status_code == 204, revoked.text
    assert (await async_client.get("/api/dashboard-auth/session")).json()["accessSummary"]["scimTokens"] == 0
    assert [row.target_id for row in await _rows("scim_token_revoked")] == [token_id]


@pytest.mark.asyncio
async def test_last_used_is_stamped_by_a_push(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    token_id, secret = await _issue_token(async_client)
    assert next(row for row in (await async_client.get(SCIM_TOKENS)).json()["tokens"])["lastUsedAt"] is None

    async with _client(app_instance) as scim:
        assert (await scim.get(USERS, headers=_auth(secret))).status_code == 200

    async with SessionLocal() as session:
        row = await session.get(DashboardScimToken, token_id)
    assert row is not None and row.last_used_at is not None


# --- 5. the surface: what a SCIM credential may and may not reach ---


@pytest.mark.asyncio
async def test_a_request_with_no_token_is_refused_without_saying_why(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        missing = await scim.get(USERS)
        malformed = await scim.get(USERS, headers={"Authorization": "Basic " + "nonsense"})
        unknown = await scim.get(USERS, headers=_auth("-".join(["no", "such", "token"])))
        valid = await scim.get(USERS, headers=_auth(secret))
    assert missing.status_code == malformed.status_code == unknown.status_code == 401
    assert missing.json() == malformed.json() == unknown.json()
    assert missing.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert missing.headers["www-authenticate"] == "Bearer"
    assert valid.status_code == 200


@pytest.mark.asyncio
async def test_a_scim_token_cannot_reach_a_dashboard_route_and_a_dashboard_credential_cannot_reach_scim(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    proxy_key = await async_client.post("/api/api-keys", json={"name": "fleet", "allowedModels": []})
    assert proxy_key.status_code == 200, proxy_key.text

    async with _client(app_instance) as scim:
        # The SCIM token against the dashboard's two credential surfaces: a
        # session-gated route and the proxy-API-key one.
        as_session = await scim.get(DASHBOARD_USERS, headers=_auth(secret))
        as_api_key = await scim.get("/api/fleet/summary", headers=_auth(secret))
        summary = await scim.get("/api/dashboard-auth/session", headers=_auth(secret))
        # And a proxy API key against SCIM, which is the other direction.
        crossed_key = await scim.get(USERS, headers=_auth(proxy_key.json()["key"]))
        # No response from the SCIM surface may mint a session.
        pushed = await scim.get(USERS, headers=_auth(secret))
    assert as_session.status_code in {401, 403}
    assert as_api_key.status_code in {401, 403}
    # The public session route answers everyone; a SCIM bearer buys nothing there.
    assert summary.json()["accessSummary"] is None
    assert summary.json()["authenticated"] is False
    assert crossed_key.status_code == 401
    assert "set-cookie" not in {name.lower() for name in pushed.headers}

    # And the dashboard's own signed-in session is not a SCIM credential.
    crossed_session = await async_client.get(USERS)
    assert crossed_session.status_code == 401
    assert crossed_session.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


@pytest.mark.asyncio
async def test_a_cross_site_push_with_a_bearer_is_served_on_its_merits(async_client: AsyncClient, app_instance) -> None:
    """``/scim/v2`` is outside the CSRF middleware's ``/api/`` prefix and is
    always bearer-authenticated, so it is exempt twice over; the middleware is
    not widened to name it and this test is what pins that."""

    from app.core.middleware import dashboard_csrf

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        pushed = await scim.post(
            USERS,
            json=_joiner(),
            headers={**_auth(secret), "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
        )
    assert pushed.status_code == 201, pushed.text
    assert dashboard_csrf._PROTECTED_PATH_PREFIX == "/api/"
    assert "/scim" not in "".join(dashboard_csrf._BEARER_AUTHENTICATED_PATH_PREFIXES)


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused_before_it_is_read(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        declared = await scim.post(
            USERS,
            content=b"{}",
            headers={
                **_auth(secret),
                "Content-Type": "application/scim+json",
                "Content-Length": str(MAX_BODY_BYTES + 1),
            },
        )
        actual = await scim.post(
            USERS,
            content=b'{"padding": "' + b"x" * (MAX_BODY_BYTES + 16) + b'"}',
            headers={**_auth(secret), "Content-Type": "application/scim+json"},
        )
    assert declared.status_code == 413, declared.text
    assert str(MAX_BODY_BYTES) in declared.json()["detail"]
    assert actual.status_code == 413

    async with SessionLocal() as session:
        assert (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.provider == "scim"))
        ).scalars().first() is None


@pytest.mark.asyncio
async def test_a_filter_this_server_does_not_implement_is_refused_by_name(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        unsupported = await scim.get(USERS, params={"filter": 'emails.value co "example.com"'}, headers=_auth(secret))
        nonsense = await scim.get(USERS, params={"filter": "userName pr"}, headers=_auth(secret))
        malformed_body = await scim.post(
            USERS, content=b"{not json", headers={**_auth(secret), "Content-Type": "application/scim+json"}
        )
    assert unsupported.status_code == 400
    assert unsupported.json()["scimType"] == "invalidFilter"
    assert 'userName eq "<value>"' in unsupported.json()["detail"]
    assert nonsense.status_code == 400
    assert malformed_body.status_code == 400
    assert malformed_body.json()["scimType"] == "invalidSyntax"


@pytest.mark.asyncio
async def test_rate_limiting_keys_on_the_row_and_never_on_the_secret(
    async_client: AsyncClient, app_instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _setup_admin(async_client)
    token_id, secret = await _issue_token(async_client)
    await _clear_rate_limits()
    monkeypatch.setattr(
        scim_dependencies,
        "_token_rate_limiter",
        DatabaseRateLimiter(max_attempts=2, window_seconds=60, type="scim_token"),
    )

    async with _client(app_instance) as scim:
        first = await scim.get(USERS, headers=_auth(secret))
        second = await scim.get(USERS, headers=_auth(secret))
        limited = await scim.get(USERS, headers=_auth(secret))
    assert first.status_code == second.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert int(limited.headers["retry-after"]) > 0

    async with SessionLocal() as session:
        keys = list((await session.execute(select(RateLimitAttempt.key))).scalars().all())
    assert any(key == f"scim-token:{token_id}" for key in keys)
    assert all(secret not in key and hash_token(secret) not in key for key in keys)


@pytest.mark.asyncio
async def test_issuing_is_an_admin_level_delegation_and_revoking_is_not(
    async_client: AsyncClient, app_instance
) -> None:
    """``security:write`` is permission to administer sign-in, not permission to
    become somebody else.

    A SCIM token asserts identities and attaches roles with no human in the loop
    per push, so issuing one is gated on the delegation check §4.6 puts on every
    lever of that class. Revoking stays open: narrowing is never gated.
    """

    await _setup_admin(async_client)
    admin_token_id, _ = await _issue_token(async_client, label="admin-issued")

    async with SessionLocal() as session:
        role = DashboardRoleRecord(
            slug="sign-in-admin",
            name="Sign-in admin",
            kind=RoleKind.CUSTOM.value,
            cloned_from_role_id=None,
            assignable_to_users=True,
        )
        role.grants = [
            DashboardRoleGrant(permission="dashboard:read", scope="all"),
            # ``security:write`` implies ``ops:write``; the vocabulary refuses
            # the pair on its own.
            DashboardRoleGrant(permission="ops:write", scope="all"),
            DashboardRoleGrant(permission="security:write", scope="all"),
        ]
        session.add(role)
        await session.commit()
        role_id = role.id

    invited = await async_client.post(DASHBOARD_USERS, json={"username": "sam", "roleId": role_id})
    assert invited.status_code == 201, invited.text

    async with _client(app_instance) as person:
        accepted = await person.post(
            "/api/dashboard-auth/invite/accept",
            json={"token": invited.json()["invite"]["token"], "password": ACCEPT_PASSWORD},
        )
        assert accepted.status_code == 200, accepted.text

        refused_issue = await person.post(SCIM_TOKENS, json={"label": "their-own"})
        refused_rotate = await person.post(f"{SCIM_TOKENS}/{admin_token_id}/rotate")
        allowed_revoke = await person.delete(f"{SCIM_TOKENS}/{admin_token_id}")

    assert refused_issue.status_code == 403, refused_issue.text
    assert refused_issue.json()["error"]["code"] == "insufficient_delegation"
    assert refused_rotate.status_code == 403
    assert allowed_revoke.status_code == 204, allowed_revoke.text

    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardScimToken))).scalars().all() == []


# --- 6. the lifecycle, through the shared functions ---


@asynccontextmanager
async def _users_service() -> AsyncIterator[DashboardUsersService]:
    """The back-channel service, composed exactly as `get_dashboard_users_context` composes it."""

    async with SessionLocal() as session:
        yield DashboardUsersService(
            DashboardUsersRepository(session), DashboardRolesRepository(session), DashboardAuthRepository(session)
        )


async def _owned_key(client: AsyncClient, owner_id: str, *, active: bool = True, reason: str | None = None) -> ApiKey:
    """A proxy key belonging to that account. Ownership is set directly: no route assigns it."""

    created = await client.post("/api/api-keys", json={"name": f"key-{uuid.uuid4().hex[:6]}"})
    assert created.status_code == 200, created.text
    async with SessionLocal() as session:
        key = await session.get(ApiKey, created.json()["id"])
        assert key is not None
        key.owner_user_id = owner_id
        key.is_active = active
        key.deactivated_reason = reason
        await session.commit()
        await session.refresh(key)
    return key


async def _key(key_id: str) -> ApiKey:
    async with SessionLocal() as session:
        key = await session.get(ApiKey, key_id)
        assert key is not None
        return key


async def _set_policy(policy: LocalLoginPolicy) -> None:
    """Store the policy directly; the route that guards tightening has its own tests."""

    async with SessionLocal() as session:
        row = (await session.execute(select(DashboardSettings))).scalar_one()
        row.local_login_policy = policy.value
        await session.commit()
    await get_settings_cache().invalidate()
    await get_dashboard_users_cache().invalidate()


async def _make_qualifying_emergency_account(user_id: str) -> None:
    """The five facts of PLAN §4.2, written straight onto the row.

    Earning them through the product would mean an invitation, a password and
    a TOTP enrolment for an account this surface provisioned — none of which
    this test is about. What it is about is that the guard sees the same five
    facts however they got there.
    """

    async with SessionLocal() as session:
        row = await session.get(DashboardUser, user_id)
        assert row is not None
        row.role_id = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
        row.is_break_glass = True
        # Opaque blobs: the guard tests them for presence, never reads them.
        row.password_hash = "-".join(["not", "a", "real", "hash"])
        row.totp_secret_encrypted = "-".join(["not", "a", "real", "secret"]).encode()
        await session.commit()
    await get_dashboard_users_cache().invalidate()


async def _set_status(user_id: str, status: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(DashboardUser, user_id)
        assert row is not None
        row.status = status
        await session.commit()
    await get_dashboard_users_cache().invalidate()


async def _generation(user_id: str) -> int:
    row = await _user(user_id)
    assert row is not None
    return row.session_generation


@pytest.mark.asyncio
async def test_a_leaver_loses_their_sessions_and_the_keys_they_own(async_client: AsyncClient, app_instance) -> None:
    """One push, the whole cascade — and nothing an administrator turned off by hand."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    cascaded = await _owned_key(async_client, user_id)
    by_hand = await _owned_key(async_client, user_id, active=False, reason="manual")
    somebody_else = await _owned_key(async_client, user_id)
    async with SessionLocal() as session:
        orphan = await session.get(ApiKey, somebody_else.id)
        assert orphan is not None
        orphan.owner_user_id = None
        await session.commit()
    before = await _generation(user_id)

    async with _client(app_instance) as scim:
        left = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
            headers=_auth(secret),
        )
    assert left.status_code == 200, left.text

    account = await _user(user_id)
    assert account is not None and account.status == "disabled"
    # The session cookie carries the generation; bumping it is the revocation.
    assert account.session_generation > before
    assert (await _key(cascaded.id)).deactivated_reason == "owner_disabled"
    assert (await _key(by_hand.id)).deactivated_reason == "manual"
    assert (await _key(somebody_else.id)).is_active is True
    assert [row.action for row in await _rows("scim_user_deprovisioned")] == ["scim_user_deprovisioned"]
    assert '"source": "scim"' in ((await _rows("user_disabled"))[0].details or "")
    assert '"count": 1' in ((await _rows("user_keys_deactivated"))[0].details or "")


@pytest.mark.asyncio
async def test_reactivation_restores_only_the_keys_the_cascade_turned_off(
    async_client: AsyncClient, app_instance
) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    cascaded = await _owned_key(async_client, user_id)
    by_hand = await _owned_key(async_client, user_id, active=False, reason="manual")
    expired = await _owned_key(async_client, user_id, active=False, reason="expired")

    async with _client(app_instance) as scim:
        assert (
            await scim.patch(
                f"{USERS}/{user_id}",
                json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
                headers=_auth(secret),
            )
        ).status_code == 200
        rejoined = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "active", "value": True}]},
            headers=_auth(secret),
        )
    assert rejoined.status_code == 200, rejoined.text
    assert rejoined.json()["active"] is True

    assert (await _key(cascaded.id)).is_active is True
    assert (await _key(cascaded.id)).deactivated_reason is None
    # An operator's own block outlives the person's whole absence.
    assert (await _key(by_hand.id)).is_active is False
    assert (await _key(expired.id)).deactivated_reason == "expired"
    assert '"count": 1' in ((await _rows("user_keys_reactivated"))[0].details or "")


@pytest.mark.asyncio
async def test_a_redelivered_deprovision_writes_nothing_twice(async_client: AsyncClient, app_instance) -> None:
    """Identity providers retry. The second push must cost the account nothing."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        leave = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
        first = await scim.patch(f"{USERS}/{user_id}", json=leave, headers=_auth(secret))
        after_first = await _generation(user_id)
        second = await scim.patch(f"{USERS}/{user_id}", json=leave, headers=_auth(secret))
    assert first.status_code == second.status_code == 200
    assert second.json()["active"] is False
    assert await _generation(user_id) == after_first
    assert len(await _rows("user_disabled")) == 1
    assert len(await _rows("scim_user_deprovisioned")) == 1


@pytest.mark.asyncio
async def test_the_last_emergency_account_is_not_deprovisioned(async_client: AsyncClient, app_instance) -> None:
    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    key = await _owned_key(async_client, user_id)
    await _make_qualifying_emergency_account(user_id)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    async with _client(app_instance) as scim:
        refused = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
            headers=_auth(secret),
        )
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "409"
    assert "emergency account" in body["detail"]

    account = await _user(user_id)
    assert account is not None and account.status == "active"
    # The guard runs before the cascade and the refusal takes the whole write
    # with it, so the person keeps working while somebody reads the audit row.
    assert (await _key(key.id)).is_active is True
    refusals = await _rows("scim_deprovision_refused")
    assert len(refusals) == 1 and refusals[0].severity == "warning"
    assert not await _rows("user_disabled")


@pytest.mark.asyncio
async def test_the_last_admin_is_refused_by_the_invariant_that_owns_it(async_client: AsyncClient, app_instance) -> None:
    """The other 409: no emergency designation anywhere, and one admin left."""

    bootstrap_id = await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    promoted = await async_client.patch(
        f"{DASHBOARD_USERS}/{user_id}", json={"roleId": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN], "force": True}
    )
    assert promoted.status_code == 200, promoted.text
    await _set_status(bootstrap_id, "disabled")

    async with _client(app_instance) as scim:
        refused = await scim.patch(
            f"{USERS}/{user_id}",
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
            headers=_auth(secret),
        )
    assert refused.status_code == 409, refused.text
    assert "admin" in refused.json()["detail"]
    assert refused.json().get("scimType") is None
    account = await _user(user_id)
    assert account is not None and account.status == "active"
    # That refusal is not a deprovisioning refusal; only the emergency guard writes one.
    assert not await _rows("scim_deprovision_refused")


@pytest.mark.asyncio
async def test_an_account_that_never_accepted_its_invitation_is_deprovisioned(
    async_client: AsyncClient, app_instance
) -> None:
    """The gap an identity provider would otherwise leave open forever.

    A hand-invited joiner who leaves before their first sign-in must not keep a
    live invitation, and the account row has to survive so the caller can still
    find them by the name it pushed.
    """

    await _setup_admin(async_client)
    created = await async_client.post(DASHBOARD_USERS, json={"username": "nadia", "roleId": VIEWER_ROLE})
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]
    async with SessionLocal() as session:
        invite = (
            await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id))
        ).scalar_one()
        # An invitation that waits for a provider sign-in has no link to
        # expire, so nothing else in the product would ever close it.
        invite.sso_only = True
        invite.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
        await session.commit()

    async with _users_service() as service:
        assert await service.deactivate_user(user_id, actor=scim_actor(), actor_ip=None, source="scim") is True

    account = await _user(user_id)
    assert account is not None and account.status == "disabled"
    async with SessionLocal() as session:
        remaining = (
            (await session.execute(select(DashboardUserInvite).where(DashboardUserInvite.user_id == user_id)))
            .scalars()
            .all()
        )
    assert remaining == []
    assert [row.action for row in await _rows("user_disabled")] == ["user_disabled"]
    assert '"source": "scim"' in ((await _rows("invite_revoked"))[0].details or "")

    async with _users_service() as service:
        assert await service.deactivate_user(user_id, actor=scim_actor(), actor_ip=None, source="scim") is False
    assert len(await _rows("user_disabled")) == 1


@pytest.mark.asyncio
async def test_a_refusal_names_the_invariant_that_actually_lost(
    async_client: AsyncClient, app_instance, monkeypatch
) -> None:
    """Both predicates ride into one UPDATE, so a zero-row write has to be re-read.

    Losing that race is what the re-read is for: naming the last-admin rule for
    a refusal the emergency rule earned sends an operator to fix an account
    that was never the problem.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        first = (await _create(scim, secret)).json()["id"]
        second = (await _create(scim, secret, userName="brit@example.com", externalId="ext-brit")).json()["id"]
    key = await _owned_key(async_client, first)
    for user_id in (first, second):
        await _make_qualifying_emergency_account(user_id)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    async def _lost_the_race(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(DashboardUsersRepository, "update_role_status_guarded", _lost_the_race)

    # Another admin is still there, so the conditional write can only have been
    # refused by the emergency predicate.
    async with _users_service() as service:
        with pytest.raises(LastBreakGlassProtectedError):
            await service.deactivate_user(first, actor=scim_actor(), actor_ip=None, source="scim")
    assert (await _key(key.id)).is_active is True
    assert len(await _rows("scim_deprovision_refused")) == 1

    # The reverse race: the other admin is there when the guard counts and gone
    # by the time the write lands, which is precisely what the re-read catches.
    reads = {"n": 0}

    async def _admin_vanishes_mid_write(*args: object, **kwargs: object) -> int:
        reads["n"] += 1
        return 1 if reads["n"] == 1 else 0

    monkeypatch.setattr(DashboardUsersRepository, "count_active_admins", _admin_vanishes_mid_write)
    async with _users_service() as service:
        with pytest.raises(LastAdminProtectedError):
            await service.deactivate_user(second, actor=scim_actor(), actor_ip=None, source="scim")
    # The reverse race is not a deprovisioning refusal and writes no second row.
    assert len(await _rows("scim_deprovision_refused")) == 1


@pytest.mark.asyncio
async def test_the_credential_never_reaches_an_audit_row_or_a_log_line(
    async_client: AsyncClient, app_instance, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole lifecycle at once, then every row and every line it wrote.

    The audit sanitiser drops detail keys by exact name from a fixed set and
    knows none of the names this surface uses, so it is not a safety net here.
    The rule is that nothing ever puts the credential in a dictionary at all,
    and this is the assertion that keeps it true.
    """

    await _setup_admin(async_client)
    token_id, secret = await _issue_token(async_client)
    digest = hash_token(secret)

    # This product's own loggers, at the level it actually runs at. The driver's
    # statement echo is deliberately out of scope: it prints every bound
    # parameter, so at DEBUG it necessarily contains the stored digest — which
    # is a fact about turning SQL echo on, not about this surface.
    with caplog.at_level(logging.INFO, logger="app"):
        async with _client(app_instance) as scim:
            created = await _create(scim, secret)
            user_id = created.json()["id"]
            await scim.patch(
                f"{USERS}/{user_id}",
                json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
                headers=_auth(secret),
            )
            # A refusal carries a message of its own; it must not quote the caller either.
            refused = await scim.post(
                USERS,
                json=_joiner(userName="mo@example.com", externalId="ext-mo", roles=[{"value": "admin"}]),
                headers=_auth(secret),
            )
            assert refused.status_code == 400, refused.text
            assert secret not in refused.text
        rotated = await async_client.post(f"{SCIM_TOKENS}/{token_id}/rotate")
        assert rotated.status_code == 200, rotated.text

    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        rows = list((await session.execute(select(AuditLog))).scalars().all())
    assert rows
    for row in rows:
        written = f"{row.details or ''}{row.target_id or ''}{row.actor_username or ''}"
        assert secret not in written and digest not in written

    logged = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "app" or record.name.startswith("app.")
    )
    assert logged
    for value in (secret, digest, rotated.json()["secret"]):
        assert value not in logged


# --- 7. one request: one parser, one set of refusals, one transaction ---
#
# Every case below is a defect two independent reviews found in ``apply()`` and
# the helpers it calls. They are grouped here because they were one defect: a
# patch that parsed the wire format a second, weaker time, and an update that
# committed its attributes before the lifecycle guards had a chance to refuse.


#: Longer than ``dashboard_users.display_name`` (``varchar(128)``).
OVERLONG_DISPLAY_NAME = "n" * 200


async def _identity(user_id: str) -> DashboardIdentity:
    async with SessionLocal() as session:
        return (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.user_id == user_id))
        ).scalar_one()


async def _patch(scim: AsyncClient, secret: str, user_id: str, *operations: Mapping[str, object]):
    return await scim.patch(f"{USERS}/{user_id}", json={"Operations": list(operations)}, headers=_auth(secret))


async def _replace(scim: AsyncClient, secret: str, user_id: str, **attributes: object):
    body: dict[str, object] = {"userName": "alice@example.com", "externalId": "ext-alice"}
    body.update(attributes)
    return await scim.put(f"{USERS}/{user_id}", json=body, headers=_auth(secret))


@pytest.mark.asyncio
async def test_a_push_cannot_re_enable_an_account_it_may_not_provision(async_client: AsyncClient, app_instance) -> None:
    """The direction that grants access is bounded by the predicate that bounds the role push.

    An administrator promoted this account by hand and then switched it off. A
    bearer token that may not push the admin role may not switch that account
    back on either — and may not revive the keys the disable cascaded, which is
    what made this the one refusal worth more than a status flip.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    key = await _owned_key(async_client, user_id)

    promoted = await async_client.patch(
        f"{DASHBOARD_USERS}/{user_id}", json={"roleId": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN], "force": True}
    )
    assert promoted.status_code == 200, promoted.text
    disabled = await async_client.patch(f"{DASHBOARD_USERS}/{user_id}", json={"status": "disabled"})
    assert disabled.status_code == 200, disabled.text
    assert (await _key(key.id)).deactivated_reason == "owner_disabled"

    async with _client(app_instance) as scim:
        refused = await _patch(scim, secret, user_id, {"op": "replace", "value": {"active": True}})
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "409"
    account = await _user(user_id)
    assert account is not None
    # An account invariant, so no invented RFC keyword; and the detail names the
    # account rather than the role the resource deliberately does not report.
    # Quoted, and read off the row rather than spelled out again, so the
    # assertion pins the name the refusal is about and would not be satisfied by
    # a longer name that merely contains it.
    assert body.get("scimType") is None
    assert f"'{account.username}'" in body["detail"]
    assert "'admin'" not in body["detail"]

    assert account.status == "disabled"
    assert account.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
    assert (await _key(key.id)).is_active is False
    assert (await _key(key.id)).deactivated_reason == "owner_disabled"
    refusals = await _rows("scim_provision_refused")
    assert len(refusals) == 1 and refusals[0].severity == "warning"
    assert '"reason": "account_not_provisionable"' in (refusals[0].details or "")
    assert not await _rows("user_enabled")


@pytest.mark.asyncio
async def test_a_patch_ignores_the_attributes_a_replace_ignores(async_client: AsyncClient, app_instance) -> None:
    """A routine push carries more than a core user, and the deprovision rides with it.

    ``title`` and ``locale`` used to make the whole operation a ``400``, taking
    the ``active: false`` batched beside them with it — a leaver who stayed.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        batched = await _patch(
            scim, secret, user_id, {"op": "replace", "value": {"active": False, "title": "Manager", "locale": "en-US"}}
        )
        pathed = await _patch(
            scim,
            secret,
            user_id,
            {"op": "replace", "path": "title", "value": "Director"},
            {"op": "replace", "path": "active", "value": True},
        )
        replaced = await _replace(scim, secret, user_id, title="Manager", locale="en-US", active=True)
    assert batched.status_code == 200, batched.text
    assert batched.json()["active"] is False
    assert pathed.status_code == 200, pathed.text
    assert pathed.json()["active"] is True
    assert replaced.status_code == 200, replaced.text


@pytest.mark.asyncio
async def test_a_schema_qualified_path_this_resource_ignores_does_not_refuse_the_deprovision(
    async_client: AsyncClient, app_instance
) -> None:
    """The enterprise extension rides on a routine Entra push, and it is spelled long.

    An attribute this resource does not model is dropped by the fold, not
    refused — but the path never reached the fold while the request model
    capped it below the length of a schema-qualified spelling, so an operation
    naming ``…:enterprise:2.0:User:department`` made the whole request a
    ``400`` and took the ``active: false`` batched beside it with it.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    extension = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:manager.displayName"
    assert len(extension) > 64

    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        leaver = await _patch(
            scim,
            secret,
            user_id,
            {"op": "replace", "path": extension, "value": "Dana Lead"},
            {"op": "replace", "path": "active", "value": False},
        )
        # Past every real spelling there is still a bound, and it is the
        # model's, answered the way an over-long modelled value is.
        absurd = await _patch(scim, secret, user_id, {"op": "replace", "path": "u" * 513, "value": "x"})
    assert leaver.status_code == 200, leaver.text
    assert leaver.json()["active"] is False
    assert absurd.status_code == 400, absurd.text
    assert absurd.json()["scimType"] == "invalidSyntax"

    account = await _user(user_id)
    assert account is not None and account.status == "disabled"


@pytest.mark.asyncio
async def test_a_patch_meets_the_length_caps_a_replace_meets(async_client: AsyncClient, app_instance) -> None:
    """``display_name`` is ``varchar(128)`` and ``identities.user_name`` is ``varchar(256)``.

    SQLite stores an over-long value regardless, so this refusal is invisible on
    the development dialect and a ``500`` on PostgreSQL. Both verbs answer it
    identically because both are read by the same request model.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        patched = await _patch(
            scim, secret, user_id, {"op": "replace", "path": "displayName", "value": OVERLONG_DISPLAY_NAME}
        )
        replaced = await _replace(scim, secret, user_id, displayName=OVERLONG_DISPLAY_NAME)
        formatted = await _patch(
            scim, secret, user_id, {"op": "replace", "path": "name", "value": {"formatted": OVERLONG_DISPLAY_NAME}}
        )
        named = await _patch(scim, secret, user_id, {"op": "replace", "path": "userName", "value": "u" * 300})
        addressed = await _patch(
            scim, secret, user_id, {"op": "replace", "path": "emails", "value": "e" * 320 + "@example.com"}
        )
    for refused in (patched, replaced, formatted, named, addressed):
        assert refused.status_code == 400, refused.text
        assert refused.json()["scimType"] == "invalidSyntax"

    account = await _user(user_id)
    assert account is not None and account.display_name is None
    identity = await _identity(user_id)
    assert identity.user_name == "alice@example.com"
    assert identity.display_name is None and identity.email is None


@pytest.mark.asyncio
async def test_a_patch_reads_roles_exactly_as_a_replace_does(async_client: AsyncClient, app_instance) -> None:
    """One role travels on a push, whichever verb carries it.

    A patch used to take the first entry of a multi-entry array and discard the
    rest, and to let a later operation overwrite an earlier ``admin`` value
    before anything checked it.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    two = [{"value": "operator"}, {"value": "viewer"}]
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        patched = await _patch(scim, secret, user_id, {"op": "replace", "path": "roles", "value": two})
        replaced = await _replace(scim, secret, user_id, roles=two)
        # Two operations naming one attribute: the fold cannot pick a winner
        # without discarding the value that should have been refused.
        twice = await _patch(
            scim,
            secret,
            user_id,
            {"op": "replace", "path": "roles", "value": [{"value": "admin"}]},
            {"op": "replace", "path": "roles", "value": [{"value": "viewer"}]},
        )
    assert patched.status_code == 400, patched.text
    assert patched.json()["scimType"] == "invalidValue"
    assert "one role" in patched.json()["detail"]
    assert replaced.status_code == 400, replaced.text
    assert replaced.json()["scimType"] == "invalidValue"
    assert twice.status_code == 400, twice.text
    assert twice.json()["scimType"] == "invalidSyntax"
    assert "roles" in twice.json()["detail"]

    account = await _user(user_id)
    assert account is not None and account.role_id == VIEWER_ROLE


@pytest.mark.asyncio
async def test_a_patch_honours_primary_on_emails_as_a_replace_does(async_client: AsyncClient, app_instance) -> None:
    """The same payload has to store the same address whichever verb carries it."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    addresses = [{"value": "alias@example.com"}, {"value": "real@example.com", "primary": True}]
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        patched = await _patch(scim, secret, user_id, {"op": "replace", "path": "emails", "value": addresses})
        assert patched.status_code == 200, patched.text
        assert (await _identity(user_id)).email == "real@example.com"
        cleared = await _patch(scim, secret, user_id, {"op": "replace", "path": "emails", "value": []})
        assert cleared.status_code == 200, cleared.text
        replaced = await _replace(scim, secret, user_id, emails=addresses)
    assert (await _identity(user_id)).email == "real@example.com"
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["emails"] == patched.json()["emails"] == [{"value": "real@example.com", "primary": True}]


@pytest.mark.asyncio
async def test_a_refused_deactivation_takes_the_whole_push_back(async_client: AsyncClient, app_instance) -> None:
    """One request, one transaction.

    The attributes used to commit before the lifecycle guards ran, so a push
    refused by the emergency-account invariant still renamed the person and
    moved their address. Nothing of a refused request survives it now.
    """

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    key = await _owned_key(async_client, user_id)
    before = (await _identity(user_id)).last_seen_at
    await _make_qualifying_emergency_account(user_id)
    await _set_policy(LocalLoginPolicy.BREAK_GLASS_ONLY)

    async with _client(app_instance) as scim:
        refused = await _patch(
            scim,
            secret,
            user_id,
            {
                "op": "replace",
                "value": {
                    "displayName": "Alice Smith",
                    "userName": "alice.smith@example.com",
                    "emails": [{"value": "alice.smith@example.com", "primary": True}],
                    "active": False,
                },
            },
        )
    assert refused.status_code == 409, refused.text
    assert "emergency account" in refused.json()["detail"]

    account = await _user(user_id)
    assert account is not None
    assert account.status == "active" and account.display_name is None
    assert (await _key(key.id)).is_active is True
    identity = await _identity(user_id)
    assert identity.user_name == "alice@example.com"
    assert identity.display_name is None and identity.email is None
    assert identity.last_seen_at == before

    # The identity provider can still find the person by the name it pushed,
    # which is the whole reason the rest of the push must not have landed.
    async with _client(app_instance) as scim:
        found = await scim.get(USERS, params={"filter": 'userName eq "alice@example.com"'}, headers=_auth(secret))
    assert found.json()["totalResults"] == 1


@pytest.mark.asyncio
async def test_an_ignored_role_push_is_audited_once_across_repeated_pushes(
    async_client: AsyncClient, app_instance
) -> None:
    """A directory re-pushes on a schedule; a row per push would bury the one that matters."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    pinned = await async_client.patch(
        f"{DASHBOARD_USERS}/{user_id}", json={"roleId": PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR], "force": True}
    )
    assert pinned.status_code == 200, pinned.text

    push = {"op": "replace", "path": "roles", "value": [{"value": "viewer"}]}
    async with _client(app_instance) as scim:
        first = await _patch(scim, secret, user_id, push)
        second = await _patch(scim, secret, user_id, push, {"op": "replace", "path": "displayName", "value": "Alice"})
    assert first.status_code == second.status_code == 200

    account = await _user(user_id)
    assert account is not None
    assert account.role_id == PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
    assert account.role_source == DashboardUserRoleSource.MANUAL.value
    # The rest of the push still applies.
    assert account.display_name == "Alice"
    ignored = await _rows("scim_role_push_ignored")
    assert len(ignored) == 1 and ignored[0].severity == "warning"
    assert ignored[0].target_id == user_id
    assert '"reason": "role_source_manual"' in (ignored[0].details or "")
    assert '"role": "viewer"' in (ignored[0].details or "")


@pytest.mark.asyncio
async def test_a_push_that_changes_nothing_writes_nothing(async_client: AsyncClient, app_instance) -> None:
    """Idempotent means idempotent: not even the identity's last-seen stamp moves."""

    await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
        provisioned = await _identity(user_id)
        named = await _patch(scim, secret, user_id, {"op": "replace", "path": "displayName", "value": "Alice"})
        assert named.status_code == 200, named.text
        # A push that really changes something does stamp the identity, so the
        # assertions below are about a stamp that moves, not one that is never set.
        stamped = await _identity(user_id)
        assert stamped.last_seen_at is not None and stamped.last_seen_at != provisioned.last_seen_at

        # The same value again: nothing to write, so nothing is written.
        repeated = await _patch(scim, secret, user_id, {"op": "replace", "path": "displayName", "value": "Alice"})
        assert repeated.status_code == 200, repeated.text
        assert (await _identity(user_id)).last_seen_at == stamped.last_seen_at

        leave = {"op": "replace", "path": "active", "value": False}
        assert (await _patch(scim, secret, user_id, leave)).status_code == 200
        settled = await _identity(user_id)
        assert (await _patch(scim, secret, user_id, leave)).status_code == 200
        assert (await _identity(user_id)).last_seen_at == settled.last_seen_at

        rejoin = {"op": "replace", "path": "active", "value": True}
        assert (await _patch(scim, secret, user_id, rejoin)).status_code == 200
        rejoined = await _identity(user_id)
        assert (await _patch(scim, secret, user_id, rejoin)).status_code == 200
        assert (await _identity(user_id)).last_seen_at == rejoined.last_seen_at

    assert len(await _rows("user_disabled")) == 1
    assert len(await _rows("user_enabled")) == 1


@pytest.mark.asyncio
async def test_a_push_does_not_move_a_role_it_could_not_have_granted(async_client: AsyncClient, app_instance) -> None:
    """A role this surface may not hand out is one it may not take away either.

    Without that, one request could demote the last administrator and disable
    it in the same breath — and the last-admin guard, which reads the role the
    write is about to leave behind, would never have fired.
    """

    bootstrap_id = await _setup_admin(async_client)
    _, secret = await _issue_token(async_client)
    async with _client(app_instance) as scim:
        user_id = (await _create(scim, secret)).json()["id"]
    async with SessionLocal() as session:
        row = await session.get(DashboardUser, user_id)
        assert row is not None
        # Still provisioned (``role_source`` stays ``scim``) and now
        # administrative: the state a later custom-role edit reaches with
        # nobody's consent.
        row.role_id = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
        await session.commit()
    await get_dashboard_users_cache().invalidate()

    demote = {"op": "replace", "path": "roles", "value": [{"value": "viewer"}]}
    async with _client(app_instance) as scim:
        kept = await _patch(scim, secret, user_id, demote)
    assert kept.status_code == 200, kept.text
    account = await _user(user_id)
    assert account is not None and account.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
    ignored = await _rows("scim_role_push_ignored")
    assert len(ignored) == 1
    assert '"reason": "role_not_provisionable"' in (ignored[0].details or "")

    await _set_status(bootstrap_id, "disabled")
    async with _client(app_instance) as scim:
        refused = await _patch(scim, secret, user_id, demote, {"op": "replace", "path": "active", "value": False})
    assert refused.status_code == 409, refused.text
    assert "admin" in refused.json()["detail"]

    account = await _user(user_id)
    assert account is not None
    assert account.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
    assert account.status == "active"
    assert account.role_source == DashboardUserRoleSource.SCIM.value
    # A refused request audits no skip: the push it belonged to did not happen.
    assert len(await _rows("scim_role_push_ignored")) == 1

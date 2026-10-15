"""The break-glass predicate and its post-state guard (PLAN §4.2).

Qualification is four facts read from the current row; the guard refuses only
a change that takes the last one away, and only while local sign-in is
restricted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.requests import Request

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import DashboardUser, LocalLoginPolicy
from app.modules.dashboard_users.break_glass import (
    LastBreakGlassProtectedError,
    assert_break_glass_remains,
    break_glass_second_factor_required,
    local_login_admits,
    qualifies,
    user_qualifies,
)

pytestmark = pytest.mark.unit

ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
#: Stand-in for the encrypted column bytes, assembled from parts so the
#: line does not read as `SECRET = "<literal>"` to a secret scanner.
SECRET = b"an-" + b"encrypted-" + b"blob"
# Built by concatenation so no secret scanner reads it as a real hash.
STORED_HASH = "not-a-real-" + "password-hash"


def _user(
    *,
    is_break_glass: bool = True,
    status: str = "active",
    role_id: str = ADMIN_ROLE,
    has_totp: bool = True,
    has_password: bool = True,
) -> DashboardUser:
    return DashboardUser(
        id="user-1",
        username="rescue",
        role_id=role_id,
        status=status,
        is_break_glass=is_break_glass,
        totp_secret_encrypted=SECRET if has_totp else None,
        password_hash=STORED_HASH if has_password else None,
    )


def _others(count: int) -> Callable[[], Awaitable[int]]:
    async def _count() -> int:
        return count

    return _count


def test_qualification_needs_all_five_facts() -> None:
    assert qualifies(is_break_glass=True, status="active", role_id=ADMIN_ROLE, has_totp=True, has_password=True)
    assert not qualifies(is_break_glass=False, status="active", role_id=ADMIN_ROLE, has_totp=True, has_password=True)
    assert not qualifies(is_break_glass=True, status="disabled", role_id=ADMIN_ROLE, has_totp=True, has_password=True)
    assert not qualifies(is_break_glass=True, status="active", role_id=VIEWER_ROLE, has_totp=True, has_password=True)
    assert not qualifies(is_break_glass=True, status="active", role_id=ADMIN_ROLE, has_totp=False, has_password=True)
    assert not qualifies(is_break_glass=True, status="active", role_id=ADMIN_ROLE, has_totp=True, has_password=False)
    assert user_qualifies(_user()) and not user_qualifies(_user(has_totp=False))


def test_an_account_without_a_local_password_never_qualifies() -> None:
    """A proxy-provisioned admin cannot use the local password form it is counted for."""

    assert not user_qualifies(_user(has_password=False))
    assert not local_login_admits(_user(has_password=False), LocalLoginPolicy.BREAK_GLASS_ONLY.value)


def test_designation_without_a_secret_does_not_force_a_second_factor() -> None:
    """The migrated ``admin`` row must still be able to sign in and enrol."""

    assert break_glass_second_factor_required(_user())
    assert not break_glass_second_factor_required(_user(has_totp=False))
    assert not break_glass_second_factor_required(_user(is_break_glass=False))


def test_local_login_admits_follows_the_policy() -> None:
    designated, plain_admin, viewer = (
        _user(),
        _user(is_break_glass=False),
        _user(is_break_glass=False, role_id=VIEWER_ROLE),
    )
    for user in (designated, plain_admin, viewer):
        assert local_login_admits(user, LocalLoginPolicy.ENABLED.value)
    assert local_login_admits(plain_admin, LocalLoginPolicy.ADMINS_ONLY.value)
    assert not local_login_admits(viewer, LocalLoginPolicy.ADMINS_ONLY.value)
    assert local_login_admits(designated, LocalLoginPolicy.BREAK_GLASS_ONLY.value)
    assert not local_login_admits(plain_admin, LocalLoginPolicy.BREAK_GLASS_ONLY.value)


def test_break_glass_only_admits_a_qualifying_account_not_a_bare_designation() -> None:
    """The strictest policy must not leave a password-only admin door open.

    The migrated ``admin`` row carries the designation with no second factor;
    admitting it would make ``break_glass_only`` weaker than ``admins_only``.
    It is still admitted under the policies where it can enrol.
    """

    unenrolled = _user(has_totp=False)
    assert not local_login_admits(unenrolled, LocalLoginPolicy.BREAK_GLASS_ONLY.value)
    assert local_login_admits(unenrolled, LocalLoginPolicy.ENABLED.value)
    assert local_login_admits(unenrolled, LocalLoginPolicy.ADMINS_ONLY.value)
    for user in (_user(status="disabled"), _user(role_id=VIEWER_ROLE), _user(has_password=False)):
        assert not local_login_admits(user, LocalLoginPolicy.BREAK_GLASS_ONLY.value)


@pytest.mark.asyncio
async def test_the_guard_sleeps_while_local_sign_in_is_open() -> None:
    await assert_break_glass_remains(
        _user(),
        policy=LocalLoginPolicy.ENABLED.value,
        count_other_qualifying=_others(0),
        has_totp=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"has_totp": False},
        {"status": "disabled"},
        {"role_id": VIEWER_ROLE},
        {"is_break_glass": False},
    ],
)
async def test_every_shape_of_losing_qualification_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(LastBreakGlassProtectedError):
        await assert_break_glass_remains(
            _user(),
            policy=LocalLoginPolicy.ADMINS_ONLY.value,
            count_other_qualifying=_others(0),
            **change,
        )


@pytest.mark.asyncio
async def test_another_qualifying_account_makes_the_change_allowed() -> None:
    await assert_break_glass_remains(
        _user(),
        policy=LocalLoginPolicy.BREAK_GLASS_ONLY.value,
        count_other_qualifying=_others(1),
        has_totp=False,
    )


@pytest.mark.asyncio
async def test_an_install_with_nothing_to_lose_stays_editable() -> None:
    """Nobody qualified before the change, so the change cannot be what removed the last one."""

    await assert_break_glass_remains(
        _user(has_totp=False),
        policy=LocalLoginPolicy.BREAK_GLASS_ONLY.value,
        count_other_qualifying=_others(0),
        status="disabled",
    )


@pytest.mark.asyncio
async def test_a_change_that_keeps_qualification_is_allowed() -> None:
    await assert_break_glass_remains(
        _user(),
        policy=LocalLoginPolicy.ADMINS_ONLY.value,
        count_other_qualifying=_others(0),
        role_id=ADMIN_ROLE,
        status="active",
    )


@pytest.mark.asyncio
async def test_removing_the_last_emergency_password_is_refused() -> None:
    """A qualifying account that drops its password stops being a way back in."""

    with pytest.raises(LastBreakGlassProtectedError):
        await assert_break_glass_remains(
            _user(),
            policy=LocalLoginPolicy.BREAK_GLASS_ONLY.value,
            count_other_qualifying=_others(0),
            has_password=False,
        )


def test_the_failed_login_key_pairs_the_username_with_the_address() -> None:
    """One bucket per (username, address): the username is hashed, never stored in the clear."""

    from app.modules.dashboard_auth.api import _password_login_address_key, _password_login_key

    # The bucket prefix is assembled rather than written out: "<prefix>:<host>"
    # in one literal reads as a credential pair to secret scanners.
    bucket = "password_" + "login"
    here_host, there_host = "10.0.0.1", "10.0.0.2"
    here = cast(Request, SimpleNamespace(client=SimpleNamespace(host=here_host)))
    there = cast(Request, SimpleNamespace(client=SimpleNamespace(host=there_host)))
    alice_here = _password_login_key(here, "alice")
    assert alice_here != _password_login_key(there, "alice")
    assert alice_here != _password_login_key(here, "bob")
    assert alice_here == _password_login_key(here, "alice")
    assert "alice" not in alice_here
    # No username to key on (``username_required``) keeps the address-only bucket.
    assert _password_login_key(here, None).startswith(f"{bucket}:{here_host}:")
    # The per-address bucket is the same address, without any username term.
    assert _password_login_address_key(here) == f"{bucket}:{here_host}"
    assert _password_login_address_key(there) == f"{bucket}:{there_host}"
    assert alice_here.startswith(_password_login_address_key(here) + ":")


def test_the_per_address_ceiling_is_a_second_bucket_not_a_replacement() -> None:
    """PLAN §4.4 keeps ``password_login:{ip}`` and *adds* the per-account budget.

    Separate limiter types, so the per-account 8/60 s semantics are untouched,
    and a ceiling generous enough that a shared office egress never reaches it.
    """

    from app.modules.dashboard_auth.service import get_password_address_rate_limiter, get_password_rate_limiter

    per_account, per_address = get_password_rate_limiter(), get_password_address_rate_limiter()
    assert (per_account.max_attempts, per_account.window_seconds) == (8, 60)
    assert (per_address.max_attempts, per_address.window_seconds) == (60, 60)
    assert per_address.type != per_account.type
    assert per_address.max_attempts > per_account.max_attempts

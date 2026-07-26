"""Store-level behaviour for workspace-less plan-downgrade evidence (#1456)."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.usage.plan_downgrade_observations import (
    InMemoryPlanDowngradeObservationStore,
    PlanDowngradeObservationStore,
    _is_missing_observations_schema,
    credential_fingerprint,
)

pytestmark = pytest.mark.unit


def _account(*, refresh_token: str = "refresh-1") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id="acc_fingerprint",
        chatgpt_account_id="upstream_user",
        email="a@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt(refresh_token),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def test_credential_fingerprint_is_stable_for_the_same_credential() -> None:
    account = _account()
    assert credential_fingerprint(account) == credential_fingerprint(account)


def test_credential_fingerprint_changes_when_the_credential_is_replaced() -> None:
    original = credential_fingerprint(_account(refresh_token="refresh-1"))
    replaced = credential_fingerprint(_account(refresh_token="refresh-2"))
    assert original != replaced


def test_credential_fingerprint_does_not_expose_token_material() -> None:
    """The digest is what gets persisted, so it must not leak the credential."""
    account = _account(refresh_token="super-secret-refresh-token")
    fingerprint = credential_fingerprint(account)

    assert "super-secret-refresh-token" not in fingerprint
    assert fingerprint != account.refresh_token_encrypted.hex()
    assert len(fingerprint) == 64
    assert all(character in "0123456789abcdef" for character in fingerprint)


def test_credential_fingerprint_handles_empty_material() -> None:
    account = _account()
    account.refresh_token_encrypted = b""
    assert credential_fingerprint(account)


@pytest.mark.parametrize(
    "message",
    [
        "no such table: account_plan_downgrade_observations",
        'relation "account_plan_downgrade_observations" does not exist',
    ],
)
def test_missing_schema_is_recognized_for_both_backends(message: str) -> None:
    assert _is_missing_observations_schema(OperationalError("SELECT 1", {}, Exception(message)))


def test_unrelated_database_errors_are_not_treated_as_missing_schema() -> None:
    assert not _is_missing_observations_schema(OperationalError("SELECT 1", {}, Exception("database is locked")))
    assert not _is_missing_observations_schema(ProgrammingError("SELECT 1", {}, Exception("syntax error")))


@pytest.mark.asyncio
async def test_store_degrades_to_the_fallback_when_the_table_is_missing(monkeypatch) -> None:
    """A not-yet-migrated database must not break usage refresh.

    The observations table ships with this change, so a replica running the new
    code before the migration applies would otherwise raise on every refresh --
    turning a stale plan label into a broken refresh loop. Confirmation degrades
    to process-local state instead, which is the behavior that shipped before
    persistence was introduced.
    """
    fallback = InMemoryPlanDowngradeObservationStore()
    store = PlanDowngradeObservationStore(fallback=fallback)

    def _raise_missing_table(*_args, **_kwargs):
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("no such table: account_plan_downgrade_observations"),
        )

    monkeypatch.setattr(
        "app.modules.usage.plan_downgrade_observations.get_background_session",
        _raise_missing_table,
    )

    assert await store.get("acc_missing") is None

    await store.record(
        "acc_missing",
        observations=1,
        credential_fingerprint="abc",
        observed_plan_type="free",
    )
    recorded = await store.get("acc_missing")
    assert recorded is not None
    assert recorded.observations == 1
    assert await fallback.get("acc_missing") is not None

    await store.clear("acc_missing")
    assert await store.get("acc_missing") is None


@pytest.mark.asyncio
async def test_store_reraises_unrelated_database_errors(monkeypatch) -> None:
    """Only missing-schema failures are tolerated; real errors must surface."""
    store = PlanDowngradeObservationStore(fallback=InMemoryPlanDowngradeObservationStore())

    def _raise_locked(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(
        "app.modules.usage.plan_downgrade_observations.get_background_session",
        _raise_locked,
    )

    with pytest.raises(OperationalError):
        await store.get("acc_locked")
    with pytest.raises(OperationalError):
        await store.record(
            "acc_locked",
            observations=1,
            credential_fingerprint="abc",
            observed_plan_type="free",
        )
    with pytest.raises(OperationalError):
        await store.clear("acc_locked")

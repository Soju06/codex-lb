from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardConflictError
from app.db.models import AccountStatus
from app.modules.accounts import service as accounts_service_module
from app.modules.accounts.account_bundle import (
    AccountBundleError,
    AccountBundleTooLargeError,
    BundleAccount,
    BundleCredentials,
    UnsupportedAccountBundleError,
    bundle_integrity_token,
    decrypt_bundle,
    encrypt_bundle,
    mask_email,
    new_payload,
)
from app.modules.accounts.api import _bundle_error_outcome, _raise_bundle_error
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository, BundlePersistenceResult
from app.modules.accounts.service import (
    BUNDLE_VALIDATION_AGGREGATE_WARNING,
    BUNDLE_VALIDATION_WARNING,
    AccountsService,
)
from app.modules.usage.updater import UsageUpdater

MAX_BYTES = 256 * 1024


def _account(email: str = "operator@example.com") -> BundleAccount:
    return BundleAccount(
        chatgpt_account_id="workspace-1",
        chatgpt_user_id="user-1",
        email=email,
        workspace_id="workspace-1",
        workspace_label="Primary",
        seat_type="member",
        alias="work",
        plan_type="team",
        routing_policy="preserve",
        limit_warmup_enabled=True,
        security_work_authorized=True,
        credentials=BundleCredentials(
            access_token="access-secret",
            refresh_token="refresh-secret",
            id_token="id-secret",
        ),
    )


@pytest.mark.parametrize("accounts", [[], [_account()], [_account("a@example.com"), _account("b@example.com")]])
def test_bundle_round_trip_for_zero_one_and_many(accounts: list[BundleAccount]) -> None:
    encrypted = encrypt_bundle(new_payload(accounts), "correct horse battery staple", max_bytes=MAX_BYTES)

    restored = decrypt_bundle(encrypted, "correct horse battery staple", max_bytes=MAX_BYTES)

    assert restored.accounts == accounts
    assert b"access-secret" not in encrypted
    assert b"operator@example.com" not in encrypted


@pytest.mark.parametrize("field", ["access_token", "refresh_token", "id_token"])
def test_bundle_credentials_reject_whitespace_only_without_normalizing_tokens(field: str) -> None:
    values = {
        "access_token": " access-secret ",
        "refresh_token": " refresh-secret ",
        "id_token": " id-secret ",
    }
    credentials = BundleCredentials(
        access_token=values["access_token"],
        refresh_token=values["refresh_token"],
        id_token=values["id_token"],
    )
    assert getattr(credentials, field) == values[field]

    values[field] = "   "
    with pytest.raises(ValueError, match="credential cannot be blank"):
        BundleCredentials(
            access_token=values["access_token"],
            refresh_token=values["refresh_token"],
            id_token=values["id_token"],
        )


def test_bundle_account_rejects_whitespace_only_email() -> None:
    with pytest.raises(ValueError, match="email cannot be blank"):
        _account("   ")


def test_bundle_uses_fresh_random_encryption_material() -> None:
    payload = new_payload([_account()])
    first = encrypt_bundle(payload, "passphrase", max_bytes=MAX_BYTES)
    second = encrypt_bundle(payload, "passphrase", max_bytes=MAX_BYTES)

    assert first != second
    assert decrypt_bundle(first, "passphrase", max_bytes=MAX_BYTES) == payload
    assert decrypt_bundle(second, "passphrase", max_bytes=MAX_BYTES) == payload


@pytest.mark.parametrize("passphrase", ["wrong", ""])
def test_wrong_or_missing_passphrase_is_safe(passphrase: str) -> None:
    encrypted = encrypt_bundle(new_payload([_account()]), "right", max_bytes=MAX_BYTES)

    with pytest.raises(AccountBundleError, match="passphrase|required") as caught:
        decrypt_bundle(encrypted, passphrase, max_bytes=MAX_BYTES)

    assert "access-secret" not in str(caught.value)
    assert "operator@example.com" not in str(caught.value)


def test_ciphertext_and_authenticated_metadata_corruption_are_rejected() -> None:
    encrypted = encrypt_bundle(new_payload([_account()]), "right", max_bytes=MAX_BYTES)
    envelope = json.loads(encrypted)
    envelope["ciphertext"] = ("A" if envelope["ciphertext"][0] != "A" else "B") + envelope["ciphertext"][1:]
    corrupted_ciphertext = json.dumps(envelope).encode()

    with pytest.raises(AccountBundleError):
        decrypt_bundle(corrupted_ciphertext, "right", max_bytes=MAX_BYTES)

    envelope = json.loads(encrypted)
    envelope["cipher"]["name"] = "not-a-cipher"
    with pytest.raises(AccountBundleError):
        decrypt_bundle(json.dumps(envelope).encode(), "right", max_bytes=MAX_BYTES)


@pytest.mark.parametrize("raw", [b"", b"not-json", b"[]", b"{}"])
def test_malformed_envelopes_are_rejected(raw: bytes) -> None:
    with pytest.raises(AccountBundleError):
        decrypt_bundle(raw, "right", max_bytes=MAX_BYTES)


def test_unsupported_version_is_distinct() -> None:
    raw = json.dumps({"format": "codex-lb-account-bundle", "version": 2}).encode()

    with pytest.raises(UnsupportedAccountBundleError):
        decrypt_bundle(raw, "right", max_bytes=MAX_BYTES)


def test_encrypted_and_plaintext_limits_are_enforced() -> None:
    payload = new_payload([_account()])
    with pytest.raises(AccountBundleTooLargeError):
        encrypt_bundle(payload, "right", max_bytes=32)

    encrypted = encrypt_bundle(payload, "right", max_bytes=MAX_BYTES)
    with pytest.raises(AccountBundleTooLargeError):
        decrypt_bundle(encrypted, "right", max_bytes=len(encrypted) - 1)


def test_integrity_token_tracks_exact_opaque_upload_and_identity_is_masked() -> None:
    assert bundle_integrity_token(b"first") != bundle_integrity_token(b"second")
    assert mask_email("operator@example.com") == "o***@example.com"
    assert mask_email("opaque") == "o***"


def test_destination_reencrypts_bundle_credentials_with_a_distinct_at_rest_key() -> None:
    source_encryptor = TokenEncryptor(key=Fernet.generate_key())
    destination_encryptor = TokenEncryptor(key=Fernet.generate_key())
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._encryptor = destination_encryptor

    destination = service._bundle_accounts_for_destination(new_payload([_account()]))[0]

    assert destination_encryptor.decrypt(destination.access_token_encrypted) == _account().credentials.access_token
    with pytest.raises(InvalidToken):
        source_encryptor.decrypt(destination.access_token_encrypted)


def test_payload_rejects_workspace_id_label_equivalent_duplicates() -> None:
    first = _account("duplicate@example.invalid")
    second = _account("DUPLICATE@example.invalid")
    first.workspace_id = "equivalent-slot"
    first.workspace_label = None
    second.workspace_id = None
    second.workspace_label = "equivalent-slot"

    with pytest.raises(ValueError, match="duplicate account identities"):
        new_payload([first, second])


def test_identity_conflict_mapping_redacts_domain_exception_identity() -> None:
    domain_error = AccountIdentityConflictError("private-identity@example.invalid")

    with pytest.raises(DashboardConflictError) as caught:
        _raise_bundle_error(domain_error)

    assert caught.value.code == "account_identity_conflict"
    assert caught.value.message == "Account identity conflicts with multiple destination accounts"
    assert "private-identity" not in caught.value.message
    assert _bundle_error_outcome(domain_error) == "account_identity_conflict"


@pytest.mark.asyncio
async def test_post_import_validation_success_clears_imported_and_replaced_account_routing(monkeypatch) -> None:
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(status=AccountStatus.ACTIVE)))
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(return_value=SimpleNamespace(fetch_succeeded=True))),
    )
    cleared: list[str] = []
    monkeypatch.setattr("app.modules.accounts.service.clear_account_routing_unavailable", cleared.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(account_id="new-account", outcome="imported"),
            BundlePersistenceResult(account_id="local-account", outcome="replaced"),
        ]
    )

    assert warnings == {}
    assert cleared == ["new-account", "local-account"]


@pytest.mark.asyncio
async def test_post_import_validation_failure_is_fixed_safe_warning(monkeypatch) -> None:
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(status=AccountStatus.ACTIVE)))
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(side_effect=RuntimeError("upstream detail"))),
    )
    unavailable: list[str] = []
    monkeypatch.setattr("app.modules.accounts.service.mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [BundlePersistenceResult(account_id="failed-account", outcome="imported")]
    )

    assert warnings == {"failed-account": BUNDLE_VALIDATION_WARNING}
    assert BUNDLE_VALIDATION_AGGREGATE_WARNING == "Some imported accounts could not be validated."
    assert "upstream detail" not in warnings["failed-account"]
    assert unavailable == ["failed-account"]


@pytest.mark.asyncio
async def test_post_import_validation_persists_proxy_required_pause(monkeypatch) -> None:
    account = SimpleNamespace(
        id="proxy-required",
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        reset_at=None,
        blocked_at=None,
    )
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        update_status_if_current=AsyncMock(return_value=True),
    )
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._import_usage_refresh_allowed = AsyncMock(return_value=False)
    unavailable: list[str] = []
    monkeypatch.setattr("app.modules.accounts.service.mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [BundlePersistenceResult(account_id=account.id, outcome="imported")]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    repo.update_status_if_current.assert_awaited_once_with(
        account.id,
        AccountStatus.PAUSED,
        accounts_service_module.IMPORT_PROXY_REQUIRED_PAUSE_REASON,
        None,
        blocked_at=None,
        expected_status=AccountStatus.ACTIVE,
        expected_deactivation_reason=None,
        expected_reset_at=None,
        expected_blocked_at=None,
    )
    assert unavailable == [account.id]


@pytest.mark.asyncio
async def test_post_import_validation_does_not_cancel_proxy_pause_at_deadline(monkeypatch) -> None:
    account = SimpleNamespace(
        id="proxy-required-slow-pause",
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        reset_at=None,
        blocked_at=None,
    )

    async def persist_pause(*_args, **_kwargs) -> bool:
        await asyncio.sleep(0.02)
        account.status = AccountStatus.PAUSED
        return True

    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        update_status_if_current=AsyncMock(side_effect=persist_pause),
    )
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._import_usage_refresh_allowed = AsyncMock(return_value=False)
    routing_mark_statuses: list[AccountStatus] = []
    monkeypatch.setattr(accounts_service_module, "BUNDLE_VALIDATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        accounts_service_module,
        "mark_account_routing_unavailable",
        lambda _account_id: routing_mark_statuses.append(account.status),
    )

    warnings = await service._validate_imported_bundle_accounts(
        [BundlePersistenceResult(account_id=account.id, outcome="imported")]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    assert account.status == AccountStatus.PAUSED
    assert routing_mark_statuses == [AccountStatus.PAUSED]
    repo.update_status_if_current.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_import_validation_uses_one_deadline_for_the_batch(monkeypatch) -> None:
    accounts = {
        account_id: SimpleNamespace(id=account_id, status=AccountStatus.ACTIVE)
        for account_id in ("slow-account", "remaining-account")
    }
    repo = SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda account_id: accounts[account_id]))
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._import_usage_refresh_allowed = AsyncMock(return_value=True)

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    refresh = AsyncMock(side_effect=never_finishes)
    service._usage_updater = cast(UsageUpdater, SimpleNamespace(force_refresh_result=refresh))
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "BUNDLE_VALIDATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(account_id="slow-account", outcome="imported"),
            BundlePersistenceResult(account_id="remaining-account", outcome="imported"),
        ]
    )

    assert warnings == {
        "slow-account": BUNDLE_VALIDATION_WARNING,
        "remaining-account": BUNDLE_VALIDATION_WARNING,
    }
    assert unavailable == ["slow-account", "remaining-account"]
    assert refresh.await_count == 1

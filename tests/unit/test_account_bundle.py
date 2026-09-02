from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.clients.usage import UsageFetchError
from app.core.clients.usage import fetch_usage as core_fetch_usage
from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardConflictError
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts import service as accounts_service_module
from app.modules.accounts.account_bundle import (
    MAX_BUNDLE_ACCOUNTS,
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
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.accounts.repository import (
    BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
    AccountIdentityConflictError,
    AccountsRepository,
    BundlePersistenceResult,
)
from app.modules.accounts.service import (
    BUNDLE_VALIDATION_AGGREGATE_WARNING,
    BUNDLE_VALIDATION_WARNING,
    AccountsService,
    InvalidAuthJsonError,
)
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.background_repository import BackgroundAdditionalUsageRepository, BackgroundUsageRepository
from app.modules.usage.updater import AccountRefreshResult, UsageUpdater, _BundleValidationAuthRepository

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


@pytest.mark.asyncio
async def test_export_rejects_too_many_accounts_before_identity_or_credential_work() -> None:
    accounts = [SimpleNamespace()] * (MAX_BUNDLE_ACCOUNTS + 1)
    repo = SimpleNamespace(
        list_accounts=AsyncMock(return_value=accounts),
        account_bundle_identity_matches=AsyncMock(),
    )
    service = AccountsService(repo=cast(AccountsRepository, repo))
    decrypt = Mock()
    service._encryptor = cast(TokenEncryptor, SimpleNamespace(decrypt=decrypt))

    with pytest.raises(InvalidAuthJsonError, match="maximum account count"):
        await service.export_account_bundle(None, "passphrase", max_bytes=MAX_BYTES)

    repo.account_bundle_identity_matches.assert_not_awaited()
    decrypt.assert_not_called()


def test_payload_rejects_workspace_id_label_equivalent_duplicates() -> None:
    first = _account("duplicate@example.invalid")
    second = _account("DUPLICATE@example.invalid")
    first.workspace_id = "equivalent-slot"
    first.workspace_label = None
    second.workspace_id = None
    second.workspace_label = "equivalent-slot"

    with pytest.raises(ValueError, match="duplicate account identities"):
        new_payload([first, second])


@pytest.mark.parametrize("legacy_first", [False, True])
def test_payload_rejects_canonical_workspace_label_matching_legacy_label(legacy_first: bool) -> None:
    canonical = _account("duplicate@example.invalid")
    legacy = _account("DUPLICATE@example.invalid")
    canonical.workspace_id = "ws-123"
    canonical.workspace_label = "Team"
    legacy.workspace_id = None
    legacy.workspace_label = "Team"

    accounts = [legacy, canonical] if legacy_first else [canonical, legacy]
    with pytest.raises(ValueError, match="duplicate account identities"):
        new_payload(accounts)


def test_payload_allows_shared_workspace_label_for_distinct_workspace_ids() -> None:
    first = _account("operator@example.invalid")
    second = _account("OPERATOR@example.invalid")
    first.workspace_id = "canonical-slot-a"
    first.workspace_label = "Shared label"
    second.workspace_id = "canonical-slot-b"
    second.workspace_label = "Shared label"

    assert len(new_payload([first, second]).accounts) == 2


def test_identity_conflict_mapping_redacts_domain_exception_identity() -> None:
    domain_error = AccountIdentityConflictError("private-identity@example.invalid")

    with pytest.raises(DashboardConflictError) as caught:
        _raise_bundle_error(domain_error)

    assert caught.value.code == "account_identity_conflict"
    assert caught.value.message == "Account identity conflicts with multiple destination accounts"
    assert "private-identity" not in caught.value.message
    assert _bundle_error_outcome(domain_error) == "account_identity_conflict"


@pytest.mark.asyncio
async def test_bundle_commit_propagates_selection_invalidation_before_cancellable_validation(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    validation_started = asyncio.Event()
    persisted_result = BundlePersistenceResult(
        account_id="quarantined-import",
        outcome="imported",
        restore_status=AccountStatus.ACTIVE,
    )

    async def persist_bundle(*_args, **_kwargs) -> list[BundlePersistenceResult]:
        events.append(("persisted", persisted_result.account_id))
        return [persisted_result]

    async def blocked_validation(_persisted: list[BundlePersistenceResult]) -> None:
        events.append(("validation", "started"))
        validation_started.set()
        await asyncio.Event().wait()

    def invalidate_selection(*, propagate: bool = True) -> None:
        events.append(("invalidate", propagate))

    repo = SimpleNamespace(
        account_bundle_identity_matches=AsyncMock(return_value=[None]),
        persist_account_bundle=AsyncMock(side_effect=persist_bundle),
    )
    service = AccountsService(repo=cast(AccountsRepository, repo))
    service._validate_imported_bundle_accounts = cast(Any, blocked_validation)
    monkeypatch.setattr(
        accounts_service_module,
        "get_account_selection_cache",
        lambda: SimpleNamespace(invalidate=invalidate_selection),
    )
    monkeypatch.setattr(
        accounts_service_module,
        "mark_account_routing_unavailable",
        lambda account_id: events.append(("unavailable", account_id)),
    )
    raw = encrypt_bundle(new_payload([_account()]), "passphrase", max_bytes=MAX_BYTES)
    task = asyncio.create_task(
        service.commit_account_bundle(
            raw,
            "passphrase",
            integrity_token=bundle_integrity_token(raw),
            conflict_mode="skip",
            confirm_replace=False,
            max_bytes=MAX_BYTES,
        )
    )

    await validation_started.wait()
    assert events == [
        ("persisted", "quarantined-import"),
        ("invalidate", True),
        ("unavailable", "quarantined-import"),
        ("validation", "started"),
    ]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events.count(("invalidate", True)) == 1


@pytest.mark.asyncio
async def test_post_import_validation_success_clears_imported_and_replaced_account_routing(monkeypatch) -> None:
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(
            side_effect=lambda account_id: SimpleNamespace(
                id=account_id,
                status=AccountStatus.PAUSED,
                refresh_token_encrypted=b"quarantined-token",
            )
        ),
        restore_validated_bundle_account=AsyncMock(return_value=True),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(return_value=SimpleNamespace(fetch_succeeded=True))),
    )
    cleared: list[str] = []
    events: list[tuple[str, str | bool]] = []

    def invalidate_selection(*, propagate: bool = True) -> None:
        events.append(("invalidate", propagate))

    def clear_routing(account_id: str) -> None:
        events.append(("clear", account_id))
        cleared.append(account_id)

    monkeypatch.setattr(
        "app.modules.accounts.service.get_account_selection_cache",
        lambda: SimpleNamespace(invalidate=invalidate_selection),
    )
    monkeypatch.setattr("app.modules.accounts.service.clear_account_routing_unavailable", clear_routing)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id="new-account",
                outcome="imported",
                restore_status=AccountStatus.ACTIVE,
            ),
            BundlePersistenceResult(
                account_id="local-account",
                outcome="replaced",
                restore_status=AccountStatus.ACTIVE,
            ),
        ]
    )

    assert warnings == {}
    assert cleared == ["new-account", "local-account"]
    assert events == [
        ("invalidate", False),
        ("clear", "new-account"),
        ("invalidate", False),
        ("clear", "local-account"),
    ]
    assert validation_repo.restore_validated_bundle_account.await_count == 2


@pytest.mark.asyncio
async def test_post_import_validation_success_preserves_non_active_replacement(monkeypatch) -> None:
    account = SimpleNamespace(
        id="rate-limited",
        status=AccountStatus.PAUSED,
        refresh_token_encrypted=b"replacement-token",
    )
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        restore_validated_bundle_account=AsyncMock(return_value=True),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(return_value=SimpleNamespace(fetch_succeeded=True))),
    )
    cleared: list[str] = []
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "clear_account_routing_unavailable", cleared.append)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id="rate-limited",
                outcome="replaced",
                restore_status=AccountStatus.RATE_LIMITED,
                restore_deactivation_reason="destination-rate-limit",
                restore_reset_at=101,
                restore_blocked_at=202,
            )
        ]
    )

    assert warnings == {}
    validation_repo.restore_validated_bundle_account.assert_awaited_once_with(
        "rate-limited",
        expected_refresh_token_encrypted=b"replacement-token",
        status=AccountStatus.RATE_LIMITED,
        deactivation_reason="destination-rate-limit",
        reset_at=101,
        blocked_at=202,
    )
    assert cleared == ["rate-limited"]
    assert unavailable == []


@pytest.mark.parametrize(
    ("restore_status", "restore_reason", "restore_reset_at", "restore_blocked_at"),
    [
        (AccountStatus.REAUTH_REQUIRED, "destination-reauth", 505, 606),
        (AccountStatus.RATE_LIMITED, "destination-rate-limit", 101, 202),
        (AccountStatus.QUOTA_EXCEEDED, "destination-quota", 303, 404),
    ],
)
@pytest.mark.asyncio
async def test_quarantined_routable_non_active_replacement_rotates_expired_token_before_restoration(
    monkeypatch,
    restore_status: AccountStatus,
    restore_reason: str,
    restore_reset_at: int,
    restore_blocked_at: int,
) -> None:
    encryptor = TokenEncryptor()
    original_refresh_ciphertext = encryptor.encrypt(f"original-{restore_status.value}-refresh-token")
    account = Account(
        id=f"guarded-{restore_status.value}",
        email=f"guarded-{restore_status.value}@example.invalid",
        chatgpt_account_id=f"workspace-{restore_status.value}",
        plan_type="plus",
        status=AccountStatus.PAUSED,
        deactivation_reason=BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
        access_token_encrypted=encryptor.encrypt("expired-access-token"),
        refresh_token_encrypted=original_refresh_ciphertext,
        id_token_encrypted=encryptor.encrypt("original-id-token"),
    )
    rotated_refresh_ciphertexts: list[bytes] = []

    async def rotate_tokens(
        _account_id: str,
        _access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        _id_token_encrypted: bytes,
        _last_refresh,
        *,
        expected_refresh_token_encrypted: bytes,
    ) -> bool:
        assert expected_refresh_token_encrypted == original_refresh_ciphertext
        rotated_refresh_ciphertexts.append(refresh_token_encrypted)
        return True

    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        get_by_id_fresh=AsyncMock(return_value=account),
        rotate_tokens=AsyncMock(side_effect=rotate_tokens),
        update_status=AsyncMock(return_value=True),
        update_status_if_current=AsyncMock(return_value=True),
        update_account_metadata=AsyncMock(return_value=True),
        workspace_slot_taken=AsyncMock(return_value=False),
        restore_validated_bundle_account=AsyncMock(return_value=True),
    )
    auth_repo = _BundleValidationAuthRepository(cast(Any, validation_repo))
    guarded_updater = UsageUpdater(
        cast(Any, SimpleNamespace()),
        accounts_repo=cast(Any, validation_repo),
        auth_manager=AuthManager(cast(Any, auth_repo), redact_sensitive_details=True),
        redact_sensitive_logs=True,
        bundle_validation_mode=True,
    )
    forbidden_updater = SimpleNamespace(
        force_refresh_result=AsyncMock(side_effect=AssertionError("unguarded validator selected"))
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._bundle_validation_usage_updater = guarded_updater
    service._bundle_nonreactivating_validation_usage_updater = cast(UsageUpdater, forbidden_updater)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)

    async def refresh_tokens(_manager, refresh_token: str, *, account: Account) -> TokenRefreshResult:
        assert refresh_token == f"original-{restore_status.value}-refresh-token"
        return TokenRefreshResult(
            access_token="rotated-access-token",
            refresh_token=f"rotated-{restore_status.value}-refresh-token",
            id_token="rotated-id-token",
            account_id=account.chatgpt_account_id,
            plan_type=None,
            email=None,
        )

    monkeypatch.setattr(auth_manager_module, "get_refresh_claim_coordinator", lambda: None)
    monkeypatch.setattr(AuthManager, "_refresh_tokens", refresh_tokens)
    monkeypatch.setattr(
        "app.modules.usage.updater.fetch_usage",
        AsyncMock(side_effect=[UsageFetchError(401, "expired"), UsagePayload.model_validate({})]),
    )
    monkeypatch.setattr(
        "app.modules.usage.updater._resolve_upstream_route_for_account",
        AsyncMock(return_value=None),
    )
    selection_invalidations: list[bool] = []
    cleared: list[str] = []
    unavailable: list[str] = []
    monkeypatch.setattr(
        accounts_service_module,
        "get_account_selection_cache",
        lambda: SimpleNamespace(
            invalidate=lambda *, propagate=True: selection_invalidations.append(propagate),
        ),
    )
    monkeypatch.setattr(accounts_service_module, "clear_account_routing_unavailable", cleared.append)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="replaced",
                restore_status=restore_status,
                restore_deactivation_reason=restore_reason,
                restore_reset_at=restore_reset_at,
                restore_blocked_at=restore_blocked_at,
            )
        ]
    )

    assert warnings == {}
    assert len(rotated_refresh_ciphertexts) == 1
    assert account.refresh_token_encrypted == rotated_refresh_ciphertexts[0]
    assert encryptor.decrypt(account.refresh_token_encrypted) == f"rotated-{restore_status.value}-refresh-token"
    validation_repo.restore_validated_bundle_account.assert_awaited_once_with(
        account.id,
        expected_refresh_token_encrypted=rotated_refresh_ciphertexts[0],
        status=restore_status,
        deactivation_reason=restore_reason,
        reset_at=restore_reset_at,
        blocked_at=restore_blocked_at,
    )
    forbidden_updater.force_refresh_result.assert_not_awaited()
    validation_repo.update_status.assert_not_awaited()
    validation_repo.update_status_if_current.assert_not_awaited()
    validation_repo.update_account_metadata.assert_not_awaited()
    assert selection_invalidations == [False]
    assert cleared == [account.id]
    assert unavailable == []


@pytest.mark.asyncio
async def test_post_import_validation_reactivation_cas_miss_stays_unroutable(monkeypatch) -> None:
    account = SimpleNamespace(
        id="concurrently-replaced",
        status=AccountStatus.PAUSED,
        refresh_token_encrypted=b"stale-token-version",
    )
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        restore_validated_bundle_account=AsyncMock(return_value=False),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(return_value=SimpleNamespace(fetch_succeeded=True))),
    )
    cleared: list[str] = []
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "clear_account_routing_unavailable", cleared.append)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="replaced",
                restore_status=AccountStatus.ACTIVE,
            )
        ]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    assert account.status == AccountStatus.PAUSED
    assert cleared == []
    assert unavailable == [account.id]


@pytest.mark.asyncio
async def test_bundle_validation_restore_cas_uses_the_credential_version_actually_validated(monkeypatch) -> None:
    account = Account(
        id="validation-credential-race",
        email="validation-credential-race@example.invalid",
        status=AccountStatus.PAUSED,
        access_token_encrypted=b"committed-access-token",
        refresh_token_encrypted=b"committed-refresh-token",
        id_token_encrypted=b"committed-id-token",
    )
    validated_rotated_version = b"validated-rotated-refresh-token"
    unrelated_replacement_version = b"unrelated-replacement-refresh-token"
    concurrent_replacement = Account(
        id=account.id,
        email=account.email,
        status=AccountStatus.PAUSED,
        access_token_encrypted=b"unrelated-replacement-access-token",
        refresh_token_encrypted=unrelated_replacement_version,
        id_token_encrypted=b"unrelated-replacement-id-token",
    )
    current_database_version = account.refresh_token_encrypted
    restore_expected_versions: list[bytes] = []

    async def refresh_with_guarded_rotation(refresh_account: Account, **_kwargs) -> AccountRefreshResult:
        nonlocal current_database_version
        # Mirror AuthManager's guarded successful rotation on the exact object
        # being validated, followed by an unrelated repository replacement.
        refresh_account.refresh_token_encrypted = validated_rotated_version
        current_database_version = unrelated_replacement_version
        return AccountRefreshResult(usage_written=False, fetch_succeeded=True)

    async def restore_if_current(
        _account_id: str,
        *,
        expected_refresh_token_encrypted: bytes,
        **_kwargs,
    ) -> bool:
        restore_expected_versions.append(expected_refresh_token_encrypted)
        return expected_refresh_token_encrypted == current_database_version

    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        get_by_id_fresh=AsyncMock(return_value=concurrent_replacement),
        restore_validated_bundle_account=AsyncMock(side_effect=restore_if_current),
    )
    updater = UsageUpdater(
        cast(Any, SimpleNamespace()),
        accounts_repo=cast(Any, validation_repo),
        bundle_validation_mode=True,
    )
    monkeypatch.setattr(updater, "_refresh_account", refresh_with_guarded_rotation)
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._bundle_validation_usage_updater = updater
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    cleared: list[str] = []
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "clear_account_routing_unavailable", cleared.append)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="replaced",
                restore_status=AccountStatus.ACTIVE,
            )
        ]
    )

    assert restore_expected_versions == [validated_rotated_version]
    assert validation_repo.get_by_id_fresh.await_count == 0
    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    assert cleared == []
    assert unavailable == [account.id]


def test_bundle_validation_uses_only_owned_background_repositories() -> None:
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))

    assert isinstance(service._bundle_validation_repo, BackgroundAccountsRepository)
    assert isinstance(service._bundle_validation_usage_updater._accounts_repo, BackgroundAccountsRepository)
    assert service._bundle_validation_usage_updater._redact_sensitive_logs is True
    assert service._bundle_validation_usage_updater._bundle_validation_mode is True
    assert service._bundle_validation_usage_updater._auth_manager is not None
    assert service._bundle_validation_usage_updater._auth_manager._redact_sensitive_details is True
    auth_repo = service._bundle_validation_usage_updater._auth_manager._repo
    assert isinstance(
        auth_repo,
        _BundleValidationAuthRepository,
    )
    assert isinstance(
        auth_repo._delegate,
        BackgroundAccountsRepository,
    )
    assert isinstance(
        service._bundle_nonreactivating_validation_usage_updater._usage_repo,
        BackgroundUsageRepository,
    )
    assert isinstance(
        service._bundle_nonreactivating_validation_usage_updater._additional_usage_repo,
        BackgroundAdditionalUsageRepository,
    )
    assert service._bundle_nonreactivating_validation_usage_updater._accounts_repo is None
    assert service._bundle_nonreactivating_validation_usage_updater._redact_sensitive_logs is True
    assert service._bundle_nonreactivating_validation_usage_updater._bundle_validation_mode is True


@pytest.mark.asyncio
async def test_bundle_validation_auth_repository_allows_only_guarded_token_rotation() -> None:
    delegate = SimpleNamespace(
        rotate_tokens=AsyncMock(return_value=True),
        update_status=AsyncMock(return_value=True),
        update_status_if_current=AsyncMock(return_value=True),
        update_account_metadata=AsyncMock(return_value=True),
    )
    repo = _BundleValidationAuthRepository(cast(Any, delegate))

    rotated = await repo.rotate_tokens(
        "bundle-rotation",
        b"new-access",
        b"new-refresh",
        b"new-id",
        accounts_service_module.utcnow(),
        expected_refresh_token_encrypted=b"validated-refresh",
        plan_type="team",
        email="untrusted-new@example.invalid",
        chatgpt_account_id="untrusted-upstream-id",
        chatgpt_user_id="untrusted-user-id",
        workspace_id="untrusted-workspace-id",
        workspace_label="untrusted-workspace-label",
        seat_type="owner",
    )

    assert rotated is True
    delegate.rotate_tokens.assert_awaited_once()
    rotate_call = delegate.rotate_tokens.await_args
    assert rotate_call.kwargs == {"expected_refresh_token_encrypted": b"validated-refresh"}
    assert await repo.update_status("bundle-rotation", AccountStatus.REAUTH_REQUIRED) is False
    assert (
        await repo.update_status_if_current(
            "bundle-rotation",
            AccountStatus.REAUTH_REQUIRED,
            expected_status=AccountStatus.PAUSED,
        )
        is False
    )
    assert await repo.update_account_metadata("bundle-rotation", plan_type="free") is False
    delegate.update_status.assert_not_awaited()
    delegate.update_status_if_current.assert_not_awaited()
    delegate.update_account_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_bundle_validation_permanent_refresh_failure_preserves_quarantine_and_routing(monkeypatch) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="bundle-permanent-refresh-failure",
        email="bundle-permanent-refresh-failure@example.invalid",
        status=AccountStatus.PAUSED,
        deactivation_reason=BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
        access_token_encrypted=encryptor.encrypt("expired-access-token"),
        refresh_token_encrypted=encryptor.encrypt("invalid-refresh-token"),
        id_token_encrypted=encryptor.encrypt("synthetic-id-token"),
    )
    delegate = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        get_by_id_fresh=AsyncMock(return_value=account),
        rotate_tokens=AsyncMock(return_value=True),
        update_status=AsyncMock(return_value=True),
        update_status_if_current=AsyncMock(return_value=True),
        update_account_metadata=AsyncMock(return_value=True),
        workspace_slot_taken=AsyncMock(return_value=False),
    )
    auth_repo = _BundleValidationAuthRepository(cast(Any, delegate))
    updater = UsageUpdater(
        cast(Any, SimpleNamespace()),
        accounts_repo=cast(Any, delegate),
        auth_manager=AuthManager(cast(Any, auth_repo), redact_sensitive_details=True),
        redact_sensitive_logs=True,
        bundle_validation_mode=True,
    )
    routing_marks: list[str] = []
    selection_invalidations: list[bool] = []

    async def reject_refresh(*_args, **_kwargs):
        raise RefreshError("refresh_token_invalidated", "private permanent detail", True)

    monkeypatch.setattr(auth_manager_module, "get_refresh_claim_coordinator", lambda: None)
    monkeypatch.setattr(AuthManager, "_refresh_tokens", reject_refresh)
    monkeypatch.setattr(
        "app.modules.usage.updater.fetch_usage",
        AsyncMock(side_effect=UsageFetchError(401, "private usage detail")),
    )
    monkeypatch.setattr(
        "app.modules.usage.updater._resolve_upstream_route_for_account",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(auth_manager_module, "mark_account_routing_unavailable", routing_marks.append)
    monkeypatch.setattr(
        auth_manager_module,
        "get_account_selection_cache",
        lambda: SimpleNamespace(invalidate=lambda: selection_invalidations.append(True)),
    )

    result = await updater.force_refresh_result(account, ignore_refresh_disabled=True)

    assert result.fetch_succeeded is False
    assert account.status == AccountStatus.PAUSED
    assert account.deactivation_reason == BUNDLE_IMPORT_VALIDATION_PAUSE_REASON
    assert routing_marks == []
    assert selection_invalidations == []
    delegate.update_status.assert_not_awaited()
    delegate.update_status_if_current.assert_not_awaited()
    delegate.update_account_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_bundle_validation_forced_refresh_log_redacts_exception_details(caplog, monkeypatch) -> None:
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    updater = service._bundle_validation_usage_updater
    monkeypatch.setattr(
        updater,
        "_refresh_account",
        AsyncMock(side_effect=RuntimeError("private-workspace-and-token-detail")),
    )
    account = cast(Account, SimpleNamespace(id="safe-destination-id", status=AccountStatus.PAUSED))

    with caplog.at_level("WARNING", logger="app.modules.usage.updater"):
        result = await updater.force_refresh_result(account, ignore_refresh_disabled=True)

    assert result.fetch_succeeded is False
    assert "safe-destination-id" in caplog.text
    assert "private-workspace-and-token-detail" not in caplog.text


@pytest.mark.asyncio
async def test_bundle_validation_redacts_nested_usage_error_and_lifecycle_reason(caplog, monkeypatch) -> None:
    marker = "private-upstream-body-marker"

    class ErrorResponse:
        status = 402

        async def json(self, content_type=None):
            return {"error": {"message": marker}}

    class RequestContext:
        async def __aenter__(self):
            return ErrorResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class RetryClient:
        def request(self, *_args, **_kwargs):
            return RequestContext()

    async def nested_fetch_usage(**kwargs):
        return await core_fetch_usage(
            access_token=kwargs["access_token"],
            account_id=kwargs["account_id"],
            base_url="http://usage.test/backend-api",
            max_retries=0,
            timeout_seconds=1.0,
            client=cast(Any, RetryClient()),
            allow_direct_egress=True,
            redact_sensitive_logs=kwargs["redact_sensitive_logs"],
        )

    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    updater = service._bundle_validation_usage_updater
    status_repo = SimpleNamespace(update_status=AsyncMock())
    assert updater._auth_manager is not None
    cast(Any, updater._auth_manager)._repo = status_repo
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", nested_fetch_usage)
    monkeypatch.setattr(
        "app.modules.usage.updater._resolve_upstream_route_for_account",
        AsyncMock(return_value=None),
    )
    encryptor = TokenEncryptor()
    account = Account(
        id="safe-destination-id",
        email="safe@example.invalid",
        plan_type="plus",
        chatgpt_account_id="safe-upstream-id",
        access_token_encrypted=encryptor.encrypt("synthetic-access-token"),
        refresh_token_encrypted=encryptor.encrypt("synthetic-refresh-token"),
        id_token_encrypted=encryptor.encrypt("synthetic-id-token"),
        status=AccountStatus.PAUSED,
    )

    with caplog.at_level("WARNING"):
        result = await updater._refresh_account(account, usage_account_id=account.chatgpt_account_id)

    assert result.fetch_succeeded is False
    assert marker not in caplog.text
    assert account.status == AccountStatus.PAUSED
    assert account.deactivation_reason is None
    status_repo.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_bundle_validation_does_not_persist_usage_identity_metadata(monkeypatch) -> None:
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    updater = service._bundle_validation_usage_updater
    sync_identity_metadata = AsyncMock(return_value=True)
    monkeypatch.setattr(updater, "_sync_identity_metadata", sync_identity_metadata)
    monkeypatch.setattr(
        "app.modules.usage.updater.fetch_usage",
        AsyncMock(
            return_value=UsagePayload(
                plan_type="team",
                workspace_id="safe-workspace-id",
                workspace_label="untrusted-new-label",
                seat_type="owner",
            )
        ),
    )
    monkeypatch.setattr(
        "app.modules.usage.updater._resolve_upstream_route_for_account",
        AsyncMock(return_value=None),
    )
    encryptor = TokenEncryptor()
    account = Account(
        id="metadata-guarded",
        email="metadata-guarded@example.invalid",
        plan_type="plus",
        chatgpt_account_id="metadata-guarded-upstream",
        workspace_id="safe-workspace-id",
        workspace_label="stored-label",
        seat_type="member",
        access_token_encrypted=encryptor.encrypt("synthetic-access-token"),
        refresh_token_encrypted=encryptor.encrypt("synthetic-refresh-token"),
        id_token_encrypted=encryptor.encrypt("synthetic-id-token"),
        status=AccountStatus.PAUSED,
    )

    result = await updater._refresh_account(account, usage_account_id=account.chatgpt_account_id)

    assert result.fetch_succeeded is True
    sync_identity_metadata.assert_not_awaited()
    assert account.plan_type == "plus"
    assert account.workspace_label == "stored-label"
    assert account.seat_type == "member"


@pytest.mark.asyncio
async def test_bundle_validation_does_not_mutate_plan_downgrade_observations(monkeypatch) -> None:
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    updater = service._bundle_validation_usage_updater
    observation_store = SimpleNamespace(
        observe=AsyncMock(return_value=2),
        clear=AsyncMock(),
    )
    fetch_usage_mock = AsyncMock()
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", fetch_usage_mock)
    monkeypatch.setattr(
        "app.modules.usage.updater._resolve_upstream_route_for_account",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.modules.usage.updater._plan_downgrade_observation_store",
        lambda: observation_store,
    )
    encryptor = TokenEncryptor()
    account = Account(
        id="observation-store-guarded",
        email="observation-store-guarded@example.invalid",
        plan_type="plus",
        chatgpt_account_id="observation-store-guarded-upstream",
        access_token_encrypted=encryptor.encrypt("synthetic-access-token"),
        refresh_token_encrypted=encryptor.encrypt("synthetic-refresh-token"),
        id_token_encrypted=encryptor.encrypt("synthetic-id-token"),
        status=AccountStatus.PAUSED,
    )

    fetch_usage_mock.return_value = UsagePayload(plan_type="free")
    downgrade_result = await updater._refresh_account(account, usage_account_id=account.chatgpt_account_id)

    fetch_usage_mock.return_value = UsagePayload(plan_type="pro")
    paid_result = await updater._refresh_account(account, usage_account_id=account.chatgpt_account_id)

    assert downgrade_result.fetch_succeeded is False
    assert paid_result.fetch_succeeded is True
    observation_store.observe.assert_not_awaited()
    observation_store.clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_import_validation_failure_is_fixed_safe_warning(monkeypatch) -> None:
    account = SimpleNamespace(status=AccountStatus.PAUSED, refresh_token_encrypted=b"quarantined-token")
    validation_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=account))
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(side_effect=RuntimeError("upstream detail"))),
    )
    unavailable: list[str] = []
    monkeypatch.setattr("app.modules.accounts.service.mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id="failed-account",
                outcome="imported",
                restore_status=AccountStatus.ACTIVE,
            )
        ]
    )

    assert warnings == {"failed-account": BUNDLE_VALIDATION_WARNING}
    assert BUNDLE_VALIDATION_AGGREGATE_WARNING == "Some imported accounts could not be validated."
    assert "upstream detail" not in warnings["failed-account"]
    assert unavailable == ["failed-account"]


@pytest.mark.asyncio
async def test_post_import_validation_failure_leaves_routable_non_active_replacement_quarantined(
    monkeypatch,
) -> None:
    account = SimpleNamespace(
        id="failed-rate-limited",
        status=AccountStatus.PAUSED,
        refresh_token_encrypted=b"quarantined-rate-limit-token",
    )
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        restore_validated_bundle_account=AsyncMock(),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_nonreactivating_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=AsyncMock(return_value=SimpleNamespace(fetch_succeeded=False))),
    )
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="replaced",
                restore_status=AccountStatus.RATE_LIMITED,
                restore_deactivation_reason="destination-rate-limit",
                restore_reset_at=101,
                restore_blocked_at=202,
            )
        ]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    validation_repo.restore_validated_bundle_account.assert_not_awaited()
    assert account.status == AccountStatus.PAUSED
    assert unavailable == [account.id]


@pytest.mark.asyncio
async def test_post_import_validation_outer_cancellation_keeps_quarantine() -> None:
    account = SimpleNamespace(
        id="cancelled-import",
        status=AccountStatus.PAUSED,
        refresh_token_encrypted=b"quarantined-token",
    )
    refresh_started = asyncio.Event()
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        restore_validated_bundle_account=AsyncMock(),
    )

    async def blocked_refresh(*_args, **_kwargs):
        refresh_started.set()
        await asyncio.Event().wait()

    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)
    service._bundle_validation_usage_updater = cast(
        UsageUpdater,
        SimpleNamespace(force_refresh_result=blocked_refresh),
    )
    task = asyncio.create_task(
        service._validate_imported_bundle_accounts(
            [
                BundlePersistenceResult(
                    account_id=account.id,
                    outcome="imported",
                    restore_status=AccountStatus.ACTIVE,
                )
            ]
        )
    )
    await refresh_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert account.status == AccountStatus.PAUSED
    validation_repo.restore_validated_bundle_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_import_validation_persists_proxy_required_pause(monkeypatch) -> None:
    account = SimpleNamespace(
        id="proxy-required",
        status=AccountStatus.PAUSED,
        deactivation_reason=BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
        reset_at=None,
        blocked_at=None,
        refresh_token_encrypted=b"quarantined-token",
    )
    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        update_status_if_current=AsyncMock(return_value=True),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=False)
    unavailable: list[str] = []
    monkeypatch.setattr("app.modules.accounts.service.mark_account_routing_unavailable", unavailable.append)

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="imported",
                restore_status=AccountStatus.ACTIVE,
            )
        ]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    validation_repo.update_status_if_current.assert_awaited_once_with(
        account.id,
        AccountStatus.PAUSED,
        accounts_service_module.IMPORT_PROXY_REQUIRED_PAUSE_REASON,
        None,
        blocked_at=None,
        expected_status=AccountStatus.PAUSED,
        expected_deactivation_reason=BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
        expected_reset_at=None,
        expected_blocked_at=None,
        expected_refresh_token_encrypted=b"quarantined-token",
    )
    assert unavailable == [account.id]


@pytest.mark.asyncio
async def test_post_import_validation_timeout_keeps_proxy_account_quarantined(monkeypatch) -> None:
    account = SimpleNamespace(
        id="proxy-required-slow-pause",
        status=AccountStatus.PAUSED,
        deactivation_reason=BUNDLE_IMPORT_VALIDATION_PAUSE_REASON,
        reset_at=None,
        blocked_at=None,
        refresh_token_encrypted=b"quarantined-token",
    )

    async def persist_pause(*_args, **_kwargs) -> bool:
        await asyncio.sleep(0.02)
        return True

    validation_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=account),
        update_status_if_current=AsyncMock(side_effect=persist_pause),
    )
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=False)
    routing_mark_statuses: list[AccountStatus] = []
    monkeypatch.setattr(accounts_service_module, "BUNDLE_VALIDATION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        accounts_service_module,
        "mark_account_routing_unavailable",
        lambda _account_id: routing_mark_statuses.append(account.status),
    )

    warnings = await service._validate_imported_bundle_accounts(
        [
            BundlePersistenceResult(
                account_id=account.id,
                outcome="imported",
                restore_status=AccountStatus.ACTIVE,
            )
        ]
    )

    assert warnings == {account.id: BUNDLE_VALIDATION_WARNING}
    assert account.status == AccountStatus.PAUSED
    assert routing_mark_statuses == [AccountStatus.PAUSED]
    validation_repo.update_status_if_current.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_import_validation_timeout_does_not_detach_unbounded_refreshes(monkeypatch) -> None:
    account_ids = ("slow-account", "waiting-account", "remaining-account")
    accounts = {
        account_id: SimpleNamespace(
            id=account_id,
            chatgpt_account_id=account_id,
            status=AccountStatus.PAUSED,
            refresh_token_encrypted=f"token-{account_id}".encode(),
        )
        for account_id in account_ids
    }
    validation_repo = SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda account_id: accounts[account_id]))
    service = AccountsService(repo=cast(AccountsRepository, SimpleNamespace()))
    service._bundle_validation_repo = cast(BackgroundAccountsRepository, validation_repo)
    service._background_import_usage_refresh_allowed = AsyncMock(return_value=True)

    refreshes_started: list[str] = []

    async def never_finishes(account, **_kwargs):
        refreshes_started.append(account.id)
        await asyncio.Event().wait()

    monkeypatch.setattr(service._bundle_validation_usage_updater, "_refresh_account", never_finishes)
    unavailable: list[str] = []
    monkeypatch.setattr(accounts_service_module, "BUNDLE_VALIDATION_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(accounts_service_module, "mark_account_routing_unavailable", unavailable.append)

    await usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()
    try:
        warnings = await service._validate_imported_bundle_accounts(
            [
                BundlePersistenceResult(
                    account_id=account_id,
                    outcome="imported",
                    restore_status=AccountStatus.ACTIVE,
                )
                for account_id in account_ids
            ]
        )

        assert warnings == {account_id: BUNDLE_VALIDATION_WARNING for account_id in account_ids}
        assert unavailable == list(account_ids)
        assert refreshes_started == ["slow-account"]
        assert list(usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT._inflight) == ["slow-account"]
    finally:
        await usage_updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()
        await asyncio.sleep(0)

"""Cross-replica token-refresh serialization regressions.

Two replicas are simulated as two independent AsyncSessions/AuthManagers over
one database with distinct refresh-claim claimant identities — the established
multi-replica pattern from ``tests/integration/test_multi_replica.py``.

Before the ``account_refresh_claims`` serialization landed, the concurrent-race
tests here failed with the loser calling upstream a second time, receiving a
permanent ``refresh_token_reused`` error, and writing ``REAUTH_REQUIRED`` (also
deleting the account's sticky sessions).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountRefreshClaim, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_refresh_state() -> None:
    auth_manager_module._clear_refresh_singleflight_state()


def _rotated_result(account_id: str) -> TokenRefreshResult:
    return TokenRefreshResult(
        access_token="access-new",
        refresh_token="refresh-new",
        id_token="id-new",
        account_id=None,
        plan_type="plus",
        email=None,
    )


async def _create_account(account_id: str, *, refresh_token: str = "refresh-old") -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-old"),
                refresh_token_encrypted=encryptor.encrypt(refresh_token),
                id_token_encrypted=encryptor.encrypt("id-old"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        session.add(
            StickySession(
                key=f"sticky-{account_id}",
                kind=StickySessionKind.STICKY_THREAD,
                account_id=account_id,
            )
        )
        await session.commit()


async def _insert_claim(account_id: str, *, claimed_by: str, expires_in_seconds: float) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            AccountRefreshClaim(
                account_id=account_id,
                claimed_by=claimed_by,
                claimed_at=now,
                claim_expires_at=now + timedelta(seconds=expires_in_seconds),
            )
        )
        await session.commit()


async def _account_snapshot(account_id: str) -> tuple[AccountStatus, str, bool]:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalars().one()
        sticky_present = (
            await session.execute(select(StickySession.key).where(StickySession.account_id == account_id))
        ).scalar_one_or_none() is not None
        return account.status, encryptor.decrypt(account.refresh_token_encrypted), sticky_present


@pytest.mark.asyncio
async def test_concurrent_cross_replica_refresh_runs_one_upstream_exchange(db_setup, monkeypatch):
    """THE RACE (failed pre-claims): both replicas force-refresh the same
    account; pre-claims the loser POSTed the same single-use refresh token,
    received permanent ``refresh_token_reused``, wrote REAUTH_REQUIRED, and
    deleted the sticky session. With claims, exactly one upstream exchange runs
    and the loser adopts the winner's rotation."""
    account_id = "acc_claim_race"
    await _create_account(account_id)

    upstream_calls = 0
    winner_started = asyncio.Event()
    winner_release = asyncio.Event()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls > 1:
            # This is exactly what upstream returns to the loser of a
            # concurrent rotation of a single-use refresh token.
            raise RefreshError("refresh_token_reused", "refresh token reused", True)
        winner_started.set()
        await winner_release.wait()
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session_a, SessionLocal() as session_b:
        repo_a = AccountsRepository(session_a)
        repo_b = AccountsRepository(session_b)
        account_a = await repo_a.get_by_id(account_id)
        account_b = await repo_b.get_by_id(account_id)
        assert account_a is not None and account_b is not None
        manager_a = AuthManager(repo_a, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        manager_b = AuthManager(repo_b, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        task_a = asyncio.create_task(manager_a.refresh_account(account_a))
        await asyncio.wait_for(winner_started.wait(), timeout=5)
        # Replica A holds the claim and is mid-exchange; replica B must wait.
        task_b = asyncio.create_task(manager_b.refresh_account(account_b))
        await asyncio.sleep(0.1)
        assert not task_b.done()

        winner_release.set()
        result_a = await asyncio.wait_for(task_a, timeout=5)
        result_b = await asyncio.wait_for(task_b, timeout=5)

    encryptor = TokenEncryptor()
    assert upstream_calls == 1
    assert encryptor.decrypt(result_a.refresh_token_encrypted) == "refresh-new"
    assert encryptor.decrypt(result_b.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True

    # The claim was released after the winner persisted.
    async with SessionLocal() as session:
        remaining = (
            await session.execute(select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id))
        ).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
async def test_expired_foreign_claim_is_taken_over(db_setup, monkeypatch):
    """Crashed-claimant liveness: an expired foreign claim must not block refresh."""
    account_id = "acc_claim_expired"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="dead-replica", expires_in_seconds=-5)

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert TokenEncryptor().decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_unexpired_foreign_claim_times_out_transient_and_is_not_cached(db_setup, monkeypatch):
    """Bounded wait: with a live foreign claim the loser must fail with a
    transient (non-permanent) error, never call upstream, never touch account
    status, and the singleflight must not cache the failure as permanent."""
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS", "0.05")
    get_settings.cache_clear()

    account_id = "acc_claim_blocked"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        with pytest.raises(RefreshError) as exc_info:
            await manager.ensure_fresh(account, force=True)
        assert exc_info.value.code == "refresh_claim_timeout"
        assert exc_info.value.is_permanent is False
        assert exc_info.value.transport_error is True
        assert upstream_calls == 0

        status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert stored_refresh_token == "refresh-old"
        assert sticky_present is True

        # Release the foreign claim: the next forced refresh must proceed
        # immediately. A cached permanent failure would re-raise instead.
        async with SessionLocal() as cleanup_session:
            claim = (
                await cleanup_session.execute(
                    select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id)
                )
            ).scalar_one()
            await cleanup_session.delete(claim)
            await cleanup_session.commit()

        refreshed = await asyncio.wait_for(manager.ensure_fresh(account, force=True), timeout=5)
        assert upstream_calls == 1
        assert TokenEncryptor().decrypt(refreshed.refresh_token_encrypted) == "refresh-new"


@pytest.mark.asyncio
async def test_winner_adopts_rotation_committed_before_its_claim(db_setup, monkeypatch):
    """Post-claim fresh re-read: when the material already rotated, the claim
    winner must adopt it with zero upstream calls."""
    account_id = "acc_claim_preclaim_rotation"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def unexpected_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("upstream exchange must not run when the material already rotated")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None

        # Another replica rotates and commits after our snapshot was taken.
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).update_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
            )

        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_permanent_failure_guard_sees_committed_rotation_despite_identity_map(db_setup, monkeypatch):
    """Stale-guard regression (failed pre-hardening): the loser's session
    identity map still holds the pre-rotation row; ``session.get`` returned it
    without a DB read and the loser wrote REAUTH_REQUIRED over a healthy
    account. The fresh ``populate_existing`` re-read must observe the winner's
    committed rotation and adopt it instead."""
    account_id = "acc_stale_guard"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as loser_session:
        loser_repo = AccountsRepository(loser_session)
        # Populate the loser session's identity map with the pre-rotation row.
        loser_account = await loser_repo.get_by_id(account_id)
        assert loser_account is not None

        # Winner commits the rotation through a different session (replica).
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).update_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
            )

        # Exercise the legacy (unclaimed) path: the hardening must protect
        # callers even without a claim coordinator.
        manager = AuthManager(loser_repo)
        result = await manager.refresh_account(loser_account)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True


@pytest.mark.asyncio
async def test_update_tokens_cas_rejects_stale_writer(db_setup):
    account_id = "acc_cas"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.update_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-stale"),
            refresh_token_encrypted=encryptor.encrypt("refresh-stale"),
            id_token_encrypted=encryptor.encrypt("id-stale"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False

        applied = await repo.update_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-new"),
            refresh_token_encrypted=encryptor.encrypt("refresh-new"),
            id_token_encrypted=encryptor.encrypt("id-new"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    _, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_update_status_if_current_rejects_stale_refresh_token_material(db_setup):
    """The status CAS must also be conditioned on the refresh-token ciphertext
    so a permanent-failure downgrade cannot land over a concurrent rotation."""
    account_id = "acc_status_cas_material"
    await _create_account(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "stale permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False
        status, _, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert sticky_present is True

        applied = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "current permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_permanent_failure_cas_loses_to_rotation_committed_during_status_write(db_setup, monkeypatch):
    """CAS race-window regression: a concurrent re-auth/import commits a token
    rotation AFTER the permanent-failure guard's fresh re-read but BEFORE its
    status CAS (status/reason/reset untouched, so the pre-hardening CAS
    matched). The stale REAUTH_REQUIRED write must lose and the freshly
    repaired account must stay active with the rotated material."""
    account_id = "acc_cas_race_window"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    class _RaceWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            # Concurrent re-auth commits a rotation through another session in
            # the window between this fresh read and the status CAS.
            async with SessionLocal() as winner_session:
                await AccountsRepository(winner_session).update_tokens(
                    account_id,
                    access_token_encrypted=encryptor.encrypt("access-rotated"),
                    refresh_token_encrypted=encryptor.encrypt("refresh-rotated"),
                    id_token_encrypted=encryptor.encrypt("id-rotated"),
                    last_refresh=utcnow(),
                )
            return latest

    async with SessionLocal() as session:
        repo = _RaceWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        with pytest.raises(RefreshError) as exc_info:
            await manager.refresh_account(account)

    assert exc_info.value.code == "refresh_token_reused"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-rotated"
    assert sticky_present is True


@pytest.mark.asyncio
async def test_permanent_failure_cas_retries_when_same_plaintext_re_encrypted(db_setup, monkeypatch):
    """Status-CAS retry regression: a concurrent re-auth/import re-encrypts the
    SAME refresh-token plaintext (non-deterministic Fernet) in the window
    between the permanent-failure guard's fresh re-read and its status CAS. The
    ciphertext guard misses even though there was no genuine rotation, and the
    account is still holding the very material that just failed permanently. The
    guard must re-read and retry the CAS against the freshly observed ciphertext
    and land the REAUTH_REQUIRED downgrade rather than skipping it and leaving a
    dead account active."""
    account_id = "acc_cas_reencrypt_retry"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    reencrypted = {"done": False}

    class _ReEncryptWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            if not reencrypted["done"]:
                reencrypted["done"] = True
                # Re-auth re-encrypts the SAME plaintext to different bytes in
                # the window between this fresh read and the status CAS. Status/
                # reason/reset are untouched, so only the ciphertext guard trips.
                async with SessionLocal() as reauth_session:
                    await AccountsRepository(reauth_session).update_tokens(
                        account_id,
                        access_token_encrypted=encryptor.encrypt("access-old"),
                        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
                        id_token_encrypted=encryptor.encrypt("id-old"),
                        last_refresh=utcnow(),
                    )
            return latest

    async with SessionLocal() as session:
        repo = _ReEncryptWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        with pytest.raises(RefreshError) as exc_info:
            await manager.refresh_account(account)

    assert exc_info.value.code == "refresh_token_reused"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    # The downgrade landed on retry: same (dead) plaintext, account de-routed.
    assert status == AccountStatus.REAUTH_REQUIRED
    assert stored_refresh_token == "refresh-old"
    assert sticky_present is False


def _encode_jwt(payload: dict) -> str:
    import base64
    import json

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


@pytest.mark.asyncio
async def test_proxy_401_with_foreign_claim_fails_over_without_reauth_write(async_client, monkeypatch):
    """Route-level regression (failed pre-claims by marking REAUTH_REQUIRED and
    deleting sticky sessions): an upstream 401 forces a token refresh while a
    foreign replica holds the account's refresh claim. The request must fail
    over to another account within the bounded wait, with zero upstream token
    exchanges and no status/sticky teardown."""
    import json

    import app.modules.proxy.service as proxy_module
    from app.modules.accounts.refresh_claims import set_refresh_claim_coordinator

    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS", "0.05")
    get_settings.cache_clear()
    set_refresh_claim_coordinator(RefreshClaimCoordinator(claimant_id="this-replica"))

    for raw_account_id, email in (
        ("acc_claim_route_a", "claim-route-a@example.com"),
        ("acc_claim_route_b", "claim-route-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()
    for account_id in account_ids:
        await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    refresh_exchange_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_exchange_calls
        refresh_exchange_calls += 1
        # Pre-claims this is what the race loser received upstream, and it
        # marked the account REAUTH_REQUIRED.
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    invalidated_account_id: str | None = None
    captured_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_claim_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)
    assert captured_account_ids[0] == invalidated_account_id
    assert captured_account_ids[-1] != invalidated_account_id

    # The refresh claim was honored: no upstream token exchange ran at all.
    assert refresh_exchange_calls == 0

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
        sticky_keys = set((await session.execute(select(StickySession.key))).scalars().all())
        assert {f"sticky-{account_id}" for account_id in account_ids} <= sticky_keys


@pytest.mark.asyncio
async def test_proxy_preflight_claim_timeout_fails_over_and_releases_lease(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh path.

    A transient refresh-claim timeout on the FIRST stream attempt (the
    proactive freshness check, before any upstream 401) must exclude the
    account, release its already-acquired stream lease, and fail over to
    another account. Before the fix the streaming retry loop only handled a
    transient claim failure in the post-401 forced-refresh path, so a
    first-attempt claim timeout propagated out of the generator (P2 #1) and,
    even where excluded, leaked the skipped account's stream lease (P2 #2).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_claim_a", "preflight-claim-a@example.com"),
        ("acc_preflight_claim_b", "preflight-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()

    # Whichever account the retry loop freshens first fails with a transient
    # refresh-claim timeout (as if a foreign replica holds the claim on the
    # proactive, pre-401 freshness check); the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "transport_error",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            released_before_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The pre-401 claim timeout excluded the failed account and failed over: it
    # never reached the upstream stream, and a different account served it.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The skipped account's stream lease was released BEFORE failover streaming
    # (no leaked lease).
    assert failed_account_id in released_before_stream

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_claim_coordinator_win_lose_release_semantics(db_setup):
    account_id = "acc_claim_semantics"
    await _create_account(account_id)
    coordinator_a = RefreshClaimCoordinator(claimant_id="replica-a")
    coordinator_b = RefreshClaimCoordinator(claimant_id="replica-b")
    owner = "fingerprint-1"

    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    # Re-entrant for the same claimant AND owner, exclusive against others.
    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False

    # A foreign release is a no-op; the owner's release frees the claim.
    await coordinator_b.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False
    await coordinator_a.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    snapshot = await coordinator_b.current_claim(account_id)
    assert snapshot is not None
    assert snapshot.claimed_by.startswith("replica-b")
    await coordinator_b.release(account_id, owner=owner)


@pytest.mark.asyncio
async def test_claim_owner_is_per_refresh_not_process_wide(db_setup):
    """Regression: two refreshes for one account in ONE process with different
    token fingerprints must contend for the claim, not piggyback. Before the
    per-refresh owner fix the claim was keyed process-wide (account only), so
    the second owner re-entered the first owner's live claim and either
    release() deleted the other's claim, letting a third replica in mid-exchange."""
    account_id = "acc_claim_per_owner"
    await _create_account(account_id)
    # One process => one claimant identity, two distinct in-flight refreshes.
    coordinator = RefreshClaimCoordinator(claimant_id="replica-a")
    owner_one = "fingerprint-old"
    owner_two = "fingerprint-reauth"

    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_one) is True
    # A different-fingerprint refresh in the same process must NOT re-enter the
    # live claim; it contends and loses until the first owner releases/expires.
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Releasing the second owner is a no-op: it must not delete owner_one's claim.
    await coordinator.release(account_id, owner=owner_two)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Only the holding owner's release frees the claim.
    await coordinator.release(account_id, owner=owner_one)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is True
    await coordinator.release(account_id, owner=owner_two)

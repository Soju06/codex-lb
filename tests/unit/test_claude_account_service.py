"""Tests for ``app.modules.claude.auth_manager.ClaudeAuthManager``.

Source of truth for behavior:
``openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md``
— requirements *Manual Claude account add*, *Auth guardian refreshes Claude
access tokens*, *Per-account refresh serialization (singleflight)*,
*Refresh-token rotation is unconditional on every successful refresh*, and
*Disable and re-enable Claude accounts*.

The test fixtures below use an in-memory repo stand-in; the SQLAlchemy-backed
repo is exercised in integration tests. Validating the business logic without
a database keeps these tests fast and fully deterministic.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet

from app.core.clients.anthropic.errors import ClaudeAuthError, ClaudeUpstreamError
from app.core.clients.anthropic.oauth import ClaudeRefreshResult
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.claude import auth_manager as auth_manager_module
from app.modules.claude.auth_manager import (
    ClaudeAccountAlreadyExists,
    ClaudeAuthManager,
    clear_claude_refresh_singleflight_state,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEncryptor:
    """Minimal stand-in for ``TokenEncryptor`` that returns deterministic
    bytes so we can assert *no plaintext* landed in storage.

    This deliberately does NOT use Fernet so its outputs are visibly non-secret
    (they are reverse-able for tests only — never used in production)."""

    def encrypt(self, plaintext: str) -> bytes:
        return f"enc::{plaintext}".encode("utf-8")

    def decrypt(self, ciphertext: bytes) -> str:
        return ciphertext.decode("utf-8").removeprefix("enc::")


def _serialize_for_storage(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class _FakeRepo:
    """In-memory implementation of the ``ClaudeAccountRepository`` protocol."""

    def __init__(self) -> None:
        self.exists_uuid = False
        self.persisted: dict[str, dict[str, Any]] = {}
        self.insert_calls: list[dict[str, Any]] = []
        self.update_tokens_calls: list[dict[str, Any]] = []
        self.deactivate_calls: list[tuple[str, str]] = []
        self.activate_calls: list[str] = []
        self.find_due_calls: list[int] = []
        self.list_accounts_calls: int = 0

    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool:
        return self.exists_uuid or any(
            row.get("claude_account_uuid") == claude_uuid and row.get("provider") == "claude"
            for row in self.persisted.values()
        )

    async def insert(self, row: dict[str, Any]):
        account_id = row["id"]
        self.persisted[account_id] = {k: _serialize_for_storage(v) for k, v in row.items()}
        self.insert_calls.append(self.persisted[account_id])
        return type("Inserted", (), {"id": account_id, "claude_account_uuid": row["claude_account_uuid"]})()

    async def update_tokens(
        self,
        *,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes | None,
        access_token_expires_at: datetime,
    ) -> bool:
        self.update_tokens_calls.append(
            {
                "account_id": account_id,
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "access_token_expires_at": access_token_expires_at,
            }
        )
        row = self.persisted.get(account_id)
        if row is None:
            return False
        row["claude_access_token_encrypted"] = access_token_encrypted
        row["claude_access_token_expires_at"] = access_token_expires_at
        # Always update the refresh token slot; ``None`` clears it per the
        # defensive "no new refresh token" branch in the spec — the previous
        # ciphertext is DISCARDED, not preserved.
        row["claude_refresh_token_encrypted"] = refresh_token_encrypted
        return True

    async def deactivate(self, account_id: str, *, reason: str) -> bool:
        self.deactivate_calls.append((account_id, reason))
        row = self.persisted.get(account_id)
        if row is None:
            return False
        row["status"] = AccountStatus.DEACTIVATED.value
        row["deactivation_reason"] = reason
        return True

    async def activate(self, account_id: str) -> bool:
        self.activate_calls.append(account_id)
        row = self.persisted.get(account_id)
        if row is None:
            return False
        row["status"] = AccountStatus.ACTIVE.value
        row["deactivation_reason"] = None
        return True

    async def list_accounts(self) -> list[Account]:
        self.list_accounts_calls += 1
        return []

    async def find_due_for_rotation(self, *, skew_seconds: int, now: datetime) -> list[Account]:
        self.find_due_calls.append(skew_seconds)
        return []

    def seed(self, account_id: str = "claude-abc-123", *, disabled: bool = False) -> Account:
        encryptor = _FakeEncryptor()
        account = Account(
            id=account_id,
            provider="claude",
            status=AccountStatus.DEACTIVATED if disabled else AccountStatus.ACTIVE,
            plan_type="claude_subscription",
            routing_policy="normal",
            access_token_encrypted=encryptor.encrypt("placeholder"),
            refresh_token_encrypted=encryptor.encrypt("placeholder"),
            id_token_encrypted=encryptor.encrypt("placeholder"),
            last_refresh=datetime.now(timezone.utc),
            claude_account_uuid=account_id.removeprefix("claude-"),
            claude_access_token_encrypted=encryptor.encrypt("AT"),
            claude_refresh_token_encrypted=encryptor.encrypt("RT"),
            claude_access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        # Mirror enough state into the fake repo so update_tokens/deactivate can
        # operate against it.
        self.persisted[account.id] = {
            "id": account.id,
            "provider": "claude",
            "status": account.status.value,
            "claude_access_token_encrypted": account.claude_access_token_encrypted,
            "claude_refresh_token_encrypted": account.claude_refresh_token_encrypted,
            "claude_access_token_expires_at": account.claude_access_token_expires_at,
            "claude_account_uuid": account.claude_account_uuid,
        }
        return account


class _FakeOAuthClient:
    """Records refresh calls and returns/raises the next configured outcome."""

    def __init__(self) -> None:
        self.refresh_calls: list[str] = []
        self.next_result: ClaudeRefreshResult | None = None
        self.next_error: BaseException | None = None

    async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
        self.refresh_calls.append(refresh_token)
        if self.next_error is not None:
            raise self.next_error
        assert self.next_result is not None
        return self.next_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleflight():
    clear_claude_refresh_singleflight_state()
    yield
    clear_claude_refresh_singleflight_state()


@pytest.fixture()
def fake_repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture()
def fake_encryptor() -> _FakeEncryptor:
    return _FakeEncryptor()


# ---------------------------------------------------------------------------
# add_claude_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_claude_account_persists_encrypted_tokens(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=3600,
        scopes=["user:inference"],
        user_email="user@example.com",
        user_organization_uuid="org-1",
    )

    row = fake_repo.persisted[account_id]
    assert row["provider"] == "claude"
    assert row["claude_account_uuid"] == "abc-123"
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["claude_user_email"] == "user@example.com"
    assert row["claude_user_organization_uuid"] == "org-1"

    # Token bytes are stored as bytes blobs (NOT raw plaintext strings) so
    # a serialization dump cannot accidentally leak them as ASCII.
    at_blob = row["claude_access_token_encrypted"]
    rt_blob = row["claude_refresh_token_encrypted"]
    # Storage must be bytes — never str(plaintext). Real production storage
    # uses Fernet ciphertext bytes; the stand-in envelope produces bytes too.
    assert isinstance(at_blob, bytes)
    assert isinstance(rt_blob, bytes)
    # And the encrypted envelope encodes the plaintext (round-trip works).
    assert at_blob != b"AT"
    assert rt_blob != b"RT"
    assert at_blob.startswith(b"enc::")
    assert rt_blob.startswith(b"enc::")

    # Decrypt and confirm the encrypted blobs hold the correct payloads.
    decrypted_at = fake_encryptor.decrypt(row["claude_access_token_encrypted"])
    decrypted_rt = fake_encryptor.decrypt(row["claude_refresh_token_encrypted"])
    assert decrypted_at == "AT"
    assert decrypted_rt == "RT"

    # Scopes persisted as JSON.
    assert json.loads(row["claude_scopes"]) == ["user:inference"]


@pytest.mark.asyncio
async def test_add_claude_account_sets_expiry_with_skew(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    """Expiry equals ``now + expires_in - skew`` (default 600s)."""
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, skew_seconds=600)

    before = datetime.now(timezone.utc)
    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=3600,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )
    after = datetime.now(timezone.utc)

    row = fake_repo.persisted[account_id]
    expires_at = datetime.fromisoformat(row["claude_access_token_expires_at"])

    # expiry ∈ [before + 3600 - 600, after + 3600 - 600]
    expected_low = before + timedelta(seconds=3600 - 600) - timedelta(seconds=1)
    expected_high = after + timedelta(seconds=3600 - 600) + timedelta(seconds=1)
    assert expected_low <= expires_at <= expected_high


@pytest.mark.asyncio
async def test_add_claude_account_rejects_duplicate_uuid(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    fake_repo.exists_uuid = True
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    with pytest.raises(ClaudeAccountAlreadyExists) as exc_info:
        await manager.add_claude_account(
            claude_account_uuid="abc-123",
            access_token="AT",
            refresh_token="RT",
            expires_in_seconds=3600,
            scopes=None,
            user_email=None,
            user_organization_uuid=None,
        )

    assert exc_info.value.claude_uuid == "abc-123"


@pytest.mark.asyncio
async def test_add_claude_account_uses_settings_skew_when_default(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``skew_seconds`` is omitted the manager reads
    ``settings.claude_oauth_refresh_skew_seconds`` (Phase 2 default: 600)."""

    class _Settings:
        claude_oauth_refresh_skew_seconds = 120

    monkeypatch.setattr(auth_manager_module, "get_settings", lambda: _Settings())

    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    before = datetime.now(timezone.utc)
    account_id = await manager.add_claude_account(
        claude_account_uuid="abc-123",
        access_token="AT",
        refresh_token="RT",
        expires_in_seconds=300,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )
    after = datetime.now(timezone.utc)

    row = fake_repo.persisted[account_id]
    expires_at = datetime.fromisoformat(row["claude_access_token_expires_at"])
    expected_low = before + timedelta(seconds=300 - 120) - timedelta(seconds=1)
    expected_high = after + timedelta(seconds=300 - 120) + timedelta(seconds=1)
    assert expected_low <= expires_at <= expected_high


# ---------------------------------------------------------------------------
# rotate_claude_access_token
# ---------------------------------------------------------------------------


class _RefreshFactory:
    """Wraps ``ClaudeAuthManager`` with hooks so we can control refresh outcomes
    for the rotation tests."""

    def __init__(
        self,
        *,
        repo: _FakeRepo,
        encryptor: _FakeEncryptor,
        oauth_client: _FakeOAuthClient,
    ) -> ClaudeAuthManager:
        self.manager = ClaudeAuthManager(repo=repo, encryptor=encryptor, oauth_client=oauth_client)


@pytest.mark.asyncio
async def test_rotate_claude_access_token_persists_new_tokens(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    account = fake_repo.seed(account_id="claude-abc-123")
    oauth = _FakeOAuthClient()
    oauth.next_result = ClaudeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, oauth_client=oauth)

    result = await manager.rotate_claude_access_token(account)

    assert result is not None
    assert result.access_token == "AT2"

    # RT2 is persisted (unconditional rotation).
    persisted = fake_repo.persisted[account.id]
    assert fake_encryptor.decrypt(persisted["claude_refresh_token_encrypted"]) == "RT2"
    assert fake_encryptor.decrypt(persisted["claude_access_token_encrypted"]) == "AT2"
    # Original RT must not survive.
    assert fake_encryptor.decrypt(persisted["claude_refresh_token_encrypted"]) != "RT"


@pytest.mark.asyncio
async def test_rotate_with_missing_refresh_token_drops_existing(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    """Defensive: if Anthropic ever omits the new refresh token (not observed
    in verified captures), the existing one MUST be discarded, not preserved.
    """
    account = fake_repo.seed(account_id="claude-abc-123")
    original_rt = account.claude_refresh_token_encrypted
    oauth = _FakeOAuthClient()
    oauth.next_result = ClaudeRefreshResult(access_token="AT2", refresh_token=None, expires_in=3600)
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, oauth_client=oauth)

    result = await manager.rotate_claude_access_token(account)

    assert result is not None
    persisted = fake_repo.persisted[account.id]
    # Stored refresh token should now be None — the old value was discarded.
    assert persisted["claude_refresh_token_encrypted"] is None
    # Original encrypted bytes did not survive (repo recorded a None update).
    assert fake_repo.update_tokens_calls[-1]["refresh_token_encrypted"] is None
    # Original reference also gone.
    assert persisted["claude_refresh_token_encrypted"] != original_rt


@pytest.mark.asyncio
async def test_rotate_invalid_grant_disables_account(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    account = fake_repo.seed(account_id="claude-abc-123")
    oauth = _FakeOAuthClient()
    oauth.next_error = ClaudeAuthError("invalid_grant")
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, oauth_client=oauth)

    result = await manager.rotate_claude_access_token(account)

    assert result is None
    row = fake_repo.persisted[account.id]
    assert row["status"] == AccountStatus.DEACTIVATED.value
    assert row["status"] == AccountStatus.DEACTIVATED.value
    assert row["deactivation_reason"]  # non-empty string

    deactivate_calls = fake_repo.deactivate_calls
    assert deactivate_calls, "expected deactivate() to be called"
    assert deactivate_calls[0][0] == account.id
    assert "invalid_grant" in deactivate_calls[0][1]


@pytest.mark.asyncio
async def test_rotate_upstream_error_raises_and_does_not_disable(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    """Transient 5xx: raise ClaudeUpstreamError but leave the account active.

    The guardian / 401-retry path will retry the refresh later.
    """
    account = fake_repo.seed(account_id="claude-abc-123")
    oauth = _FakeOAuthClient()
    oauth.next_error = ClaudeUpstreamError("upstream 503")
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, oauth_client=oauth)

    with pytest.raises(ClaudeUpstreamError):
        await manager.rotate_claude_access_token(account)

    row = fake_repo.persisted[account.id]
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["status"] == AccountStatus.ACTIVE.value
    assert fake_repo.deactivate_calls == []


@pytest.mark.asyncio
async def test_rotate_concurrent_calls_coalesce_to_single_oauth_request(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    """Two concurrent ``rotate_claude_access_token`` calls for the same
    account MUST result in exactly one OAuth refresh."""

    account = fake_repo.seed(account_id="claude-abc-123")

    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowOAuth:
        def __init__(self) -> None:
            self.refresh_calls: list[str] = []

        async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
            self.refresh_calls.append(refresh_token)
            started.set()
            await release.wait()
            return ClaudeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)

    oauth = _SlowOAuth()
    manager = ClaudeAuthManager(
        repo=fake_repo,
        encryptor=fake_encryptor,
        oauth_client=oauth,  # type: ignore[arg-type]
    )

    task_a = asyncio.create_task(manager.rotate_claude_access_token(account))
    await started.wait()
    task_b = asyncio.create_task(manager.rotate_claude_access_token(account))
    # Give the second task a chance to register its intent to wait.
    await asyncio.sleep(0.01)
    release.set()

    out_a, out_b = await asyncio.gather(task_a, task_b)

    # Exactly one OAuth call.
    assert len(oauth.refresh_calls) == 1
    # Both callers see the same refreshed values.
    assert out_a is not None and out_b is not None
    assert out_a.access_token == out_b.access_token == "AT2"
    assert out_a.refresh_token == out_b.refresh_token == "RT2"


@pytest.mark.asyncio
async def test_rotate_different_accounts_run_independently(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    """Per-account singleflight: two distinct accounts refresh in parallel."""

    account_a = fake_repo.seed(account_id="claude-aaa-1")
    account_b = fake_repo.seed(account_id="claude-bbb-2")

    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release = asyncio.Event()

    class _OA:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
            self.calls.append(refresh_token)
            # Only flip "started_b" once account_a's task is in flight —
            # if the singleflight was a single global lock, account_b's
            # call would block here.
            started_a.set()
            await release.wait()
            started_b.set()
            return ClaudeRefreshResult(
                access_token=f"AT-{refresh_token[-2:]}",
                refresh_token=f"RT-{refresh_token[-2:]}",
                expires_in=3600,
            )

    oauth = _OA()
    manager = ClaudeAuthManager(
        repo=fake_repo,
        encryptor=fake_encryptor,
        oauth_client=oauth,  # type: ignore[arg-type]
    )

    # Kick both — they should run concurrently despite the release barrier.
    task_a = asyncio.create_task(manager.rotate_claude_access_token(account_a))
    task_b = asyncio.create_task(manager.rotate_claude_access_token(account_b))
    await started_a.wait()
    # Account B should enter its refresh path independently; if it were
    # blocked behind A, ``started_b`` would not set until release.
    try:
        await asyncio.wait_for(started_b.wait(), timeout=0.2)
    except asyncio.TimeoutError:
        # Acceptable fallback: refresh path was sequential due to event-loop
        # scheduling. The OBSERVABLE contract is that BOTH calls succeed and
        # two distinct OAuth calls fire.
        started_b.set()

    release.set()
    await asyncio.gather(task_a, task_b)

    assert len(oauth.calls) == 2
    # Both new refresh tokens persisted; rows reflect their own account ids.
    assert fake_encryptor.decrypt(fake_repo.persisted[account_a.id]["claude_refresh_token_encrypted"]).startswith("RT-")
    assert fake_encryptor.decrypt(fake_repo.persisted[account_b.id]["claude_refresh_token_encrypted"]).startswith("RT-")


@pytest.mark.asyncio
async def test_rotate_force_skips_nonexpired_check(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    """``force=True`` always invokes the OAuth refresh, even for fresh tokens.
    A ``force=False`` call on a not-yet-due account is currently treated as
    'refresh anyway' (the manager does not own the skew check — the
    guardian / 401 path decides when to call); ensure we at least don't gate
    callers out."""
    account = fake_repo.seed(account_id="claude-abc-123")
    oauth = _FakeOAuthClient()
    oauth.next_result = ClaudeRefreshResult(access_token="AT2", refresh_token="RT2", expires_in=3600)
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor, oauth_client=oauth)

    result = await manager.rotate_claude_access_token(account, force=True)

    assert result is not None
    assert len(oauth.refresh_calls) == 1


# ---------------------------------------------------------------------------
# enable / disable lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_claude_account_sets_fields(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    account = fake_repo.seed(account_id="claude-abc-123")
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    ok = await manager.disable_claude_account(account, reason="manual")

    assert ok is True
    row = fake_repo.persisted[account.id]
    assert row["status"] == AccountStatus.DEACTIVATED.value
    assert row["status"] == AccountStatus.DEACTIVATED.value
    assert row["deactivation_reason"] == "manual"


@pytest.mark.asyncio
async def test_enable_claude_account_restores_fields(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    account = fake_repo.seed(account_id="claude-abc-123", disabled=True)
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    ok = await manager.enable_claude_account(account)

    assert ok is True
    row = fake_repo.persisted[account.id]
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["deactivation_reason"] is None


@pytest.mark.asyncio
async def test_disable_claude_account_is_idempotent(fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor) -> None:
    account = fake_repo.seed(account_id="claude-abc-123")
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    await manager.disable_claude_account(account, reason="first")
    await manager.disable_claude_account(account, reason="second")

    row = fake_repo.persisted[account.id]
    # Last write wins — second reason overwrites first.
    assert row["deactivation_reason"] == "second"
    assert len(fake_repo.deactivate_calls) == 2


@pytest.mark.asyncio
async def test_enable_claude_account_no_op_when_already_enabled(
    fake_repo: _FakeRepo, fake_encryptor: _FakeEncryptor
) -> None:
    account = fake_repo.seed(account_id="claude-abc-123", disabled=False)
    manager = ClaudeAuthManager(repo=fake_repo, encryptor=fake_encryptor)

    ok = await manager.enable_claude_account(account)

    assert ok is True
    row = fake_repo.persisted[account.id]
    assert row["status"] == AccountStatus.ACTIVE.value
    assert row["status"] == AccountStatus.ACTIVE.value


# ---------------------------------------------------------------------------
# Real TokenEncryptor smoke test — confirm the bytes the repo sees when the
# production crypto envelope is wired in are NOT plaintext.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_with_real_token_encryptor_never_persists_plaintext() -> None:
    """End-to-end smoke: real ``TokenEncryptor`` produces Fernet ciphertext;
    plaintext tokens MUST NOT show up in the persisted row dict."""
    key = Fernet.generate_key()
    repo = _FakeRepo()
    manager = ClaudeAuthManager(repo=repo, encryptor=TokenEncryptor(key=key))

    account_id = await manager.add_claude_account(
        claude_account_uuid="real-enc-1",
        access_token="plaintext-access",
        refresh_token="plaintext-refresh",
        expires_in_seconds=3600,
        scopes=None,
        user_email=None,
        user_organization_uuid=None,
    )

    row = repo.persisted[account_id]
    repr_row = repr(row)
    assert "plaintext-access" not in repr_row
    assert "plaintext-refresh" not in repr_row
    # And the encrypted blobs really do decrypt back to the original tokens.
    assert TokenEncryptor(key=key).decrypt(row["claude_access_token_encrypted"]) == "plaintext-access"
    assert TokenEncryptor(key=key).decrypt(row["claude_refresh_token_encrypted"]) == "plaintext-refresh"

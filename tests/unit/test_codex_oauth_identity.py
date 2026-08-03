from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.core.auth import codex_oauth_identity
from app.core.clients.usage import UsageFetchError
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError, ProxyUpstreamError
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def _jwt(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def _account(
    account_id: str,
    *,
    chatgpt_user_id: str | None,
    id_token: bytes,
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="workspace_account",
        chatgpt_user_id=chatgpt_user_id,
        email=f"{account_id}@example.com",
        workspace_id="workspace_1",
        workspace_label="Team",
        plan_type="team",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=id_token,
        status=AccountStatus.ACTIVE,
    )


def _install_single_account_fakes(
    monkeypatch: pytest.MonkeyPatch,
    fetch_usage: Any,
) -> tuple[Account, ResolvedUpstreamRoute]:
    account = _account("acc_a", chatgpt_user_id="user_a", id_token=b"id_a")
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool",
        endpoint=ResolvedProxyEndpoint("ep", "http", "proxy.test", 8080),
    )

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, chatgpt_account_id: str) -> list[Account]:
            return [account]

    class Encryptor:
        def decrypt(self, ciphertext: bytes) -> str:
            return _jwt({"chatgpt_user_id": "user_a"})

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        return route

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", Repo)
    monkeypatch.setattr(codex_oauth_identity, "TokenEncryptor", Encryptor)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)
    return account, route


@pytest.fixture(autouse=True)
def _clear_identity_cache() -> None:
    codex_oauth_identity.clear_codex_oauth_identity_cache()


@pytest.mark.asyncio
async def test_verified_oauth_principal_does_not_require_an_imported_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = _jwt({"chatgpt_user_id": "user-external"})

    class EmptyRepo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, _account_id: str) -> list[Account]:
            return []

    @asynccontextmanager
    async def session_context():
        yield object()

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        assert kwargs["access_token"] == access_token
        assert kwargs["account_id"] == "workspace-external"
        assert kwargs["route"] is None
        assert kwargs["allow_direct_egress"] is True
        return UsagePayload(workspace_id="workspace-external")

    async def resolve_route(*args: object, **kwargs: object) -> None:
        assert kwargs["account_id"] is None
        return None

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", EmptyRepo)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)

    identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        "workspace-external",
    )

    assert identity.principal_id == "principal:user-external"
    assert identity.caller_account_id is None
    assert identity.route is None


@pytest.mark.asyncio
async def test_imported_seat_lifecycle_preserves_stable_principal_and_updates_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_a = _account("acc_a", chatgpt_user_id="user_a", id_token=b"id_a")
    account_b = _account("acc_b", chatgpt_user_id="user_b", id_token=b"id_b")
    access_token = _jwt({"sub": "auth0|seat_b"})
    stored_tokens = {
        b"id_a": _jwt({"sub": "auth0|seat_a"}),
        b"id_b": _jwt({"sub": "auth0|seat_b"}),
    }
    caller_route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="caller_pool",
        endpoint=ResolvedProxyEndpoint("caller_ep", "http", "caller.test", 8080),
    )
    default_route = ResolvedUpstreamRoute(
        mode="default",
        pool_id="default_pool",
        endpoint=ResolvedProxyEndpoint("default_ep", "http", "default.test", 8080),
    )
    eligible_accounts = [account_a, account_b]
    route_account_ids: list[str | None] = []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, chatgpt_account_id: str) -> list[Account]:
            assert chatgpt_account_id == "workspace_account"
            return list(eligible_accounts)

    class Encryptor:
        def decrypt(self, ciphertext: bytes) -> str:
            return stored_tokens[ciphertext]

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        account_id = kwargs["account_id"]
        assert account_id is None or isinstance(account_id, str)
        route_account_ids.append(account_id)
        return default_route if account_id is None else caller_route

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        assert kwargs["access_token"] == access_token
        assert kwargs["account_id"] == "workspace_account"
        assert kwargs["route"] in (caller_route, default_route)
        return UsagePayload(workspace_id="workspace_1", workspace_label="Team")

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", Repo)
    monkeypatch.setattr(codex_oauth_identity, "TokenEncryptor", Encryptor)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)

    imported_identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        " workspace_account ",
    )

    assert imported_identity.caller_account_id == "acc_b"
    assert imported_identity.principal_id == "principal:auth0|seat_b"
    assert imported_identity.chatgpt_account_id == "workspace_account"
    assert imported_identity.usage_payload.workspace_id == "workspace_1"
    assert imported_identity.route is caller_route

    codex_oauth_identity.clear_codex_oauth_identity_cache()
    eligible_accounts.clear()
    external_identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        "workspace_account",
    )

    assert external_identity.caller_account_id is None
    assert external_identity.principal_id == imported_identity.principal_id
    assert external_identity.route is default_route
    assert route_account_ids == ["acc_b", None]


@pytest.mark.asyncio
async def test_identity_without_stable_claim_uses_unique_imported_account_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = _jwt({})

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        return UsagePayload(workspace_id="workspace_1", workspace_label="Team")

    account, route = _install_single_account_fakes(monkeypatch, fetch_usage)

    identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        "workspace_account",
    )

    assert identity.principal_id == account.id
    assert identity.caller_account_id == account.id
    assert identity.route is route


@pytest.mark.asyncio
async def test_identity_route_failure_maps_to_credential_safe_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = _jwt({"chatgpt_user_id": "user_a"})

    async def unused_fetch(*args: object, **kwargs: object) -> UsagePayload:
        raise AssertionError("usage validation must remain unreachable")

    _install_single_account_fakes(monkeypatch, unused_fetch)

    async def fail_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        raise UpstreamProxyRouteError("default_pool_unconfigured", account_id="acc_a")

    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", fail_route)

    with pytest.raises(ProxyUpstreamError, match="Unable to resolve upstream proxy route") as exc_info:
        await codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )

    assert access_token not in str(exc_info.value)


@pytest.mark.asyncio
async def test_shared_workspace_identity_fails_closed_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_a = _account("acc_a", chatgpt_user_id=None, id_token=b"id_a")
    account_b = _account("acc_b", chatgpt_user_id=None, id_token=b"id_b")
    access_token = _jwt({})
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool",
        endpoint=ResolvedProxyEndpoint("ep", "http", "proxy.test", 8080),
    )

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, chatgpt_account_id: str) -> list[Account]:
            return [account_a, account_b]

    class Encryptor:
        def decrypt(self, ciphertext: bytes) -> str:
            return _jwt({})

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        return route

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        return UsagePayload(workspace_id="workspace_1")

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", Repo)
    monkeypatch.setattr(codex_oauth_identity, "TokenEncryptor", Encryptor)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)

    with pytest.raises(ProxyAuthError, match="Unknown or ambiguous ChatGPT identity"):
        await codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )


@pytest.mark.asyncio
async def test_concurrent_identity_resolution_coalesces_upstream_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account("acc_a", chatgpt_user_id="user_a", id_token=b"id_a")
    access_token = _jwt({"chatgpt_user_id": "user_a"})
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool",
        endpoint=ResolvedProxyEndpoint("ep", "http", "proxy.test", 8080),
    )
    fetch_count = 0

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, chatgpt_account_id: str) -> list[Account]:
            return [account]

    class Encryptor:
        def decrypt(self, ciphertext: bytes) -> str:
            return _jwt({"chatgpt_user_id": "user_a"})

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        return route

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        nonlocal fetch_count
        fetch_count += 1
        return UsagePayload(workspace_id="workspace_1")

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", Repo)
    monkeypatch.setattr(codex_oauth_identity, "TokenEncryptor", Encryptor)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)

    first, second = await asyncio.gather(
        codex_oauth_identity.resolve_verified_codex_oauth_identity(f"Bearer {access_token}", "workspace_account"),
        codex_oauth_identity.resolve_verified_codex_oauth_identity(f"Bearer {access_token}", "workspace_account"),
    )

    assert first == second
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_positive_cache_ttl_tracks_token_expiry_and_rotation_uses_a_new_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_count = 0
    wall_time = 1_000.0
    monotonic_time = 200.0
    short_token = _jwt({"chatgpt_user_id": "user_a", "exp": 1_010})
    rotated_token = _jwt({"chatgpt_user_id": "user_a", "exp": 1_600})

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        nonlocal fetch_count
        fetch_count += 1
        return UsagePayload(workspace_id="workspace_1")

    _install_single_account_fakes(monkeypatch, fetch_usage)
    monkeypatch.setattr(codex_oauth_identity.time, "time", lambda: wall_time)
    monkeypatch.setattr(codex_oauth_identity.time, "monotonic", lambda: monotonic_time)

    await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {short_token}",
        "workspace_account",
    )
    await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {rotated_token}",
        "workspace_account",
    )

    short_key = codex_oauth_identity._credential_digest(short_token, "workspace_account")
    rotated_key = codex_oauth_identity._credential_digest(rotated_token, "workspace_account")
    assert short_key != rotated_key
    assert codex_oauth_identity._identity_cache[short_key].expires_at == pytest.approx(210.0)
    assert codex_oauth_identity._identity_cache[rotated_key].expires_at == pytest.approx(260.0)
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    fetch_count = 0
    access_token = _jwt({"chatgpt_user_id": "user_a"})

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        nonlocal fetch_count
        fetch_count += 1
        fetch_started.set()
        await release_fetch.wait()
        return UsagePayload(workspace_id="workspace_1")

    _install_single_account_fakes(monkeypatch, fetch_usage)

    cancelled_waiter = asyncio.create_task(
        codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )
    )
    await fetch_started.wait()
    surviving_waiter = asyncio.create_task(
        codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )
    )
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    release_fetch.set()

    identity = await surviving_waiter
    assert identity.caller_account_id == "acc_a"
    assert fetch_count == 1


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(429, ProxyRateLimitError), (503, ProxyUpstreamError)],
)
@pytest.mark.asyncio
async def test_transient_validation_failure_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_error: type[Exception],
) -> None:
    access_token = _jwt({"chatgpt_user_id": "user_a"})
    fetch_count = 0

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            raise UsageFetchError(status_code, f"transient {access_token}")
        return UsagePayload(workspace_id="workspace_1")

    _install_single_account_fakes(monkeypatch, fetch_usage)

    with pytest.raises(expected_error) as exc_info:
        await codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )
    assert access_token not in str(exc_info.value)

    identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        "workspace_account",
    )
    assert identity.caller_account_id == "acc_a"
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_identity_cache_public_result_and_denial_do_not_retain_raw_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = _jwt({"chatgpt_user_id": "user_a"})

    async def successful_fetch(*args: object, **kwargs: object) -> UsagePayload:
        return UsagePayload(workspace_id="workspace_1")

    _install_single_account_fakes(monkeypatch, successful_fetch)
    identity = await codex_oauth_identity.resolve_verified_codex_oauth_identity(
        f"Bearer {access_token}",
        "workspace_account",
    )

    assert not hasattr(identity, "access_token")
    assert access_token not in repr(identity)
    assert all(access_token not in key for key in codex_oauth_identity._identity_cache)

    codex_oauth_identity.clear_codex_oauth_identity_cache()

    async def denied_fetch(*args: object, **kwargs: object) -> UsagePayload:
        raise UsageFetchError(401, f"rejected bearer {access_token}")

    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", denied_fetch)
    with pytest.raises(ProxyAuthError) as exc_info:
        await codex_oauth_identity.resolve_verified_codex_oauth_identity(
            f"Bearer {access_token}",
            "workspace_account",
        )

    assert access_token not in str(exc_info.value)
    assert access_token not in repr(codex_oauth_identity._identity_cache)


@pytest.mark.asyncio
async def test_credential_denial_is_cached_without_repeating_upstream_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account("acc_a", chatgpt_user_id="user_a", id_token=b"id_a")
    access_token = _jwt({"chatgpt_user_id": "user_a"})
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool",
        endpoint=ResolvedProxyEndpoint("ep", "http", "proxy.test", 8080),
    )
    fetch_count = 0

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def list_eligible_by_chatgpt_account_id(self, chatgpt_account_id: str) -> list[Account]:
            return [account]

    @asynccontextmanager
    async def session_context():
        yield object()

    async def resolve_route(*args: object, **kwargs: object) -> ResolvedUpstreamRoute:
        return route

    async def fetch_usage(*args: object, **kwargs: object) -> UsagePayload:
        nonlocal fetch_count
        fetch_count += 1
        raise UsageFetchError(401, "credential rejected")

    monkeypatch.setattr(codex_oauth_identity, "AccountsRepository", Repo)
    monkeypatch.setattr(codex_oauth_identity, "get_background_session", session_context)
    monkeypatch.setattr(codex_oauth_identity, "resolve_upstream_route", resolve_route)
    monkeypatch.setattr(codex_oauth_identity, "fetch_usage", fetch_usage)

    for _ in range(2):
        with pytest.raises(ProxyAuthError, match="Invalid ChatGPT token"):
            await codex_oauth_identity.resolve_verified_codex_oauth_identity(
                f"Bearer {access_token}",
                "workspace_account",
            )

    assert fetch_count == 1

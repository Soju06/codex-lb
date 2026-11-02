from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from app.core.auth import dependencies, wham_usage_proof
from app.core.auth.wham_usage_proof import WhamUsageProofCache, wham_usage_proof_key
from app.core.clients.usage import UsageFetchError
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError, ProxyUpstreamError
from app.core.usage.models import UsagePayload


def _request(token: str, account_id: str | None = "personal-account") -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())]
    if account_id is not None:
        headers.append((b"chatgpt-account-id", account_id.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/backend-api/wham/usage",
            "query_string": b"",
            "headers": headers,
        }
    )


def test_proof_key_hashes_the_bearer_and_the_presented_account() -> None:
    key = wham_usage_proof_key("personal-token", "personal-account")

    assert "personal-token" not in key
    assert key == wham_usage_proof_key("personal-token", "personal-account")
    assert key != wham_usage_proof_key("personal-token", "other-account")
    assert key != wham_usage_proof_key("other-token", "personal-account")


@pytest.mark.asyncio
async def test_concurrent_identical_proofs_share_one_upstream_call() -> None:
    cache = WhamUsageProofCache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def prove() -> None:
        calls.append("call")
        started.set()
        await release.wait()

    first = asyncio.create_task(cache.prove("key", prove))
    await started.wait()
    second = asyncio.create_task(cache.prove("key", prove))
    await asyncio.sleep(0)

    assert not second.done()
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["call"]


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_fail_the_shared_proof() -> None:
    cache = WhamUsageProofCache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def prove() -> None:
        calls.append("call")
        started.set()
        await release.wait()

    owner = asyncio.create_task(cache.prove("key", prove))
    await started.wait()
    waiter = asyncio.create_task(cache.prove("key", prove))
    await asyncio.sleep(0)

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    release.set()
    await waiter
    assert calls == ["call"]

    # The shared proof finished normally, so its success is cached.
    await cache.prove("key", prove)
    assert calls == ["call"]


@pytest.mark.asyncio
async def test_a_proved_key_is_not_reproved() -> None:
    cache = WhamUsageProofCache()
    calls: list[str] = []

    async def prove() -> None:
        calls.append("call")

    await cache.prove("key", prove)
    await cache.prove("key", prove)

    assert calls == ["call"]


@pytest.mark.asyncio
async def test_an_expired_proof_is_reproved(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(wham_usage_proof.time, "monotonic", lambda: now[0])
    cache = WhamUsageProofCache(ttl_seconds=60.0)
    calls: list[str] = []

    async def prove() -> None:
        calls.append("call")

    await cache.prove("key", prove)
    now[0] += 61.0
    await cache.prove("key", prove)

    assert calls == ["call", "call"]


@pytest.mark.asyncio
async def test_a_failed_proof_is_retried_and_reaches_every_waiter() -> None:
    cache = WhamUsageProofCache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def prove() -> None:
        calls.append("call")
        started.set()
        await release.wait()
        raise UsageFetchError(401, "bad token")

    first = asyncio.create_task(cache.prove("key", prove))
    await started.wait()
    second = asyncio.create_task(cache.prove("key", prove))
    await asyncio.sleep(0)
    release.set()

    for attempt in (first, second):
        with pytest.raises(UsageFetchError):
            await attempt
    assert calls == ["call"]

    async def succeeds() -> None:
        calls.append("retry")

    await cache.prove("key", succeeds)
    assert calls == ["call", "retry"]


@pytest.mark.asyncio
async def test_second_identical_request_does_not_prove_upstream_again(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = WhamUsageProofCache()
    calls: list[tuple[str, str | None, int | None]] = []

    async def stub_fetch_usage(
        *,
        access_token: str,
        account_id: str | None,
        max_retries: int | None = None,
        **_: object,
    ) -> UsagePayload:
        calls.append((access_token, account_id, max_retries))
        return UsagePayload.model_validate({"plan_type": "free"})

    async def no_pool_account(account_id: str) -> str | None:
        del account_id
        return None

    monkeypatch.setattr(dependencies, "get_wham_usage_proof_cache", lambda: cache)
    monkeypatch.setattr(dependencies, "fetch_usage", stub_fetch_usage)
    monkeypatch.setattr(dependencies, "_pool_account_id_for_chatgpt_account", no_pool_account)

    assert await dependencies.validate_wham_usage_identity(_request("personal-token")) is None
    assert await dependencies.validate_wham_usage_identity(_request("personal-token")) is None

    # One bounded identity check, shared by both reads of the same bearer.
    assert calls == [("personal-token", "personal-account", 0)]


@pytest.mark.asyncio
async def test_a_refused_bearer_costs_one_upstream_call_per_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = WhamUsageProofCache()
    calls: list[str] = []

    async def rejecting_fetch_usage(**_: object) -> UsagePayload:
        calls.append("call")
        raise UsageFetchError(401, "bad token")

    async def no_pool_account(account_id: str) -> str | None:
        del account_id
        return None

    monkeypatch.setattr(dependencies, "get_wham_usage_proof_cache", lambda: cache)
    monkeypatch.setattr(dependencies, "fetch_usage", rejecting_fetch_usage)
    monkeypatch.setattr(dependencies, "_pool_account_id_for_chatgpt_account", no_pool_account)

    for _ in range(2):
        with pytest.raises(ProxyAuthError):
            await dependencies.validate_wham_usage_identity(_request("bogus-token"))

    assert calls == ["call", "call"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProxyAuthError),
        (403, ProxyAuthError),
        (429, ProxyRateLimitError),
        (500, ProxyUpstreamError),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_proof_keeps_the_public_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: type[Exception],
) -> None:
    cache = WhamUsageProofCache()

    async def rejecting_fetch_usage(**_: object) -> UsagePayload:
        raise UsageFetchError(status, "upstream said no")

    async def no_pool_account(account_id: str) -> str | None:
        del account_id
        return None

    monkeypatch.setattr(dependencies, "get_wham_usage_proof_cache", lambda: cache)
    monkeypatch.setattr(dependencies, "fetch_usage", rejecting_fetch_usage)
    monkeypatch.setattr(dependencies, "_pool_account_id_for_chatgpt_account", no_pool_account)

    with pytest.raises(expected):
        await dependencies.validate_wham_usage_identity(_request(f"token-{status}"))

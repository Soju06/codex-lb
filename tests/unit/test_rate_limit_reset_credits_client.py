from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from app.core.clients.rate_limit_reset_credits import (
    RateLimitResetCreditsFetchError,
    consume_rate_limit_reset_credit,
    fetch_rate_limit_reset_credits,
)
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class StubResponse:
    def __init__(self, status: int, payload: dict | None, text: str) -> None:
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self, content_type: str | None = None) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    async def text(self) -> str:
        return self._text


@dataclass
class ResetCreditClientState:
    calls: int = 0
    auth: str | None = None
    account: str | None = None


class StubRequestContext:
    def __init__(
        self,
        responses: list[StubResponse],
        state: ResetCreditClientState,
        headers: dict[str, str],
        retry_options: object | None,
    ) -> None:
        self._responses = responses
        self._state = state
        self._headers = headers
        self._retry_options = retry_options

    async def __aenter__(self) -> StubResponse:
        attempts = getattr(self._retry_options, "attempts", 1)
        statuses = set(getattr(self._retry_options, "statuses", set()))
        response: StubResponse | None = None
        for attempt in range(attempts):
            index = min(self._state.calls, len(self._responses) - 1)
            response = self._responses[index]
            self._state.calls += 1
            self._state.auth = self._headers.get("Authorization")
            self._state.account = self._headers.get("chatgpt-account-id")
            if response.status in statuses and attempt < attempts - 1:
                continue
            return response
        if response is None:
            response = StubResponse(500, None, "no response")
        return response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class StubRetryClient:
    def __init__(self, responses: list[StubResponse], state: ResetCreditClientState) -> None:
        self._responses = responses
        self._state = state

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        timeout: object | None = None,
        retry_options: object | None = None,
    ) -> StubRequestContext:
        return StubRequestContext(self._responses, self._state, headers or {}, retry_options)


class StubCodexResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload or {
            "credits": [
                {
                    "id": "RateLimitResetCredit_one",
                    "status": "available",
                    "granted_at": "2026-06-12T01:29:41Z",
                    "expires_at": "2026-07-12T01:29:41Z",
                    "redeemed_at": None,
                }
            ],
            "available_count": 1,
        }


class StubCodexClient:
    def __init__(self, responses: list[StubCodexResponse] | None = None) -> None:
        self._responses = responses or [StubCodexResponse()]
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


@pytest.mark.asyncio
async def test_fetch_rate_limit_reset_credits_retries_and_returns_payload() -> None:
    state = ResetCreditClientState()
    responses = [
        StubResponse(503, None, "busy"),
        StubResponse(
            200,
            {
                "credits": [
                    {
                        "id": "RateLimitResetCredit_one",
                        "status": "available",
                        "granted_at": "2026-06-12T01:29:41Z",
                        "expires_at": "2026-07-12T01:29:41Z",
                        "redeemed_at": None,
                    }
                ],
                "available_count": 1,
            },
            "",
        ),
    ]
    client = StubRetryClient(responses, state)

    payload = await fetch_rate_limit_reset_credits(
        access_token="access-token",
        account_id="acc_test",
        base_url="http://usage.test/backend-api",
        max_retries=1,
        timeout_seconds=2.0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert payload.available_count == 1
    assert payload.credits[0].id == "RateLimitResetCredit_one"
    assert state.calls == 2
    assert state.auth == "Bearer access-token"
    assert state.account == "acc_test"


@pytest.mark.asyncio
async def test_fetch_rate_limit_reset_credits_uses_resolved_codex_route() -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = StubCodexClient()

    payload = await fetch_rate_limit_reset_credits(
        access_token="access-token",
        account_id="acc_test",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        route=route,
        codex_client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert payload.available_count == 1
    assert client.calls[0]["route"] is route
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["url"] == "http://usage.test/backend-api/wham/rate-limit-reset-credits"


@pytest.mark.asyncio
async def test_fetch_rate_limit_reset_credits_preserves_error_code() -> None:
    state = ResetCreditClientState()
    responses = [
        StubResponse(
            401,
            {
                "error": {
                    "code": "account_deactivated",
                    "message": "Your OpenAI account has been deactivated.",
                }
            },
            "",
        )
    ]
    client = StubRetryClient(responses, state)

    with pytest.raises(RateLimitResetCreditsFetchError) as excinfo:
        await fetch_rate_limit_reset_credits(
            access_token="access-token",
            account_id="acc_test",
            base_url="http://usage.test/backend-api",
            timeout_seconds=1.0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "account_deactivated"


@pytest.mark.asyncio
async def test_consume_rate_limit_reset_credit_uses_resolved_codex_route() -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = StubCodexClient(
        responses=[
            StubCodexResponse(
                payload={
                    "code": "ok",
                    "credit": {
                        "id": "credit_one",
                        "status": "redeemed",
                        "redeemed_at": "2026-06-16T12:00:00Z",
                    },
                    "windows_reset": 2,
                }
            )
        ]
    )

    payload = await consume_rate_limit_reset_credit(
        access_token="access-token",
        account_id="acc_test",
        credit_id="credit_one",
        redeem_request_id="redeem-123",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        route=route,
        codex_client=cast(Any, client),
    )

    assert payload.windows_reset == 2
    assert client.calls[0]["route"] is route
    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["url"] == "http://usage.test/backend-api/wham/rate-limit-reset-credits/consume"
    assert client.calls[0]["json"] == {
        "credit_id": "credit_one",
        "redeem_request_id": "redeem-123",
    }

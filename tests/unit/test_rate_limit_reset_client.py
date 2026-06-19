from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.clients.rate_limit_reset import (
    ConsumeRateLimitResetCode,
    RateLimitResetConsumeError,
    RateLimitResetCreditsPayload,
    consume_rate_limit_reset,
    fetch_rate_limit_reset_credits,
    pick_available_reset_credit_id,
)
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class StubResponse:
    def __init__(self, status: int, payload: dict | None) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: str | None = None) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class StubRequestContext:
    def __init__(self, response: StubResponse, captured: dict[str, Any]) -> None:
        self._response = response
        self._captured = captured

    async def __aenter__(self) -> StubResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class StubRetryClient:
    def __init__(self, response: StubResponse, captured: dict[str, Any]) -> None:
        self._response = response
        self._captured = captured

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        timeout: object | None = None,
        retry_options: object | None = None,
    ) -> StubRequestContext:
        self._captured["method"] = method
        self._captured["url"] = url
        self._captured["headers"] = headers or {}
        self._captured["json"] = json or {}
        return StubRequestContext(self._response, self._captured)


class StubCodexResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload or {"code": "reset", "windows_reset": 2}


class StubCodexClient:
    def __init__(self, responses: list[StubCodexResponse] | None = None) -> None:
        self._responses = responses or [StubCodexResponse()]
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_consume_rate_limit_reset_returns_parsed_response() -> None:
    captured: dict[str, Any] = {}
    client = StubRetryClient(
        StubResponse(200, {"code": "reset", "windows_reset": 2}),
        captured,
    )

    result = await consume_rate_limit_reset(
        access_token="access-token",
        account_id="chatgpt-acc-1",
        credit_id="RateLimitResetCredit_abc",
        redeem_request_id="redeem-uuid-1",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        max_retries=0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert result.code == ConsumeRateLimitResetCode.RESET
    assert result.windows_reset == 2
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/wham/rate-limit-reset-credits/consume")
    assert captured["headers"]["Authorization"] == "Bearer access-token"
    assert captured["headers"]["chatgpt-account-id"] == "chatgpt-acc-1"
    assert captured["json"]["credit_id"] == "RateLimitResetCredit_abc"
    assert captured["json"]["redeem_request_id"] == "redeem-uuid-1"


@pytest.mark.asyncio
async def test_consume_rate_limit_reset_raises_on_upstream_error() -> None:
    client = StubRetryClient(
        StubResponse(409, {"error": {"message": "no credit left", "code": "no_credit"}}),
        {},
    )

    with pytest.raises(RateLimitResetConsumeError) as exc_info:
        await consume_rate_limit_reset(
            access_token="access-token",
            account_id="chatgpt-acc-1",
            credit_id="RateLimitResetCredit_abc",
            redeem_request_id="redeem-uuid-2",
            base_url="http://usage.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_consume_rate_limit_reset_uses_resolved_codex_route() -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = StubCodexClient()

    result = await consume_rate_limit_reset(
        access_token="access-token",
        account_id="chatgpt-acc-1",
        credit_id="RateLimitResetCredit_abc",
        redeem_request_id="redeem-uuid-3",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        max_retries=0,
        route=route,
        codex_client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert result.code == ConsumeRateLimitResetCode.RESET
    assert client.calls[0]["route"] is route
    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["url"] == "http://usage.test/backend-api/wham/rate-limit-reset-credits/consume"


@pytest.mark.asyncio
async def test_fetch_rate_limit_reset_credits_returns_parsed_payload() -> None:
    captured: dict[str, Any] = {}
    client = StubRetryClient(
        StubResponse(
            200,
            {
                "available_count": 1,
                "credits": [
                    {
                        "id": "RateLimitResetCredit_old",
                        "status": "available",
                        "granted_at": "2026-06-10T00:00:00Z",
                    },
                    {
                        "id": "RateLimitResetCredit_new",
                        "status": "available",
                        "granted_at": "2026-06-18T00:00:00Z",
                    },
                ],
            },
        ),
        captured,
    )

    result = await fetch_rate_limit_reset_credits(
        access_token="access-token",
        account_id="chatgpt-acc-1",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        max_retries=0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert result.available_count == 1
    assert len(result.credits) == 2
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/wham/rate-limit-reset-credits")


def test_pick_available_reset_credit_id_prefers_oldest_grant() -> None:
    payload = RateLimitResetCreditsPayload.model_validate(
        {
            "available_count": 2,
            "credits": [
                {"id": "RateLimitResetCredit_new", "status": "available", "granted_at": "2026-06-18T00:00:00Z"},
                {"id": "RateLimitResetCredit_old", "status": "available", "granted_at": "2026-06-10T00:00:00Z"},
                {"id": "RateLimitResetCredit_spent", "status": "redeemed", "granted_at": "2026-06-01T00:00:00Z"},
            ],
        }
    )

    assert pick_available_reset_credit_id(payload) == "RateLimitResetCredit_old"
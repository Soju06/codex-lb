from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from app.core.clients.usage import UsageFetchError, fetch_usage
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
class UsageClientState:
    calls: int = 0
    auth: str | None = None
    account: str | None = None


class StubRequestContext:
    def __init__(
        self,
        responses: list[StubResponse],
        state: UsageClientState,
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
    def __init__(self, responses: list[StubResponse], state: UsageClientState) -> None:
        self._responses = responses
        self._state = state

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
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
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12.5,
                    "reset_at": 1735689600,
                    "limit_window_seconds": 60,
                    "reset_after_seconds": 30,
                }
            },
        }


class StubCodexClient:
    def __init__(self, responses: list[StubCodexResponse] | None = None) -> None:
        self._responses = responses or [StubCodexResponse()]
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


@pytest.fixture
def usage_server() -> tuple[str, StubRetryClient, UsageClientState]:
    state = UsageClientState()
    responses = [
        StubResponse(503, None, "busy"),
        StubResponse(
            200,
            {
                "plan_type": "plus",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 12.5,
                        "reset_at": 1735689600,
                        "limit_window_seconds": 60,
                        "reset_after_seconds": 30,
                    }
                },
            },
            "",
        ),
    ]
    client = StubRetryClient(responses, state)
    return "http://usage.test/backend-api", client, state


@pytest.fixture
def failing_usage_server() -> tuple[str, StubRetryClient]:
    state = UsageClientState()
    responses = [StubResponse(503, None, "busy")]
    client = StubRetryClient(responses, state)
    return "http://usage.test/backend-api", client


@pytest.mark.asyncio
async def test_fetch_usage_retries_and_returns_payload(usage_server):
    base_url, client, state = usage_server
    data = await fetch_usage(
        access_token="access-token",
        account_id="acc_test",
        base_url=base_url,
        max_retries=1,
        timeout_seconds=2.0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )
    assert data.plan_type == "plus"
    assert state.calls == 2
    assert state.auth == "Bearer access-token"
    assert state.account == "acc_test"


@pytest.mark.asyncio
async def test_fetch_usage_uses_resolved_codex_route() -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = StubCodexClient()

    data = await fetch_usage(
        access_token="access-token",
        account_id="acc_test",
        base_url="http://usage.test/backend-api",
        timeout_seconds=2.0,
        route=route,
        codex_client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert data.plan_type == "plus"
    assert client.calls[0]["route"] is route
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["url"] == "http://usage.test/backend-api/wham/usage"


@pytest.mark.asyncio
async def test_fetch_usage_retries_resolved_codex_route_retryable_status(monkeypatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.core.clients.usage.asyncio.sleep", no_sleep)
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    client = StubCodexClient(
        [
            StubCodexResponse(503, {"error": {"message": "busy"}}),
            StubCodexResponse(),
        ]
    )

    data = await fetch_usage(
        access_token="access-token",
        account_id="acc_test",
        base_url="http://usage.test/backend-api",
        max_retries=1,
        timeout_seconds=2.0,
        route=route,
        codex_client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert data.plan_type == "plus"
    assert len(client.calls) == 2
    assert client.calls[0]["route"] is route
    assert client.calls[1]["route"] is route


@pytest.mark.asyncio
async def test_fetch_usage_raises_after_retries(failing_usage_server):
    base_url, client = failing_usage_server
    with pytest.raises(UsageFetchError) as excinfo:
        await fetch_usage(
            access_token="access-token",
            account_id=None,
            base_url=base_url,
            max_retries=0,
            timeout_seconds=1.0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )
    exc = excinfo.value
    assert isinstance(exc, UsageFetchError)
    assert exc.status_code == 503


@pytest.mark.asyncio
async def test_fetch_usage_preserves_error_code():
    state = UsageClientState()
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

    with pytest.raises(UsageFetchError) as excinfo:
        await fetch_usage(
            access_token="access-token",
            account_id=None,
            base_url="http://usage.test/backend-api",
            max_retries=0,
            timeout_seconds=1.0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    exc = excinfo.value
    assert exc.status_code == 401
    assert exc.code == "account_deactivated"
    assert "deactivated" in exc.message.lower()

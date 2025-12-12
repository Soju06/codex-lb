from __future__ import annotations

import pytest

from app.core.clients.usage import UsageFetchError, fetch_usage

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


class StubRequestContext:
    def __init__(
        self,
        responses: list[StubResponse],
        state: dict[str, object],
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
            index = min(self._state["calls"], len(self._responses) - 1)
            response = self._responses[index]
            self._state["calls"] += 1
            self._state["auth"] = self._headers.get("Authorization")
            self._state["account"] = self._headers.get("chatgpt-account-id")
            if response.status in statuses and attempt < attempts - 1:
                continue
            return response
        if response is None:
            response = StubResponse(500, None, "no response")
        return response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class StubRetryClient:
    def __init__(self, responses: list[StubResponse], state: dict[str, object]) -> None:
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


@pytest.fixture
def usage_server() -> tuple[str, StubRetryClient, dict[str, object]]:
    state: dict[str, object] = {"calls": 0, "auth": None, "account": None}
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
    state: dict[str, object] = {"calls": 0, "auth": None, "account": None}
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
        client=client,
    )
    assert data.plan_type == "plus"
    assert state["calls"] == 2
    assert state["auth"] == "Bearer access-token"
    assert state["account"] == "acc_test"


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
            client=client,
        )
    assert excinfo.value.status_code == 503

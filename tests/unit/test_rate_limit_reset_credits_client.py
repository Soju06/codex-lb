from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import pytest

from app.core.clients.headers import build_chatgpt_auth_headers
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    ResetCreditFetchError,
    ResetCreditsResponse,
    build_snapshot,
    consume_reset_credit,
    fetch_reset_credits,
)
from app.core.clients.usage import _usage_headers
from app.core.config.settings import get_settings

pytestmark = pytest.mark.unit


class StubResponse:
    def __init__(self, status: int, payload: object | None, text: str) -> None:
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self, content_type: str | None = None) -> object:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    async def text(self) -> str:
        return self._text


@dataclass
class ClientState:
    calls: int = 0
    method: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    json_body: dict[str, Any] | None = None


class StubRequestContext:
    def __init__(
        self,
        responses: list[StubResponse],
        state: ClientState,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        retry_options: object | None,
    ) -> None:
        self._responses = responses
        self._state = state
        self._method = method
        self._url = url
        self._headers = headers
        self._json_body = json_body
        self._retry_options = retry_options

    async def __aenter__(self) -> StubResponse:
        attempts = getattr(self._retry_options, "attempts", 1)
        statuses = set(getattr(self._retry_options, "statuses", set()))
        response: StubResponse | None = None
        for attempt in range(attempts):
            index = min(self._state.calls, len(self._responses) - 1)
            response = self._responses[index]
            self._state.calls += 1
            self._state.method = self._method
            self._state.url = self._url
            self._state.headers = dict(self._headers)
            self._state.json_body = dict(self._json_body) if self._json_body else None
            if response.status in statuses and attempt < attempts - 1:
                continue
            return response
        if response is None:
            response = StubResponse(500, None, "no response")
        return response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class StubRetryClient:
    def __init__(self, responses: list[StubResponse], state: ClientState) -> None:
        self._responses = responses
        self._state = state

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: object | None = None,
        retry_options: object | None = None,
    ) -> StubRequestContext:
        return StubRequestContext(
            self._responses,
            self._state,
            method,
            url,
            headers or {},
            json,
            retry_options,
        )


def _list_payload() -> dict:
    return {
        "credits": [
            {
                "id": "RateLimitResetCredit_test",
                "reset_type": "codex_rate_limits",
                "status": "available",
                "granted_at": "2026-06-12T01:29:41.346025Z",
                "expires_at": "2026-07-12T01:29:41.346025Z",
                "redeem_started_at": None,
                "redeemed_at": None,
                "title": "One free rate limit reset",
                "description": "Thanks for using Codex!",
            }
        ],
        "available_count": 1,
    }


def test_usage_headers_delegate_to_shared_helper() -> None:
    """The usage client and the reset-credits client share one header builder."""
    assert _usage_headers("tok", "acc_workspace") == build_chatgpt_auth_headers("tok", "acc_workspace")


@pytest.mark.asyncio
async def test_fetch_reset_credits_sends_bearer_and_account_id_headers() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, _list_payload(), "")], state)

    data = await fetch_reset_credits(
        "access-token",
        "acc_workspace",
        base_url="http://upstream.test/backend-api",
        timeout_seconds=2.0,
        max_retries=0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert isinstance(data, ResetCreditsResponse)
    assert data.available_count == 1
    assert data.credits[0].id == "RateLimitResetCredit_test"
    assert state.method == "GET"
    assert state.url == "http://upstream.test/backend-api/wham/rate-limit-reset-credits"
    assert state.headers is not None
    assert state.headers["Authorization"] == "Bearer access-token"
    assert state.headers["chatgpt-account-id"] == "acc_workspace"


@pytest.mark.asyncio
async def test_fetch_reset_credits_skips_account_id_header_for_email_and_local_prefixes() -> None:
    for account_id in ("email_user@example.com", "local_abcd"):
        state = ClientState()
        client = StubRetryClient([StubResponse(200, _list_payload(), "")], state)
        await fetch_reset_credits(
            "access-token",
            account_id,
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )
        assert state.headers is not None
        assert "chatgpt-account-id" not in state.headers, account_id


@pytest.mark.asyncio
async def test_fetch_reset_credits_normalizes_base_url_without_backend_api_segment() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, {"credits": [], "available_count": 0}, "")], state)

    await fetch_reset_credits(
        "access-token",
        None,
        base_url="http://upstream.test/",  # trailing slash, no backend-api
        timeout_seconds=2.0,
        max_retries=0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert state.url == "http://upstream.test/backend-api/wham/rate-limit-reset-credits"


@pytest.mark.asyncio
async def test_fetch_reset_credits_raises_on_non_200() -> None:
    state = ClientState()
    client = StubRetryClient(
        [StubResponse(401, {"error": {"code": "unauthorized", "message": "bad token"}}, "")],
        state,
    )

    with pytest.raises(ResetCreditFetchError) as excinfo:
        await fetch_reset_credits(
            "access-token",
            None,
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "unauthorized"


@pytest.mark.asyncio
async def test_fetch_reset_credits_handles_non_json_body() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(502, None, "<html>boom</html>")], state)

    with pytest.raises(ResetCreditFetchError) as excinfo:
        await fetch_reset_credits(
            "access-token",
            None,
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_fetch_reset_credits_rejects_malformed_success_body() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, ["not", "an", "object"], "")], state)

    with pytest.raises(ResetCreditFetchError) as excinfo:
        await fetch_reset_credits(
            "access-token",
            None,
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_fetch_reset_credits_rejects_success_body_missing_contract_fields() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, {"credits": []}, "")], state)

    with pytest.raises(ResetCreditFetchError) as excinfo:
        await fetch_reset_credits(
            "access-token",
            None,
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_consume_reset_credit_sends_credit_id_and_uuid_redeem_request_id() -> None:
    state = ClientState()
    client = StubRetryClient(
        [
            StubResponse(
                200,
                {
                    "code": "reset",
                    "credit": {
                        "id": "RateLimitResetCredit_test",
                        "status": "redeemed",
                        "redeemed_at": "2026-06-13T13:12:31Z",
                    },
                    "windows_reset": 1,
                },
                "",
            )
        ],
        state,
    )

    result = await consume_reset_credit(
        "access-token",
        "acc_workspace",
        "RateLimitResetCredit_test",
        base_url="http://upstream.test/backend-api",
        timeout_seconds=2.0,
        max_retries=0,
        client=cast(Any, client),
        allow_direct_egress=True,
    )

    assert isinstance(result, ConsumeResetCreditResponse)
    assert result.code == "reset"
    assert result.windows_reset == 1
    assert result.credit is not None and result.credit.redeemed_at is not None
    assert state.method == "POST"
    assert state.url == "http://upstream.test/backend-api/wham/rate-limit-reset-credits/consume"
    assert state.headers is not None
    assert state.headers["Authorization"] == "Bearer access-token"
    assert state.headers["chatgpt-account-id"] == "acc_workspace"
    assert state.headers["Content-Type"] == "application/json"
    # body carries the credit id and a freshly-generated uuid redeem_request_id
    assert state.json_body is not None
    assert state.json_body["credit_id"] == "RateLimitResetCredit_test"
    redeem_request_id = state.json_body["redeem_request_id"]
    assert isinstance(redeem_request_id, str) and len(redeem_request_id) == 36
    # canonical uuid v4
    parsed = UUID(redeem_request_id, version=4)
    assert str(parsed) == redeem_request_id


@pytest.mark.asyncio
async def test_consume_reset_credit_generates_fresh_redeem_request_id_each_call() -> None:
    ids: list[str] = []
    for _ in range(2):
        state = ClientState()
        client = StubRetryClient(
            [StubResponse(200, {"code": "reset", "credit": {"id": "x"}, "windows_reset": 1}, "")],
            state,
        )
        await consume_reset_credit(
            "access-token",
            None,
            "RateLimitResetCredit_test",
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )
        assert state.json_body is not None
        ids.append(state.json_body["redeem_request_id"])
    assert ids[0] != ids[1]


@pytest.mark.asyncio
async def test_consume_reset_credit_does_not_retry_when_max_retries_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    original_retries = settings.usage_fetch_max_retries
    monkeypatch.setattr(settings, "usage_fetch_max_retries", 2)

    state = ClientState()
    client = StubRetryClient(
        [
            StubResponse(503, {"error": {"code": "temporarily_unavailable", "message": "retry later"}}, ""),
            StubResponse(200, {"code": "reset", "credit": {"id": "x"}, "windows_reset": 1}, ""),
        ],
        state,
    )

    with pytest.raises(ConsumeResetCreditError) as excinfo:
        await consume_reset_credit(
            "access-token",
            None,
            "RateLimitResetCredit_test",
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "temporarily_unavailable"
    assert state.calls == 1
    monkeypatch.setattr(settings, "usage_fetch_max_retries", original_retries)


@pytest.mark.asyncio
async def test_consume_reset_credit_raises_on_non_200() -> None:
    state = ClientState()
    client = StubRetryClient(
        [StubResponse(409, {"error": {"code": "no_credit", "message": "none"}}, "")],
        state,
    )

    with pytest.raises(ConsumeResetCreditError) as excinfo:
        await consume_reset_credit(
            "access-token",
            None,
            "RateLimitResetCredit_test",
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "no_credit"


@pytest.mark.asyncio
async def test_consume_reset_credit_rejects_malformed_success_body() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, "<html>not json</html>", "")], state)

    with pytest.raises(ConsumeResetCreditError) as excinfo:
        await consume_reset_credit(
            "access-token",
            None,
            "RateLimitResetCredit_test",
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_consume_reset_credit_rejects_success_body_missing_contract_fields() -> None:
    state = ClientState()
    client = StubRetryClient([StubResponse(200, {"code": "reset"}, "")], state)

    with pytest.raises(ConsumeResetCreditError) as excinfo:
        await consume_reset_credit(
            "access-token",
            None,
            "RateLimitResetCredit_test",
            base_url="http://upstream.test/backend-api",
            timeout_seconds=2.0,
            max_retries=0,
            client=cast(Any, client),
            allow_direct_egress=True,
        )

    assert excinfo.value.status_code == 502


def test_build_snapshot_projects_nearest_available_expiry() -> None:
    response = ResetCreditsResponse.model_validate(
        {
            "credits": [
                {"id": "a", "status": "available", "expires_at": "2026-07-10T00:00:00Z"},
                {"id": "b", "status": "available", "expires_at": "2026-06-20T00:00:00Z"},
                {"id": "c", "status": "redeemed", "expires_at": "2026-06-01T00:00:00Z"},
            ],
            "available_count": 2,
        }
    )

    snapshot = build_snapshot(response)

    assert snapshot.available_count == 2
    assert snapshot.nearest_expires_at is not None
    assert snapshot.nearest_expires_at.year == 2026
    assert snapshot.nearest_expires_at.month == 6
    assert snapshot.nearest_expires_at.day == 20
    assert [credit.id for credit in snapshot.credits] == ["a", "b", "c"]


def test_build_snapshot_returns_none_expiry_when_no_available_credit() -> None:
    response = ResetCreditsResponse.model_validate(
        {"credits": [{"id": "a", "status": "redeemed"}], "available_count": 0}
    )
    assert build_snapshot(response).nearest_expires_at is None

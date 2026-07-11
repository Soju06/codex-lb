"""HTTP envelope tests for the Claude OAuth link flow endpoints.

The business logic is exhaustively tested in ``test_claude_oauth_service.py``;
this module covers only the FastAPI layer: auth dependencies, status-code
mapping, and request/response shape.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.claude.oauth import api as api_module
from app.modules.claude.oauth.api import router
from app.modules.claude.oauth.schemas import (
    ClaudeOauthCallbackResponse,
    ClaudeOauthStartResponse,
    ClaudeOauthStatusResponse,
)
from app.modules.claude.oauth.service import ClaudeOauthFlowError
from app.modules.claude.schemas import ClaudeAccountResponse

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeService:
    """Stub matching the surface the API layer uses."""

    def __init__(self) -> None:
        self.status_payload: ClaudeOauthStatusResponse | None = None
        self.callback_payload: ClaudeOauthCallbackResponse | None = None
        self.callback_error: ClaudeOauthFlowError | None = None
        self.last_callback_flow_id: str | None = None
        self.last_callback_code: str | None = None
        self.last_callback_state: str | None = None
        self.start_called = False

    async def start_oauth(self) -> ClaudeOauthStartResponse:
        self.start_called = True
        return ClaudeOauthStartResponse(
            flow_id="flow-1",
            authorization_url="https://auth.example.test/oauth/authorize?code_challenge=x",
            state_token="STATE_TOKEN_FROM_START",
            expires_in_seconds=600,
            callback_instructions="Open the URL, authorize, then paste the code.",
            redirect_uri="https://r.example.test/cb",
        )

    async def oauth_status(self, flow_id: str) -> ClaudeOauthStatusResponse:
        assert self.status_payload is not None
        return self.status_payload

    async def complete_oauth(self, *, flow_id: str, code: str, state: str) -> ClaudeOauthCallbackResponse:
        self.last_callback_flow_id = flow_id
        self.last_callback_code = code
        self.last_callback_state = state
        if self.callback_error is not None:
            raise self.callback_error
        assert self.callback_payload is not None
        return self.callback_payload


@pytest.fixture()
def app_with_fake_service(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeService()

    async def _override_service():
        yield fake

    app = FastAPI()
    # Register the project's dashboard exception handlers so Dashboard*Error
    # exceptions surface as `{"error": {"code": ..., "message": ...}}` rather
    # than FastAPI's default 500 with a `detail` wrapper.
    from app.core.handlers.exceptions import add_exception_handlers

    add_exception_handlers(app)
    app.include_router(router)
    # Skip dashboard auth + write access for unit tests via FastAPI's
    # dependency_overrides (Depends() captures the callable reference at
    # module import time, so monkeypatch on the module attribute is a
    # no-op for the registered dependency tree).
    app.dependency_overrides[api_module.validate_dashboard_session] = lambda: None
    app.dependency_overrides[api_module.set_dashboard_error_format] = lambda: None
    app.dependency_overrides[api_module.require_dashboard_write_access] = lambda: None
    app.dependency_overrides[api_module.get_claude_oauth_service] = _override_service
    return TestClient(app), fake


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


def test_start_returns_authorization_payload(
    app_with_fake_service,
) -> None:
    client, fake = app_with_fake_service
    resp = client.post("/api/claude/oauth/start", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["flowId"] == "flow-1"
    assert body["stateToken"] == "STATE_TOKEN_FROM_START"
    assert body["authorizationUrl"].startswith("https://auth.example.test/oauth/authorize")
    assert body["expiresInSeconds"] == 600
    assert fake.start_called


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


def test_status_returns_pending(app_with_fake_service) -> None:
    client, fake = app_with_fake_service
    fake.status_payload = ClaudeOauthStatusResponse(
        flow_id="flow-1",
        status="pending",
        started_at=datetime.now(timezone.utc),
    )
    resp = client.get("/api/claude/oauth/status", params={"flowId": "flow-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["flowId"] == "flow-1"


def test_status_unknown_flow_returns_error_code_payload(app_with_fake_service) -> None:
    client, fake = app_with_fake_service
    fake.status_payload = ClaudeOauthStatusResponse(
        flow_id="nope",
        status="error",
        error_code="flow_not_found",
        error_message="No OAuth flow with that id",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    resp = client.get("/api/claude/oauth/status", params={"flowId": "nope"})
    assert resp.status_code == 200
    assert resp.json()["errorCode"] == "flow_not_found"


# ---------------------------------------------------------------------------
# /callback
# ---------------------------------------------------------------------------


def test_callback_happy_path(app_with_fake_service) -> None:
    client, fake = app_with_fake_service
    fake.callback_payload = ClaudeOauthCallbackResponse(
        status="success",
        account=ClaudeAccountResponse.model_validate(
            {
                "id": "claude-uuid-1",
                "claude_account_uuid": "uuid-1",
                "user_email": "u@example.test",
                "is_active": True,
                "created_at": datetime.now(timezone.utc),
            }
        ),
    )
    resp = client.post(
        "/api/claude/oauth/callback",
        json={"flowId": "flow-1", "code": "AUTH_CODE", "state": "STATE"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["account"]["id"] == "claude-uuid-1"
    assert fake.last_callback_flow_id == "flow-1"
    assert fake.last_callback_code == "AUTH_CODE"
    assert fake.last_callback_state == "STATE"


@pytest.mark.parametrize(
    "code,http_status",
    [
        ("flow_not_found", 404),
        ("flow_expired", 410),
        ("flow_not_pending", 409),
        ("state_mismatch", 400),
        ("invalid_grant", 502),
        ("anthropic_unreachable", 502),
        ("id_token_missing", 400),
        ("id_token_malformed", 400),
        ("id_token_claims_incomplete", 400),
        ("account_already_exists", 409),
    ],
)
def test_callback_error_mapping(
    app_with_fake_service,
    code: str,
    http_status: int,
) -> None:
    client, fake = app_with_fake_service
    fake.callback_error = ClaudeOauthFlowError(code, f"msg for {code}")
    resp = client.post(
        "/api/claude/oauth/callback",
        json={"flowId": "flow-1", "code": "C", "state": "S"},
    )
    assert resp.status_code == http_status, f"error {code}: got {resp.status_code}"
    body = resp.json()
    # Dashboard exception handlers emit `{"error": {"code": ..., "message": ...}}`
    # directly (no `detail` wrapper). The error.code MUST equal the service-layer
    # error_code so the dialog can render a friendly message.
    assert body.get("error", {}).get("code") == code, body


def test_callback_rejects_empty_code(app_with_fake_service) -> None:
    client, _ = app_with_fake_service
    resp = client.post(
        "/api/claude/oauth/callback",
        json={"flowId": "flow-1", "code": "", "state": "S"},
    )
    assert resp.status_code == 422  # Pydantic validation


def test_callback_rejects_oversized_code(app_with_fake_service) -> None:
    client, _ = app_with_fake_service
    resp = client.post(
        "/api/claude/oauth/callback",
        json={"flowId": "flow-1", "code": "x" * 5000, "state": "S"},
    )
    assert resp.status_code == 422

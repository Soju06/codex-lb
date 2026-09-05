from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED
from tests.integration.test_proxy_websocket_responses import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _websocket_response_batch,
    _websocket_response_create,
    _websocket_settings,
)

pytestmark = pytest.mark.integration

SECURITY_MESSAGE = (
    "This chat was flagged for possible cybersecurity risk. "
    "To get authorized for security work, join the Trusted Access for Cyber program. "
    "https://chatgpt.com/cyber"
)


@pytest.mark.parametrize(
    ("failure_kind", "replay_guard"),
    [("event", None), ("connect", None), ("event", "previous_response"), ("event", "file"), ("event", "output")],
)
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
def test_websocket_security_retry_exhaustion_preserves_original_error(
    app_instance, monkeypatch, failure_kind, replay_guard, path
):
    def error_upstream(code, message, *, visible_output=False):
        prefix = (
            [
                _FakeUpstreamMessage(
                    "text", text=json.dumps({"type": "response.created", "response": {"id": "resp_visible"}})
                ),
                _FakeUpstreamMessage(
                    "text", text=json.dumps({"type": "response.output_text.delta", "delta": "Visible output"})
                ),
            ]
            if visible_output
            else []
        )
        return _SequencedUpstreamWebSocket(
            [],
            deferred_message_batches=[
                [
                    *prefix,
                    _FakeUpstreamMessage(
                        "text",
                        text=json.dumps(
                            {
                                "type": "error",
                                "status": 400,
                                "error": {"type": "invalid_request_error", "code": code, "message": message},
                            }
                        ),
                    ),
                ]
            ],
        )

    ordinary = error_upstream("invalid_request_error", SECURITY_MESSAGE, visible_output=replay_guard == "output")
    authorized = error_upstream("invalid_api_key", "Your session expired. Please log in again.")
    followup = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_after_security_exhaustion")]
    )
    selections = []
    opened = []

    class SettingsCache:
        async def get(self):
            return _websocket_settings()

    async def select_account(self, deadline, **kwargs):
        selections.append((kwargs["require_security_work_authorized"], set(kwargs["exclude_account_ids"])))
        if len(selections) > 3:
            return proxy_module.AccountSelection(
                account=proxy_module.Account(id="followup", security_work_authorized=False), error_message=None
            )
        if len(selections) <= 2:
            is_authorized = len(selections) == 2
            return proxy_module.AccountSelection(
                account=proxy_module.Account(
                    id="authorized" if is_authorized else "ordinary",
                    security_work_authorized=is_authorized,
                ),
                error_message=None,
            )
        return proxy_module.AccountSelection(
            account=None,
            error_message="All authorized accounts excluded",
            error_code=SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED,
        )

    async def open_upstream(self, account, headers, **kwargs):
        opened.append(account.id)
        if account.id == "followup":
            return account, followup
        if account.id == "ordinary":
            return account, ordinary
        if failure_kind == "connect":
            raise proxy_module.ProxyResponseError(
                502,
                proxy_module.openai_error("upstream_unavailable", "Authorized connect failed"),
                failure_phase="connect",
                retryable_same_contract=True,
            )
        return account, authorized

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_api_module, "validate_required_proxy_api_key_authorization", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: SettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", open_upstream)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", AsyncMock())
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", AsyncMock())
    if replay_guard == "previous_response":
        monkeypatch.setattr(
            proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", AsyncMock(return_value="ordinary")
        )
    elif replay_guard == "file":
        monkeypatch.setattr(
            proxy_module.ProxyService, "_resolve_file_account_for_responses", AsyncMock(return_value="ordinary")
        )

    request = _websocket_response_create("Analyze this security task")
    if replay_guard == "previous_response":
        request["previous_response_id"] = "resp_existing_owner"
    elif replay_guard == "file":
        request["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file-owned"}]}]
    frames = []
    with TestClient(app_instance) as client:
        with client.websocket_connect(path) as websocket:
            websocket.send_text(json.dumps(request))
            while True:
                frame = json.loads(websocket.receive_text())
                frames.append(frame)
                if frame["type"] == "error":
                    break
            if replay_guard is None:
                websocket.send_text(json.dumps(_websocket_response_create("An independent follow-up request")))
                created = json.loads(websocket.receive_text())
                assert created["type"] == "response.created"
                assert created["response"]["id"] == "resp_after_security_exhaustion"
                completed = json.loads(websocket.receive_text())
                assert completed["type"] == "response.completed"
                assert completed["response"]["id"] == "resp_after_security_exhaustion"

    if replay_guard is not None:
        assert selections == [(False, set())]
        assert opened == ["ordinary"]
        assert len(ordinary.sent_text) == 1
        assert not authorized.sent_text
        assert frames[-1]["error"]["message"] == SECURITY_MESSAGE
        assert all(frame.get("warning", {}).get("action") != "retry_security_work_authorized" for frame in frames)
        return

    assert selections == [(False, set()), (True, set()), (True, {"authorized"}), (False, set())]
    assert [frame["warning"]["code"] for frame in frames[:-1]] == [
        "security_work_authorization_required",
        "no_security_work_authorized_accounts",
    ]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["error"]["code"] == "security_work_authorization_required"
    assert frames[-1]["error"]["message"] == SECURITY_MESSAGE
    assert SECURITY_WORK_AUTHORIZED_ACCOUNTS_EXHAUSTED not in json.dumps(frames)
    assert opened == ["ordinary", "authorized", "followup"]
    assert len(ordinary.sent_text) == 1
    assert len(authorized.sent_text) == (1 if failure_kind == "event" else 0)
    assert len(followup.sent_text) == 1

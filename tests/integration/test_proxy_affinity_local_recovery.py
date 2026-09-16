from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.config.settings import get_settings
from tests.integration.test_proxy_affinity_websocket_observation import SyntheticUpstream, seed_account

pytestmark = pytest.mark.integration


def test_local_previous_response_recovery_keeps_resolved_affinity(app_instance, monkeypatch):
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED", "false")
    get_settings.cache_clear()
    connections = []
    rejected_anchors = []

    class RejectReusedAnchor(SyntheticUpstream):
        async def send_text(self, text: str) -> None:
            payload = json.loads(text)
            if payload.get("previous_response_id") and not rejected_anchors:
                rejected_anchors.append(payload["previous_response_id"])
                self.messages.put_nowait(
                    UpstreamWebSocketMessage(
                        kind="text",
                        text=json.dumps(
                            {
                                "type": "error",
                                "status": 400,
                                "error": {
                                    "type": "invalid_request_error",
                                    "code": "previous_response_not_found",
                                    "message": "Previous response with id 'resp_affinity_ws' not found.",
                                    "param": "previous_response_id",
                                },
                            }
                        ),
                    )
                )
                return
            await super().send_text(text)

    async def connect(*args, **kwargs):
        upstream = RejectReusedAnchor()
        connections.append(upstream)
        return upstream

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    try:
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            assert client.portal is not None
            key = client.portal.call(seed_account)
            settings = client.put("http://localhost/api/settings", json={"apiKeyAuthEnabled": True})
            assert settings.status_code == 200
            headers = {"authorization": f"Bearer {key}"}
            first = client.post(
                "http://localhost/v1/responses",
                headers=headers,
                json={"model": "gpt-5.1", "input": "first", "prompt_cache_key": "abc", "stream": True},
            )
            assert "response.completed" in first.text
            second = client.post(
                "http://localhost/v1/responses",
                headers=headers,
                json={
                    "model": "gpt-5.1",
                    "input": "second",
                    "prompt_cache_key": "abc",
                    "stream": True,
                    "previous_response_id": "resp_affinity_ws",
                },
            )
            assert "response.completed" in second.text, second.text
            assert rejected_anchors == ["resp_affinity_ws"]
            assert len(connections) >= 2
            assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
            rows = client.get("http://localhost/api/request-logs").json()["requests"]
            assert rows[0]["stickyKeySource"] == "payload"
            assert rows[0]["stickyKind"] == "prompt_cache"
            assert rows[0]["stickyKeyHash"] == "ba7816bf8f01cfea"
    finally:
        get_settings.cache_clear()

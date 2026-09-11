from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService

pytestmark = pytest.mark.integration


class SyntheticUpstream:
    def __init__(self, terminal: str = "response.completed") -> None:
        self.terminal = terminal
        self.messages: asyncio.Queue = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        request = json.loads(text)
        for kind in ("response.created", self.terminal):
            self.messages.put_nowait(
                UpstreamWebSocketMessage(
                    kind="text",
                    text=json.dumps(
                        {
                            "type": kind,
                            "response": {
                                "id": "resp_affinity_ws",
                                "object": "response",
                                "model": request["model"],
                                "status": "completed" if kind.endswith("completed") else "in_progress",
                                "output": [],
                                "error": {"code": "invalid_request_error", "message": "synthetic failure"}
                                if kind == "response.failed"
                                else None,
                                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                            },
                        }
                    ),
                )
            )

    async def receive(self):
        return await self.messages.get()

    def archive_received(self, message) -> None:
        pass

    async def close(self) -> None:
        pass


async def seed_account() -> str:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="affinity-ws",
                chatgpt_account_id="affinity-ws",
                email="synthetic@example.invalid",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("synthetic-access"),
                refresh_token_encrypted=encryptor.encrypt("synthetic-refresh"),
                id_token_encrypted=encryptor.encrypt("synthetic-id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()
        key = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(name="synthetic-ws", allowed_models=None, transport_policy_override="always_websocket")
        )
        return key.key


@pytest.mark.parametrize("terminal", ["response.completed", "response.failed"])
def test_native_websocket_affinity_is_visible_in_request_log(app_instance, monkeypatch, terminal):
    async def connect(*args, **kwargs):
        return SyntheticUpstream(terminal)

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        key = client.portal.call(seed_account)
        with client.websocket_connect(
            "ws://localhost/backend-api/codex/responses", headers={"authorization": f"Bearer {key}"}
        ) as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.1",
                    "input": "synthetic observation",
                    "prompt_cache_key": "abc",
                }
            )
            created = websocket.receive_json()
            assert created["type"] == "response.created", created
            assert websocket.receive_json()["type"] == terminal
        assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
        response = client.get("http://localhost/api/request-logs")
        assert response.status_code == 200
        rows = response.json()["requests"]
        assert len(rows) == 1
        assert rows[0]["stickyKeySource"] == "payload"
        assert rows[0]["stickyKind"] == "prompt_cache"
        assert rows[0]["stickyKeyHash"] == "ba7816bf8f01cfea"


@pytest.mark.parametrize("terminal", ["response.completed", "response.failed"])
def test_http_bridge_affinity_is_visible_in_request_log(app_instance, monkeypatch, terminal):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED", "false")
    get_settings.cache_clear()

    async def connect(*args, **kwargs):
        return SyntheticUpstream(terminal)

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    try:
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            assert client.portal is not None
            key = client.portal.call(seed_account)
            response = client.post(
                "http://localhost/backend-api/codex/responses",
                headers={"authorization": f"Bearer {key}"},
                json={"model": "gpt-5.1", "input": "synthetic bridge", "prompt_cache_key": "abc", "stream": True},
            )
            assert response.status_code == 200, response.text
            assert terminal in response.text
            assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
            response = client.get("http://localhost/api/request-logs")
            assert response.status_code == 200
            rows = response.json()["requests"]
            assert len(rows) == 1
            assert rows[0]["stickyKeySource"] == "payload"
            assert rows[0]["stickyKind"] == "prompt_cache"
            assert rows[0]["stickyKeyHash"] == "ba7816bf8f01cfea"
    finally:
        get_settings.cache_clear()


def test_websocket_connect_failure_retains_affinity_in_request_log(app_instance, monkeypatch):
    from app.core.clients.proxy import ProxyResponseError
    from app.core.errors import openai_error

    async def connect(*args, **kwargs):
        raise ProxyResponseError(400, openai_error("invalid_request_error", "synthetic connect rejection"))

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        key = client.portal.call(seed_account)
        with client.websocket_connect(
            "ws://localhost/backend-api/codex/responses", headers={"authorization": f"Bearer {key}"}
        ) as websocket:
            websocket.send_json(
                {
                    "type": "response.create",
                    "model": "gpt-5.1",
                    "input": "synthetic failure",
                    "prompt_cache_key": "abc",
                }
            )
            event = websocket.receive_json()
            assert event["type"] == "error", event
        assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
        response = client.get("http://localhost/api/request-logs")
        assert response.status_code == 200
        rows = response.json()["requests"]
        assert len(rows) == 1
        assert rows[0]["stickyKeySource"] == "payload"
        assert rows[0]["stickyKind"] == "prompt_cache"
        assert rows[0]["stickyKeyHash"] == "ba7816bf8f01cfea"


def test_native_websocket_turns_keep_independent_affinity_observations(app_instance, monkeypatch):
    async def connect(*args, **kwargs):
        return SyntheticUpstream()

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        key = client.portal.call(seed_account)
        with client.websocket_connect(
            "ws://localhost/backend-api/codex/responses", headers={"authorization": f"Bearer {key}"}
        ) as websocket:
            for cache_key in ("abc", "def"):
                websocket.send_json(
                    {
                        "type": "response.create",
                        "model": "gpt-5.1",
                        "input": "synthetic independent turn",
                        "prompt_cache_key": cache_key,
                    }
                )
                assert websocket.receive_json()["type"] == "response.created"
                assert websocket.receive_json()["type"] == "response.completed"
        assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
        response = client.get("http://localhost/api/request-logs")
        assert response.status_code == 200
        rows = response.json()["requests"]
        assert len(rows) == 2
        assert {row["stickyKeyHash"] for row in rows} == {"ba7816bf8f01cfea", "cb8379ac2098aa16"}
        assert all(row["stickyKeySource"] == "payload" for row in rows)


def test_bridge_security_retry_logs_cleared_effective_affinity(app_instance, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED", "false")
    get_settings.cache_clear()

    class SecurityRejectedUpstream(SyntheticUpstream):
        async def send_text(self, text: str) -> None:
            self.messages.put_nowait(
                UpstreamWebSocketMessage(
                    kind="text",
                    text=json.dumps(
                        {
                            "type": "response.failed",
                            "response": {
                                "id": "resp_security_failed",
                                "status": "failed",
                                "error": {
                                    "code": "invalid_request_error",
                                    "type": "invalid_request_error",
                                    "message": "This chat was flagged for possible cybersecurity risk. "
                                    "To get authorized for security work, join the Trusted Access for Cyber program. "
                                    "https://chatgpt.com/cyber",
                                },
                            },
                        }
                    ),
                )
            )

    async def connect(headers, access_token, account_id, **kwargs):
        if account_id == "affinity-ws":
            encryptor = TokenEncryptor()
            async with SessionLocal() as session:
                session.add(
                    Account(
                        id="affinity-authorized",
                        chatgpt_account_id="affinity-authorized",
                        email="authorized@example.invalid",
                        plan_type="plus",
                        security_work_authorized=True,
                        access_token_encrypted=encryptor.encrypt("synthetic-authorized"),
                        refresh_token_encrypted=encryptor.encrypt("synthetic-refresh"),
                        id_token_encrypted=encryptor.encrypt("synthetic-id"),
                        last_refresh=utcnow(),
                        status=AccountStatus.ACTIVE,
                    )
                )
                await session.commit()
            return SecurityRejectedUpstream()
        assert account_id == "affinity-authorized"
        return SyntheticUpstream()

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    try:
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            assert client.portal is not None
            key = client.portal.call(seed_account)
            response = client.post(
                "http://localhost/backend-api/codex/responses",
                headers={"authorization": f"Bearer {key}"},
                json={"model": "gpt-5.1", "input": "synthetic retry", "prompt_cache_key": "abc", "stream": True},
            )
            assert response.status_code == 200, response.text
            assert "response.completed" in response.text, response.text
            assert client.portal.call(app_instance.state.proxy_service.drain_persistence_tasks, 5.0)
            rows = client.get("http://localhost/api/request-logs").json()["requests"]
            assert len(rows) == 1
            assert rows[0]["stickyKeySource"] == "payload"
            assert rows[0]["stickyKind"] is None
            assert rows[0]["stickyKeyHash"] is None
    finally:
        get_settings.cache_clear()

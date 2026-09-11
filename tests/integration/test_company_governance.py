import json
from datetime import timedelta

import pytest
from aiohttp import web

from app.db.session import get_background_session
from app.modules.model_sources import governance, llmbox
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.model_source_helpers import stub_source_upstreams
from tests.integration.test_llmbox_source import create_source, install_cache

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cancellation_missing_usage_and_window_expiry(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)
    sid = (await create_source(async_client, localTokenBudget=10)).json()["id"]
    now = governance.utcnow()
    async with get_background_session() as session:
        repository = RequestLogsRepository(session)
        for index, (status, tokens, age) in enumerate(
            [
                ("success", 10, 25),
                ("cancelled", None, 0),
                ("cancelled", None, 0),
                ("cancelled", None, 0),
            ]
        ):
            await repository.add_log(
                account_id=None,
                request_id=f"cancel-{index}",
                model="company-test",
                model_source_id=sid,
                model_source_kind="llmbox",
                input_tokens=tokens,
                output_tokens=tokens,
                latency_ms=None,
                status=status,
                error_code="client_disconnected" if status == "cancelled" else None,
                requested_at=now - timedelta(hours=age),
            )
    state = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
    assert state["health"] == "unknown"
    assert state["failures"] == 0
    assert state["budgetUsed"] == 0 and state["budgetExhausted"] is False
    assert state["observedUsage"]["requestsWithoutUsage"] == 3


@pytest.mark.asyncio
async def test_stream_failure_cools_down(async_client, monkeypatch, tmp_path):
    install_cache(monkeypatch, tmp_path)

    async def upstream(request):
        return web.Response(
            text=(
                'data: {"type":"response.failed","response":{"id":"resp_fail","status":"failed",'
                '"error":{"code":"server_error","message":"failed"}}}\n\n'
            ),
            content_type="text/event-stream",
        )

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", await start(upstream))
        sid = (await create_source(async_client)).json()["id"]
        await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
        for _ in range(3):
            response = await async_client.post(
                "/v1/responses", json={"model": "company-test", "input": "hello", "stream": True}
            )
            assert "response.failed" in response.text
        response = await async_client.post(
            "/v1/responses", json={"model": "company-test", "input": "hello", "stream": True}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_source_cooling_down"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("failure,threshold", [(429, 1), (401, 1), (503, 3), (400, 0)])
async def test_company_cooldown_and_recovery(async_client, monkeypatch, tmp_path, path, failure, threshold):
    install_cache(monkeypatch, tmp_path)
    calls = []
    status = failure

    async def upstream(request):
        calls.append(request.path)
        if status != 200:
            return web.json_response({"error": {"message": "upstream refused", "type": "server_error"}}, status=status)
        if path.endswith("completions"):
            return web.json_response(
                {
                    "id": "chat_test",
                    "object": "chat.completion",
                    "model": "company-test",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                }
            )
        final = {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "model": "company-test",
            "output": [],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
        body = await request.json()
        if body.get("stream"):
            return web.Response(
                text=f"data: {json.dumps({'type': 'response.completed', 'response': final})}\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(final)

    async with stub_source_upstreams() as start:
        monkeypatch.setattr(llmbox, "LLMBOX_BASE_URL", await start(upstream))
        sid = (await create_source(async_client, supportsChatCompletions=True)).json()["id"]
        await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
        payload = (
            {"model": "company-test", "messages": [{"role": "user", "content": "test"}]}
            if path.endswith("completions")
            else {"model": "company-test", "input": "test"}
        )
        for _ in range(threshold or 4):
            result = await async_client.post(path, json=payload)
            assert result.status_code >= 400
        state = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
        if not threshold:
            assert state["health"] == "unknown"
            assert len(calls) == 4
            return
        assert state["health"] == "unavailable"
        blocked = await async_client.post(path, json=payload)
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "model_source_cooling_down"
        assert int(blocked.headers["retry-after"]) > 0
        assert len(calls) == threshold
        now = governance.utcnow()
        monkeypatch.setattr(governance, "utcnow", lambda: now + timedelta(seconds=61))
        status = 200
        recovered = await async_client.post(path, json=payload)
        assert recovered.status_code == 200, recovered.text
        state = (await async_client.get("/api/model-sources/")).json()["sources"][0]["companyStatus"]
        assert state["health"] == "healthy"
        await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": 3})
        exhausted = await async_client.post(path, json=payload)
        assert exhausted.status_code == 429
        assert exhausted.json()["error"]["code"] == "model_source_budget_exhausted"
        assert len(calls) == threshold + 1
        state = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert state["localTokenBudget"] == 3
        assert state["companyStatus"]["budgetExhausted"] is True
        await async_client.patch(f"/api/model-sources/{sid}", json={"localTokenBudget": None})
        assert (await async_client.post(path, json=payload)).status_code == 200

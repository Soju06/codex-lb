from __future__ import annotations

import app.modules.proxy.api as proxy_module


async def test_v1_embeddings_success(async_client, monkeypatch):
    async def fake_proxy_embeddings(_payload):
        return (
            200,
            {
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "index": 0,
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ],
                "model": "nomic-embed-text",
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )

    monkeypatch.setattr(proxy_module, "_proxy_embeddings", fake_proxy_embeddings)

    resp = await async_client.post(
        "/v1/embeddings",
        json={"model": "gpt-5.3-codex", "input": "ping"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["object"] == "embedding"
    assert body["data"][0]["index"] == 0


async def test_v1_embeddings_disabled_returns_405(async_client, monkeypatch):
    async def fake_proxy_embeddings(_payload):
        return (
            405,
            {
                "error": {
                    "message": "Method Not Allowed",
                    "type": "invalid_request_error",
                    "code": "invalid_request_error",
                }
            },
        )

    monkeypatch.setattr(proxy_module, "_proxy_embeddings", fake_proxy_embeddings)

    resp = await async_client.post(
        "/v1/embeddings",
        json={"model": "gpt-5.3-codex", "input": "ping"},
    )

    assert resp.status_code == 405
    body = resp.json()
    assert body["error"]["code"] == "invalid_request_error"

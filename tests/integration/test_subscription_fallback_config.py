from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _create_source(async_client, *, name: str, fallback: bool) -> str:
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": f"https://{name}.example/v1",
            "apiKey": f"token-{name}",
            "supportsChatCompletions": False,
            "supportsResponses": True,
            "isSubscriptionFallback": fallback,
            "models": [
                {
                    "model": "gpt-5.4",
                    "displayName": "gpt-5.4",
                    "supportsStreaming": True,
                    "supportsTools": True,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_updating_fallback_replaces_previous_source(async_client) -> None:
    first_id = await _create_source(async_client, name="fallback-first", fallback=True)
    second_id = await _create_source(async_client, name="fallback-second", fallback=False)

    response = await async_client.patch(
        f"/api/model-sources/{second_id}",
        json={"isSubscriptionFallback": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["isSubscriptionFallback"] is True

    listed = await async_client.get("/api/model-sources/")
    assert listed.status_code == 200
    sources = {source["id"]: source for source in listed.json()["sources"]}
    assert sources[first_id]["isSubscriptionFallback"] is False
    assert sources[second_id]["isSubscriptionFallback"] is True
    assert sum(source["isSubscriptionFallback"] is True for source in sources.values()) == 1

from __future__ import annotations

import pytest

_RESPONSES_ROUTES = ["/backend-api/codex/responses", "/v1/responses"]
_BAD_ARRAYS = [None, "abc", 123, {}]
_BAD_IDS = ["null", "string", "number", "object"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _RESPONSES_ROUTES)
@pytest.mark.parametrize("bad", _BAD_ARRAYS, ids=_BAD_IDS)
async def test_responses_routes_reject_non_array_tools_with_400(async_client, route, bad):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": "hello", "tools": bad}
    response = await async_client.post(route, json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "tools"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _BAD_ARRAYS, ids=_BAD_IDS)
async def test_chat_completions_rejects_non_array_tools_with_400(async_client, bad):
    payload = {"model": "gpt-5.1", "messages": [{"role": "user", "content": "hello"}], "tools": bad}
    response = await async_client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "tools"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses", "/v1/chat/completions"])
@pytest.mark.parametrize("bad", ["abc", 123, {}], ids=["string", "number", "object"])
async def test_compat_routes_reject_non_array_messages_with_400(async_client, route, bad):
    response = await async_client.post(route, json={"model": "gpt-5.1", "messages": bad})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "messages"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _RESPONSES_ROUTES)
async def test_responses_routes_reject_non_string_non_array_input_with_400(async_client, route):
    response = await async_client.post(route, json={"model": "gpt-5.1", "instructions": "hi", "input": 123})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "input"


@pytest.mark.asyncio
async def test_openapi_still_generates_with_passthrough_request_fields(async_client):
    response = await async_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    for model_name in ("V1ResponsesRequest", "ChatCompletionsRequest", "ResponsesCompactRequest"):
        properties = schema["components"]["schemas"][model_name]["properties"]
        assert "input" in properties, model_name
    assert "tools" in schema["components"]["schemas"]["V1ResponsesRequest"]["properties"]
    assert "messages" in schema["components"]["schemas"]["ChatCompletionsRequest"]["properties"]
    body_schema = schema["paths"]["/backend-api/codex/responses"]["post"]["requestBody"]["content"]["application/json"]
    assert body_schema["schema"]["type"] == "object"

from __future__ import annotations

import pytest


def _deep_route_cases():
    deep = {"leaf": 1}
    for _ in range(300):
        deep = [deep]
    item = [{"role": "user", "content": [{"type": "input_text", "text": "x", "n": deep}]}]
    message = [{"role": "user", "content": [{"type": "text", "text": "x", "n": deep}]}]
    tool = [{"type": "function", "name": "f", "parameters": deep}]
    chat_tool = [{"type": "function", "function": {"name": "f", "parameters": deep}}]
    text = {"format": {"type": "json_schema", "name": "n", "schema": deep}}
    hello = [{"role": "user", "content": "hi"}]
    native = {"model": "gpt-5.1", "instructions": "hi"}
    v1 = {"model": "gpt-5.1"}
    return [
        ("/backend-api/codex/responses", {**native, "input": item}, "input"),
        ("/backend-api/codex/responses", {**native, "input": "hi", "tools": tool}, "tools"),
        ("/v1/responses", {**v1, "input": item}, "input"),
        ("/v1/responses", {**v1, "messages": message}, "messages"),
        ("/v1/responses", {**v1, "input": "hi", "text": text}, "text.format.schema"),
        ("/v1/chat/completions", {**v1, "messages": message}, "messages"),
        ("/v1/chat/completions", {**v1, "messages": hello, "tools": chat_tool}, "tools"),
        ("/backend-api/codex/responses/compact", {**native, "input": item}, "input"),
    ]


_DEEP_ROUTE_CASES = _deep_route_cases()
_DEEP_ROUTE_IDS = [f"{route.rsplit('/', 1)[-1]}-{param}" for route, _payload, param in _DEEP_ROUTE_CASES]

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
@pytest.mark.parametrize(("route", "payload", "param"), _DEEP_ROUTE_CASES, ids=_DEEP_ROUTE_IDS)
async def test_deeply_nested_passthrough_fields_are_rejected_with_400(async_client, route, payload, param):
    # Past pydantic-core's ~250-level serializer limit the request used to
    # validate and then 500 on ``to_payload``; the depth guard keeps it a 400.
    response = await async_client.post(route, json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == param


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

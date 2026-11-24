import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from tests.integration.test_proxy_transient_retry import _import_account, _sse_event, _success_sse_event


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["status", "sse"])
@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("native", [False, True])
async def test_tool_continuation_quota_recovery(async_client, monkeypatch, delivery, complete, native):
    await _import_account(async_client, "tool_a", "a@example.com")
    await _import_account(async_client, "tool_b", "b@example.com")
    output = [
        {"type": "reasoning", "encrypted_content": "owner", "summary": []},
        {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": "read"},
        {"type": "custom_tool_call", "call_id": "c2", "name": "exec", "input": "read two"},
    ]
    seen = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        seen.append((account_id, payload.input))
        if len(seen) == 1:
            yield _sse_event(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_tools",
                        "output": output,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                }
            )
        elif account_id == seen[0][0]:
            if delivery == "status":
                raise ProxyResponseError(
                    429, openai_error("usage_limit_reached", "usage limit reached"), failure_phase="status"
                )
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "usage_limit_reached", "message": "usage limit reached"}},
                }
            )
        else:
            yield _success_sse_event("resp_recovered")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    payload = {
        "model": "gpt-5.6-sol",
        "instructions": "continue",
        "prompt_cache_key": "tool-replay",
        "input": [{"role": "user", "content": "retain CODE_82"}],
        "stream": True,
    }
    if native:
        payload["input"].insert(
            0,
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "description": "local",
                        "tools": [{"type": "custom", "name": "exec", "format": {"type": "text"}}],
                    }
                ],
            },
        )
        payload["input"].insert(
            1,
            {
                "type": "agent_message",
                "author": "/root/worker",
                "recipient": "/root",
                "content": [{"type": "input_text", "text": "worker evidence"}],
            },
        )
    headers = {"session_id": "tool-session"}
    first = await async_client.post("/backend-api/codex/responses", json=payload, headers=headers)
    assert first.status_code == 200
    payload["input"] += output + [
        {"type": "custom_tool_call_output", "call_id": "c1", "output": "CODE_82"},
        {"type": "custom_tool_call_output", "call_id": "c2", "output": "saved"},
    ]
    if native:
        payload["input"].append(
            {
                "type": "agent_message",
                "author": "/root/worker",
                "recipient": "/root",
                "content": [{"type": "input_text", "text": "asynchronous worker result"}],
            }
        )
        for item in payload["input"]:
            item["internal_chat_message_metadata_passthrough"] = {"turn_id": "local-client-turn"}
    if not complete:
        payload["input"] = [x for x in payload["input"] if x.get("call_id") != "c2"]
    second = await async_client.post("/backend-api/codex/responses", json=payload, headers=headers)
    # The payload is replayed verbatim regardless of its inline item shapes.
    # An upstream validation error still surfaces if the client sent an
    # incomplete tool history; the gateway must not silently drop anything.
    assert second.status_code == 200
    assert "resp_recovered" in second.text
    assert len(seen) == 3 and seen[2][0] != seen[1][0]
    assert seen[2][1] == seen[1][1]

from __future__ import annotations

import json

import pytest

from app.db.models import ModelSource, ModelSourceModel
from app.modules.model_sources.trae_protocol import TraeRequest, TraeResponsesDecoder, prepare_request


def request(custom=frozenset()):
    return TraeRequest(
        body={"config_name": "gpt-6-astra", "session_id": "session-probe"},
        custom_tools=custom,
        model="trae-astra",
        source_id="source-probe",
    )


def frame(event, data):
    return f"event: {event}\r\ndata: {json.dumps(data, ensure_ascii=False)}\r\n\r\n".encode()


def events(chunks):
    return [json.loads(line[6:]) for line in b"".join(chunks).decode().splitlines() if line.startswith("data: ")]


def test_orphan_codex_app_tool_result_is_retained_as_plain_context():
    source = ModelSource(
        id="source-probe",
        name="TRAE",
        kind="trae",
        base_url="https://copilot-cn.bytedance.net/api/ide/v2",
        is_enabled=True,
        models=[
            ModelSourceModel(
                model="trae/GPT-5.6-Luna-max",
                is_enabled=True,
                raw_metadata_json=json.dumps(
                    {"trae_config_name": "gpt-5.6-luna-max", "trae_model_name": "gpt-5.6-luna-max__dev"}
                ),
            )
        ],
    )

    bridged = prepare_request(
        source,
        {
            "model": "trae/GPT-5.6-Luna-max",
            "input": [
                {
                    "type": "function_call_output",
                    "id": "fco_retained",
                    "name": "automation_update",
                    "namespace": "codex_app",
                    "output": "saved heartbeat",
                },
                {"type": "message", "role": "user", "content": "continue"},
            ],
            "tools": [],
        },
    )

    assert bridged.body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[Retained codex_app.automation_update result]"},
                {"type": "text", "text": "saved heartbeat"},
            ],
        },
        {"role": "user", "content": [{"type": "text", "text": "continue"}]},
    ]


def test_real_text_event_shape_with_null_tools_and_fragmented_utf8():
    decoder = TraeResponsesDecoder(request())
    stream = frame("metadata", {"model": "gpt-6-astra"})
    stream += frame(
        "output", {"response": "测试", "tool_calls": None, "reasoning_content": None, "phase": "final_answer"}
    )
    stream += frame("timing_cost", {}) + frame("extra_info", {"opaque": "secret-state"})
    stream += frame("token_usage", {"prompt_tokens": 56, "completion_tokens": 14, "cache_read_input_tokens": 12})
    stream += frame("done", {"finish_reason": "stop"})
    output = events([decoder.feed(stream[i : i + 1]) for i in range(len(stream))] + [decoder.finish()])
    final = output[-1]["response"]
    assert final["status"] == "completed"
    assert final["metadata"]["trae_model"] == "gpt-6-astra"
    assert final["output"][0]["content"][0]["text"] == "测试"
    assert final["output"][0]["phase"] == "final_answer"
    assert final["usage"]["input_tokens_details"]["cached_tokens"] == 12
    assert final["output"][-1]["encrypted_content"].startswith("trae-v1:")
    assert "secret-state" not in json.dumps(output)


@pytest.mark.parametrize(
    "stream",
    [
        frame("error", {"message": "credential"}),
        frame("metadata", {"model": 123}),
        frame("output", {"response": "partial"}),
    ],
)
def test_failure_and_eof_never_complete(stream):
    decoder = TraeResponsesDecoder(request())
    output = events([decoder.feed(stream), decoder.finish()])
    assert output[-1]["type"] == "response.failed"
    assert not any(e["type"] == "response.completed" for e in output)
    assert "credential" not in json.dumps(output)


def test_custom_tool_roundtrip_shape_and_duplicate_name():
    decoder = TraeResponsesDecoder(request(frozenset({"apply_patch"})))
    call = {"index": 0, "id": "call-test", "function_call": {"name": "apply_patch", "arguments": ""}}
    chunks = [decoder.feed(frame("output", {"tool_calls": [call]}))]
    chunks.append(
        decoder.feed(
            frame(
                "output",
                {"tool_calls": [{"index": 0, "function_call": {"arguments": json.dumps({"input": "patch body"})}}]},
            )
        )
    )
    chunks.append(decoder.feed(frame("output", {"tool_calls": [call]})))
    chunks.append(decoder.feed(frame("done", {"finish_reason": "tool_calls"})))
    final = events(chunks)[-1]["response"]
    assert final["output"][0]["name"] == "apply_patch"
    assert final["output"][0]["input"] == "patch body"
    assert final["output"][0]["type"] == "custom_tool_call"


@pytest.mark.parametrize(
    "config_name,has_output,expected",
    [
        ("gemini-3-flash", True, "completed"),
        ("gemini-3.1-pro", True, "completed"),
        ("gemini-3-flash", False, "failed"),
        ("gpt-6-astra", True, "failed"),
    ],
)
def test_blank_terminal_reason_is_scoped_to_gemini_with_output(config_name, has_output, expected):
    req = request()
    req.body["config_name"] = config_name
    decoder = TraeResponsesDecoder(req)
    stream = frame("output", {"response": "marker"}) if has_output else b""
    final = events([decoder.feed(stream + frame("done", {"finish_reason": ""}))])[-1]["response"]
    assert final["status"] == expected


def test_gemini_uses_current_traecli_header_profile(monkeypatch):
    from app.modules.model_sources import trae

    monkeypatch.setattr(trae, "auth_headers", lambda: {"Authorization": "Cloud-CLI-JWT test"})
    assert trae.request_headers("gemini-3-flash")["x-ide-function"] == "traecli_next"
    assert trae.request_headers("gpt-6-astra")["x-ide-function"] == "solo_agent"

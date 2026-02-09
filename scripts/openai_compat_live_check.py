from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, cast

import openai

BASE_URL = os.environ.get("CODEX_BASE_URL", "http://localhost:2455/v1")
API_KEY = os.environ.get("CODEX_API_KEY", "sk-local")
MODEL_OVERRIDE = os.environ.get("CODEX_MODEL")

FILE_URL = os.environ.get(
    "CODEX_TEST_FILE_URL",
    "https://www.berkshirehathaway.com/letters/2024ltr.pdf",
)
FILE_ID = os.environ.get("CODEX_TEST_FILE_ID", "file-unknown")
VECTOR_STORE_ID = os.environ.get("CODEX_VECTOR_STORE_ID")
IMAGE_URL = os.environ.get(
    "CODEX_TEST_IMAGE_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/"
    "Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-"
    "madison-the-nature-boardwalk.jpg",
)
AUDIO_B64 = os.environ.get("CODEX_TEST_AUDIO_B64")
AUDIO_FORMAT = os.environ.get("CODEX_TEST_AUDIO_FORMAT", "wav")
CHAT_FILE_ID = os.environ.get("CODEX_TEST_CHAT_FILE_ID", "file-unknown")

RUN_RESPONSES = os.environ.get("CODEX_RUN_RESPONSES", "1") != "0"
RUN_CHAT = os.environ.get("CODEX_RUN_CHAT", "1") != "0"
RUN_TOOLS = os.environ.get("CODEX_RUN_TOOLS", "1") != "0"
RUN_REASONING = os.environ.get("CODEX_RUN_REASONING", "1") != "0"

DEFAULT_TEXT = "Return 'ok' only."

EXPECTED_UNSUPPORTED = [
    "R.file_input_id",
    "R.previous_response_id",
    "R.truncation_auto",
    "T.file_search",
    "T.code_interpreter",
    "T.computer_use",
    "T.image_generation",
    "T.tool_choice_required",
    "C.audio_input",
    "C.file_input_id",
]


class CaseSkipped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CaseError(Exception):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(detail.get("message", "case error"))
        self.detail = detail


@dataclass
class CaseResult:
    name: str
    status: str
    detail: dict[str, Any]


def _error_detail(exc: Exception) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "type": exc.__class__.__name__,
        "message": str(exc),
    }
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        detail["status_code"] = status_code
    body = getattr(exc, "body", None)
    if body is not None:
        detail["body"] = body
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail["response_json"] = response.json()
        except Exception:
            try:
                detail["response_text"] = response.text
            except Exception:
                pass
    return detail


def run_case(name: str, fn: Callable[[], Any]) -> CaseResult:
    try:
        result = fn()
        return CaseResult(name=name, status="ok", detail={"result": result})
    except CaseSkipped as exc:
        return CaseResult(name=name, status="skipped", detail={"reason": exc.reason})
    except CaseError as exc:
        return CaseResult(name=name, status="error", detail=exc.detail)
    except Exception as exc:
        return CaseResult(name=name, status="error", detail=_error_detail(exc))


def _pick_model(client: openai.OpenAI) -> str:
    if MODEL_OVERRIDE:
        return MODEL_OVERRIDE
    models = client.models.list()
    if models.data:
        return models.data[0].id
    raise RuntimeError("No models returned from /v1/models")


def _input_file_payload(file_ref: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Summarize this file in one sentence."},
                {"type": "input_file", **file_ref},
            ],
        }
    ]


def _input_text_payload(text: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
    ]


def _build_wav_base64(duration_ms: int = 100, sample_rate: int = 8000) -> str:
    num_channels = 1
    sample_width = 2
    num_frames = int(sample_rate * duration_ms / 1000)
    data = b"\x00\x00" * num_frames
    byte_rate = sample_rate * num_channels * sample_width
    block_align = num_channels * sample_width
    riff_size = 36 + len(data)
    header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
    fmt = b"fmt " + struct.pack(
        "<IHHIIHH",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,
    )
    data_chunk = b"data" + struct.pack("<I", len(data)) + data
    wav_bytes = header + fmt + data_chunk
    return base64.b64encode(wav_bytes).decode("ascii")


def _response_stream_summary(stream: Any) -> dict[str, Any]:
    event_types: list[str] = []
    output_text = ""
    for event in stream:
        event_type = getattr(event, "type", None)
        if isinstance(event_type, str):
            event_types.append(event_type)
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if isinstance(delta, str):
                output_text += delta
    return {
        "event_types": event_types,
        "event_count": len(event_types),
        "output_text": output_text,
    }


def _chat_stream_summary(stream: Any) -> dict[str, Any]:
    output_text = ""
    chunk_count = 0
    for chunk in stream:
        chunk_count += 1
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            output_text += delta.content
    return {"chunk_count": chunk_count, "output_text": output_text}


def main() -> int:
    client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        model = _pick_model(client)
    except Exception as exc:
        print("Failed to list models:", exc)
        return 2

    audio_b64 = AUDIO_B64 or _build_wav_base64()

    print("Base URL:", BASE_URL)
    print("Model:", model)
    print("File URL:", FILE_URL)
    print("File ID:", FILE_ID)
    print("Vector Store ID:", VECTOR_STORE_ID or "(missing)")
    print("Image URL:", IMAGE_URL)
    print("Audio format:", AUDIO_FORMAT)
    print("Chat file ID:", CHAT_FILE_ID)
    print("Expected unsupported:", ", ".join(EXPECTED_UNSUPPORTED))

    results: list[CaseResult] = []

    cases: list[tuple[str, Callable[[], Any]]] = []

    if RUN_RESPONSES:

        def r_text_input_string() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=DEFAULT_TEXT)
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_text_input_list() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_text_payload(DEFAULT_TEXT)))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_image_input_url() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(
                    Any,
                    [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Describe the image."},
                                {"type": "input_image", "image_url": IMAGE_URL},
                            ],
                        }
                    ],
                ),
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_file_input_url() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_file_payload({"file_url": FILE_URL})))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_file_input_id() -> dict[str, Any]:
            resp = client.responses.create(model=model, input=cast(Any, _input_file_payload({"file_id": FILE_ID})))
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_streaming() -> dict[str, Any]:
            stream = client.responses.create(
                model=model, input=cast(Any, _input_text_payload("Stream ok")), stream=True
            )
            return _response_stream_summary(stream)

        def r_previous_response_id() -> dict[str, Any]:
            first = client.responses.create(model=model, input=cast(Any, _input_text_payload(DEFAULT_TEXT)))
            first_id = getattr(first, "id", None)
            if not first_id:
                raise CaseError({"message": "missing response id from first call"})
            try:
                second = client.responses.create(
                    model=model,
                    input=cast(Any, _input_text_payload("Repeat exactly: ok")),
                    previous_response_id=first_id,
                )
            except Exception as exc:
                detail = _error_detail(exc)
                detail["first_id"] = first_id
                raise CaseError(detail) from exc
            return {
                "first_id": first_id,
                "second_id": getattr(second, "id", None),
                "second_output_text": getattr(second, "output_text", None),
            }

        def r_truncation_auto() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Short reply.")),
                truncation="auto",
            )
            return {"response_id": getattr(resp, "id", None)}

        def r_text_format_json_object() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload('Return {"ok": true} as JSON.')),
                text={"format": {"type": "json_object"}},
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_include_logprobs() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Return ok.")),
                include=["message.output_text.logprobs"],
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("R.text_input_string", r_text_input_string),
                ("R.text_input_list", r_text_input_list),
                ("R.image_input_url", r_image_input_url),
                ("R.file_input_url", r_file_input_url),
                ("R.file_input_id", r_file_input_id),
                ("R.streaming", r_streaming),
                ("R.previous_response_id", r_previous_response_id),
                ("R.truncation_auto", r_truncation_auto),
                ("R.text_format_json_object", r_text_format_json_object),
                ("R.include_logprobs", r_include_logprobs),
            ]
        )

    if RUN_REASONING:

        def r_reasoning_effort_low() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Solve 1+1.")),
                reasoning={"effort": "low"},
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def r_reasoning_summary_detailed() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Explain briefly.")),
                reasoning={"summary": "detailed"},
            )
            return {"response_id": getattr(resp, "id", None)}

        def r_reasoning_encrypted_content() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Reason about 2+2.")),
                include=["reasoning.encrypted_content"],
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("R.reasoning_effort_low", r_reasoning_effort_low),
                ("R.reasoning_summary_detailed", r_reasoning_summary_detailed),
                ("R.reasoning_encrypted_content", r_reasoning_encrypted_content),
            ]
        )

    if RUN_TOOLS:

        def t_web_search() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("What was a positive news story today?")),
                tools=[{"type": "web_search"}],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_file_search() -> dict[str, Any]:
            vector_store_id = VECTOR_STORE_ID or "vs_dummy"
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Find notes about onboarding.")),
                tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
                include=["file_search_call.results"],
            )
            return {
                "response_id": getattr(resp, "id", None),
                "output_text": getattr(resp, "output_text", None),
                "vector_store_id": vector_store_id,
                "used_dummy_vector_store_id": VECTOR_STORE_ID is None,
            }

        def t_code_interpreter() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Compute 2+2 and return only the number.")),
                tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_computer_use() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Open example.com and report the page title.")),
                tools=[
                    {
                        "type": "computer_use_preview",
                        "display_width": 1024,
                        "display_height": 768,
                        "environment": "browser",
                    }
                ],
            )
            return {"response_id": getattr(resp, "id", None), "output_text": getattr(resp, "output_text", None)}

        def t_image_generation() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Generate a small red circle on white background.")),
                tools=[{"type": "image_generation"}],
            )
            return {"response_id": getattr(resp, "id", None)}

        def t_tool_choice_required() -> dict[str, Any]:
            resp = client.responses.create(
                model=model,
                input=cast(Any, _input_text_payload("Draw a red dot.")),
                tools=[{"type": "image_generation"}],
                tool_choice="required",
            )
            return {"response_id": getattr(resp, "id", None)}

        cases.extend(
            [
                ("T.web_search", t_web_search),
                ("T.file_search", t_file_search),
                ("T.code_interpreter", t_code_interpreter),
                ("T.computer_use", t_computer_use),
                ("T.image_generation", t_image_generation),
                ("T.tool_choice_required", t_tool_choice_required),
            ]
        )

    if RUN_CHAT:

        def c_text() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": DEFAULT_TEXT}],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_image_url() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe the image."},
                            {"type": "image_url", "image_url": {"url": IMAGE_URL}},
                        ],
                    }
                ],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_audio_input() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Transcribe the audio."},
                            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": AUDIO_FORMAT}},
                        ],
                    }
                ],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_file_input() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Summarize the file."},
                            {"type": "file", "file": {"file_url": FILE_URL}},
                        ],
                    }
                ],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_file_input_id() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Summarize the file."},
                            {"type": "file", "file": {"file_id": CHAT_FILE_ID}},
                        ],
                    }
                ],
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_tools_function() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "What is the weather in Boston?"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_current_weather",
                            "description": "Get the current weather",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "location": {"type": "string"},
                                    "unit": {"type": "string", "enum": ["c", "f"]},
                                },
                                "required": ["location"],
                            },
                        },
                    }
                ],
                tool_choice="auto",
            )
            return {"id": getattr(resp, "id", None), "finish_reason": resp.choices[0].finish_reason}

        def c_response_format_json_object() -> dict[str, Any]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": 'Return {"ok": true} as JSON.'}],
                response_format={"type": "json_object"},
            )
            return {"id": getattr(resp, "id", None), "content": resp.choices[0].message.content}

        def c_streaming() -> dict[str, Any]:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Stream ok"}],
                stream=True,
            )
            return _chat_stream_summary(stream)

        cases.extend(
            [
                ("C.text", c_text),
                ("C.image_url", c_image_url),
                ("C.audio_input", c_audio_input),
                ("C.file_input", c_file_input),
                ("C.file_input_id", c_file_input_id),
                ("C.tools_function", c_tools_function),
                ("C.response_format_json_object", c_response_format_json_object),
                ("C.streaming", c_streaming),
            ]
        )

    for name, fn in cases:
        result = run_case(name, fn)
        results.append(result)
        status = result.status.upper()
        summary = result.detail.get("reason") or result.detail.get("message") or ""
        print(f"- {name}: {status} {summary}".rstrip())

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE_URL,
        "model": model,
        "expected_unsupported": EXPECTED_UNSUPPORTED,
        "results": [asdict(item) for item in results],
    }

    os.makedirs("refs", exist_ok=True)
    with open("refs/openai-compat-live-results.json", "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=True, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

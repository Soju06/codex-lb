"""Unit tests for the OpenAI Images <-> Responses translation layer."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import pytest

from app.core.openai.images import V1ImageResponse, V1ImagesEditsForm, V1ImagesGenerationsRequest
from app.core.types import JsonValue
from app.modules.proxy import images_service


def _tool(responses: Any, index: int = 0) -> Mapping[str, JsonValue]:
    """Return ``responses.tools[index]`` as a typed mapping for ``ty``.

    The Responses request models tools as ``list[JsonValue]``; ``ty`` cannot
    narrow to a mapping for chained subscripting in tests, so we cast at the
    one place we use this access pattern.
    """
    return cast(Mapping[str, JsonValue], responses.tools[index])


def _input_msg(responses: Any, index: int = 0) -> Mapping[str, JsonValue]:
    """Return ``responses.input[index]`` as a typed mapping for ``ty``."""
    return cast(Mapping[str, JsonValue], responses.input[index])


def _content_list(message: Mapping[str, JsonValue]) -> list[JsonValue]:
    """Return ``message['content']`` as a typed list for ``ty``."""
    return cast(list[JsonValue], message["content"])


def _content_part(message: Mapping[str, JsonValue], index: int) -> Mapping[str, JsonValue]:
    """Return one content part as a typed mapping for ``ty``."""
    return cast(Mapping[str, JsonValue], _content_list(message)[index])


def _image_response(result: Any) -> V1ImageResponse:
    """Narrow ``images_response_from_responses`` to ``V1ImageResponse`` for tests."""
    assert isinstance(result, V1ImageResponse)
    return result


def _as_mapping(value: Any) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], value)


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------


class TestImagesGenerationToResponsesRequest:
    def test_minimal_generation_payload(self) -> None:
        payload = V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-2", "prompt": "tiny red circle"})
        responses = images_service.images_generation_to_responses_request(payload, host_model="gpt-5.5")
        assert responses.model == "gpt-5.5"
        assert responses.store is False
        # The internal Responses request is always streamed because the
        # upstream backend rejects non-streaming requests that include the
        # image_generation tool. Public non-streaming clients are served by
        # draining the upstream stream into a JSON envelope.
        assert responses.stream is True
        assert "image generator" in responses.instructions
        assert isinstance(responses.input, list)
        assert _input_msg(responses)["role"] == "user"
        assert _content_list(_input_msg(responses)) == [{"type": "input_text", "text": "tiny red circle"}]
        assert len(responses.tools) == 1
        tool = _tool(responses)
        assert tool["type"] == "image_generation"
        assert tool["model"] == "gpt-image-2"
        # ``n`` is intentionally not forwarded into the tool config because
        # the upstream image_generation tool does not accept it.
        assert "n" not in tool
        assert tool["size"] == "auto"
        assert tool["quality"] == "auto"
        assert tool["background"] == "auto"
        assert tool["output_format"] == "png"
        assert tool["output_compression"] == 100
        assert tool["moderation"] == "auto"
        # partial_images is omitted on non-streaming requests.
        assert "partial_images" not in tool
        assert "input_fidelity" not in tool

    def test_stream_with_partial_images_passes_through(self) -> None:
        payload = V1ImagesGenerationsRequest.model_validate(
            {
                "model": "gpt-image-2",
                "prompt": "moon",
                "stream": True,
                "partial_images": 2,
                "size": "1024x1024",
                "quality": "low",
                "n": 1,
            }
        )
        responses = images_service.images_generation_to_responses_request(payload, host_model="gpt-5.5")
        assert responses.stream is True
        tool = _tool(responses)
        assert tool["partial_images"] == 2
        # ``n`` is dropped from the tool config; see the docstring on
        # _build_image_generation_tool. The Images-API layer enforces n bounds.
        assert "n" not in tool
        assert tool["size"] == "1024x1024"
        assert tool["quality"] == "low"

    def test_partial_images_omitted_when_not_streaming(self) -> None:
        payload = V1ImagesGenerationsRequest.model_validate(
            {
                "model": "gpt-image-2",
                "prompt": "moon",
                "stream": False,
                "partial_images": 2,
            }
        )
        responses = images_service.images_generation_to_responses_request(payload, host_model="gpt-5.5")
        assert "partial_images" not in _tool(responses)

    def test_host_model_replaces_public_model(self) -> None:
        payload = V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-2", "prompt": "blue square"})
        responses = images_service.images_generation_to_responses_request(payload, host_model="gpt-5.5")
        # The public model only appears in tools[0].model — never on the
        # outer Responses ``model`` field.
        assert responses.model == "gpt-5.5"
        assert _tool(responses)["model"] == "gpt-image-2"


class TestImagesEditToResponsesRequest:
    def test_single_image_edit_payload(self) -> None:
        form = V1ImagesEditsForm.model_validate(
            {"model": "gpt-image-1", "prompt": "make it green", "size": "1024x1024"}
        )
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        responses = images_service.images_edit_to_responses_request(
            form,
            host_model="gpt-5.5",
            images=[(png_bytes, "image/png")],
            mask=None,
        )
        # Exactly one input message containing the prompt and one input_image
        # data URL.
        assert len(cast(list[JsonValue], responses.input)) == 1
        content = _content_list(_input_msg(responses))
        assert content[0] == {"type": "input_text", "text": "make it green"}
        image_part = _as_mapping(content[1])
        assert image_part["type"] == "input_image"
        image_url = cast(str, image_part["image_url"])
        assert image_url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(image_url.split(",", 1)[1])
        assert decoded == png_bytes

    def test_mask_is_appended_with_hint_in_prompt(self) -> None:
        form = V1ImagesEditsForm.model_validate({"model": "gpt-image-1", "prompt": "edit this", "size": "1024x1024"})
        responses = images_service.images_edit_to_responses_request(
            form,
            host_model="gpt-5.5",
            images=[(b"image-bytes", "image/png")],
            mask=(b"mask-bytes", "image/png"),
        )
        content = _content_list(_input_msg(responses))
        # Prompt picks up the mask hint.
        first_part = _as_mapping(content[0])
        assert first_part["type"] == "input_text"
        text_value = cast(str, first_part["text"])
        assert "edit this" in text_value
        assert "mask" in text_value.lower()
        # Two input_image parts: source + mask.
        image_parts = [
            part for part in (cast(Mapping[str, JsonValue], p) for p in content) if part.get("type") == "input_image"
        ]
        assert len(image_parts) == 2

    def test_no_images_raises(self) -> None:
        form = V1ImagesEditsForm.model_validate({"model": "gpt-image-1", "prompt": "edit"})
        with pytest.raises(ValueError):
            images_service.images_edit_to_responses_request(
                form,
                host_model="gpt-5.5",
                images=[],
                mask=None,
            )

    def test_input_fidelity_passes_through_to_tool(self) -> None:
        form = V1ImagesEditsForm.model_validate(
            {
                "model": "gpt-image-1",
                "prompt": "edit",
                "size": "1024x1024",
                "input_fidelity": "high",
            }
        )
        responses = images_service.images_edit_to_responses_request(
            form,
            host_model="gpt-5.5",
            images=[(b"data", "image/png")],
            mask=None,
        )
        assert _tool(responses)["input_fidelity"] == "high"


# ---------------------------------------------------------------------------
# Non-streaming response translation
# ---------------------------------------------------------------------------


class TestImagesResponseFromResponses:
    def test_single_image_extracted(self) -> None:
        upstream = {
            "id": "resp_abc",
            "status": "completed",
            "output": [
                {
                    "type": "image_generation_call",
                    "id": "ig_1",
                    "status": "completed",
                    "result": "BASE64DATA==",
                    "revised_prompt": "a tiny red circle on white",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                }
            ],
            "tool_usage": {"image_gen": {"input_tokens": 12, "output_tokens": 84}},
        }
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        response = _image_response(result)
        assert len(response.data) == 1
        assert response.data[0].b64_json == "BASE64DATA=="
        assert response.data[0].revised_prompt == "a tiny red circle on white"
        assert response.usage is not None
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 84
        assert response.usage.total_tokens == 96

    def test_multiple_images_in_output(self) -> None:
        upstream = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": "thinking"},
                {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": "AAAA",
                },
                {
                    "type": "image_generation_call",
                    "status": "completed",
                    "result": "BBBB",
                    "revised_prompt": "second image",
                },
                {"type": "message", "role": "assistant", "content": []},
            ],
        }
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        response = _image_response(result)
        assert [d.b64_json for d in response.data] == ["AAAA", "BBBB"]
        assert response.data[0].revised_prompt is None
        assert response.data[1].revised_prompt == "second image"

    def test_failed_image_returns_error_envelope(self) -> None:
        upstream = {
            "status": "completed",
            "output": [
                {
                    "type": "image_generation_call",
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "not allowed",
                        "type": "invalid_request_error",
                    },
                }
            ],
        }
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        assert isinstance(result, dict)
        assert result["error"]["code"] == "content_policy_violation"
        assert result["error"]["message"] == "not allowed"

    def test_no_image_items_returns_error(self) -> None:
        upstream = {"status": "completed", "output": []}
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        assert isinstance(result, dict)
        assert result["error"]["code"] == "image_generation_failed"

    def test_empty_result_returns_error(self) -> None:
        upstream = {
            "status": "completed",
            "output": [{"type": "image_generation_call", "status": "completed", "result": ""}],
        }
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        assert isinstance(result, dict)
        assert result["error"]["code"] == "image_generation_failed"

    def test_missing_output_returns_error(self) -> None:
        upstream = {"status": "completed"}
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        assert isinstance(result, dict)

    def test_partial_usage_falls_through(self) -> None:
        upstream = {
            "status": "completed",
            "output": [{"type": "image_generation_call", "status": "completed", "result": "AA"}],
            "tool_usage": {"image_gen": {"input_tokens": 5}},
        }
        result = images_service.images_response_from_responses(_as_mapping(upstream))
        response = _image_response(result)
        assert response.usage is not None
        assert response.usage.input_tokens == 5
        assert response.usage.output_tokens is None
        assert response.usage.total_tokens is None


# ---------------------------------------------------------------------------
# SSE translation
# ---------------------------------------------------------------------------


def _sse(payload: dict[str, object]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"


async def _stream(events: list[str]) -> AsyncIterator[str]:
    for event in events:
        yield event


def _events_from_translated_stream(blocks: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in blocks:
        if not block.strip():
            continue
        if block.strip() == "data: [DONE]":
            events.append({"type": "[DONE]"})
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                break
    return events


class TestTranslateResponsesStreamToImagesStream:
    @pytest.mark.asyncio
    async def test_partial_then_completed_produces_canonical_events(self) -> None:
        upstream_events = [
            _sse({"type": "response.created", "response": {"id": "resp_x"}}),
            _sse({"type": "response.in_progress"}),
            _sse({"type": "response.output_item.added", "item": {"type": "reasoning"}}),
            _sse({"type": "response.output_item.done", "item": {"type": "reasoning", "summary": "s"}}),
            _sse({"type": "response.image_generation_call.in_progress"}),
            _sse({"type": "response.image_generation_call.generating"}),
            _sse(
                {
                    "type": "response.image_generation_call.partial_image",
                    "partial_image_b64": "PARTIAL_0",
                    "partial_image_index": 0,
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                    "output_index": 1,
                    "sequence_number": 7,
                    "item_id": "ig_1",
                }
            ),
            _sse(
                {
                    "type": "response.image_generation_call.partial_image",
                    "partial_image_b64": "PARTIAL_1",
                    "partial_image_index": 1,
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                    "output_index": 1,
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_1",
                        "status": "completed",
                        "result": "FINAL_B64",
                        "revised_prompt": "tiny red circle",
                        "size": "1024x1024",
                        "quality": "low",
                        "background": "auto",
                        "output_format": "png",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_x", "tool_usage": {"image_gen": {"input_tokens": 1, "output_tokens": 2}}},
                }
            ),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        types = [e["type"] for e in events]
        assert types == [
            "image_generation.partial_image",
            "image_generation.partial_image",
            "image_generation.completed",
            "[DONE]",
        ]
        assert events[0]["b64_json"] == "PARTIAL_0"
        assert events[0]["partial_image_index"] == 0
        assert events[0]["size"] == "1024x1024"
        # Internal-only fields are dropped.
        assert "sequence_number" not in events[0]
        assert "item_id" not in events[0]
        assert events[1]["b64_json"] == "PARTIAL_1"
        assert events[1]["partial_image_index"] == 1
        assert events[2]["b64_json"] == "FINAL_B64"
        assert events[2]["revised_prompt"] == "tiny red circle"
        # ``usage`` is attached only on the final completed event from the
        # trailing ``response.completed`` payload (the ``output_item.done``
        # event itself does not carry ``tool_usage``).
        assert events[2]["usage"]["input_tokens"] == 1
        assert events[2]["usage"]["output_tokens"] == 2

    @pytest.mark.asyncio
    async def test_response_failed_yields_single_error_event(self) -> None:
        upstream_events = [
            _sse({"type": "response.created", "response": {"id": "resp_y"}}),
            _sse(
                {
                    "type": "response.failed",
                    "response": {
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "too many",
                            "type": "rate_limit_error",
                        }
                    },
                }
            ),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        assert [e["type"] for e in events] == ["error", "[DONE]"]
        assert events[0]["error"]["code"] == "rate_limit_exceeded"
        assert events[0]["error"]["message"] == "too many"

    @pytest.mark.asyncio
    async def test_failed_image_generation_call_yields_error(self) -> None:
        upstream_events = [
            _sse(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "image_generation_call",
                        "status": "failed",
                        "error": {
                            "code": "content_policy_violation",
                            "message": "blocked",
                            "type": "invalid_request_error",
                        },
                    },
                }
            ),
            _sse({"type": "response.completed", "response": {}}),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        assert [e["type"] for e in events] == ["error", "[DONE]"]
        assert events[0]["error"]["code"] == "content_policy_violation"

    @pytest.mark.asyncio
    async def test_truncated_stream_emits_synthetic_error(self) -> None:
        # No terminal event from upstream.
        upstream_events: list[str] = []
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        assert [e["type"] for e in events] == ["error", "[DONE]"]
        assert events[0]["error"]["code"] == "image_generation_failed"

    @pytest.mark.asyncio
    async def test_error_event_passes_through(self) -> None:
        upstream_events = [
            _sse(
                {
                    "type": "error",
                    "error": {
                        "code": "server_error",
                        "message": "boom",
                        "type": "server_error",
                    },
                }
            ),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        assert [e["type"] for e in events] == ["error", "[DONE]"]
        assert events[0]["error"]["code"] == "server_error"


# ---------------------------------------------------------------------------
# Stream -> non-streaming collector
# ---------------------------------------------------------------------------


class TestCollectResponsesStreamForImages:
    @pytest.mark.asyncio
    async def test_completed_returns_response_with_merged_output(self) -> None:
        upstream_events = [
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "status": "completed",
                        "result": "ABC",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_z",
                        "tool_usage": {"image_gen": {"input_tokens": 4, "output_tokens": 6}},
                    },
                }
            ),
        ]
        response, error = await images_service.collect_responses_stream_for_images(_stream(upstream_events))
        assert error is None
        assert response is not None
        assert response["id"] == "resp_z"
        output = cast(list[JsonValue], response["output"])
        first_item = cast(Mapping[str, JsonValue], output[0])
        assert first_item["result"] == "ABC"
        tool_usage = cast(Mapping[str, JsonValue], response["tool_usage"])
        image_gen = cast(Mapping[str, JsonValue], tool_usage["image_gen"])
        assert image_gen["output_tokens"] == 6

    @pytest.mark.asyncio
    async def test_response_incomplete_returns_error_envelope(self) -> None:
        """``response.incomplete`` must surface as an error in non-streaming
        collect, matching the streaming translator's behaviour. Returning a
        200 image envelope on a half-finished response would silently
        promote partial output to success.
        """
        upstream_events = [
            _sse(
                {
                    "type": "response.incomplete",
                    "response": {"id": "resp_inc", "status": "incomplete"},
                }
            ),
        ]
        response, error = await images_service.collect_responses_stream_for_images(_stream(upstream_events))
        assert response is None
        assert error is not None
        assert error["error"]["code"] == "image_generation_failed"

    @pytest.mark.asyncio
    async def test_late_failed_after_completed_is_ignored(self) -> None:
        """Once ``response.completed`` is captured, a trailing
        ``response.failed`` (e.g. transport-level error during cleanup)
        must NOT invalidate the already-received image result.
        """
        upstream_events = [
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_late",
                        "status": "completed",
                        "result": "GOODB64",
                    },
                }
            ),
            _sse({"type": "response.completed", "response": {"id": "resp_late"}}),
            _sse(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "transport_error", "message": "late"}},
                }
            ),
        ]
        response, error = await images_service.collect_responses_stream_for_images(_stream(upstream_events))
        assert error is None
        assert response is not None
        assert response["id"] == "resp_late"

    @pytest.mark.asyncio
    async def test_late_error_event_after_completed_is_ignored(self) -> None:
        upstream_events = [
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_late",
                        "status": "completed",
                        "result": "GOODB64",
                    },
                }
            ),
            _sse({"type": "response.completed", "response": {"id": "resp_late"}}),
            _sse({"type": "error", "error": {"code": "transport_error", "message": "late"}}),
        ]
        response, error = await images_service.collect_responses_stream_for_images(_stream(upstream_events))
        assert error is None
        assert response is not None

    @pytest.mark.asyncio
    async def test_response_failed_returns_error_envelope(self) -> None:
        upstream_events = [
            _sse(
                {
                    "type": "response.failed",
                    "response": {
                        "error": {
                            "code": "rate_limit_exceeded",
                            "message": "slow down",
                            "type": "rate_limit_error",
                        }
                    },
                }
            ),
        ]
        response, error = await images_service.collect_responses_stream_for_images(_stream(upstream_events))
        assert response is None
        assert error is not None
        assert error["error"]["code"] == "rate_limit_exceeded"

    @pytest.mark.asyncio
    async def test_truncated_stream_returns_error(self) -> None:
        response, error = await images_service.collect_responses_stream_for_images(_stream([]))
        assert response is None
        assert error is not None
        assert error["error"]["code"] == "image_generation_failed"

    @pytest.mark.asyncio
    async def test_captured_response_id_populated_during_collect(self) -> None:
        upstream_events = [
            _sse({"type": "response.created", "response": {"id": "resp_collect_id"}}),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_xx",
                        "status": "completed",
                        "result": "AAAA",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_collect_id"},
                }
            ),
        ]
        captured: dict[str, str] = {}
        response, error = await images_service.collect_responses_stream_for_images(
            _stream(upstream_events), captured=captured
        )
        assert error is None
        assert response is not None
        assert captured.get("response_id") == "resp_collect_id"


class TestStreamingMultiImage:
    @pytest.mark.asyncio
    async def test_multiple_completed_image_items_are_all_emitted(self) -> None:
        """When upstream emits multiple ``image_generation_call`` completions in
        a single response, the translator must forward every one to the client
        in arrival order. Only the last completion carries ``usage`` to match
        the canonical OpenAI Images streaming shape.
        """
        upstream_events = [
            _sse({"type": "response.created", "response": {"id": "resp_multi"}}),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_first",
                        "status": "completed",
                        "result": "FIRST_B64",
                        "size": "1024x1024",
                        "quality": "low",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 1,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_second",
                        "status": "completed",
                        "result": "SECOND_B64",
                        "size": "1024x1024",
                        "quality": "low",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_multi",
                        "tool_usage": {"image_gen": {"input_tokens": 3, "output_tokens": 11}},
                    },
                }
            ),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(_stream(upstream_events))
        ]
        events = _events_from_translated_stream(translated_blocks)
        completed = [e for e in events if e["type"] == "image_generation.completed"]
        assert [e["b64_json"] for e in completed] == ["FIRST_B64", "SECOND_B64"]
        # Only the final completion carries usage.
        assert "usage" not in completed[0]
        assert completed[1]["usage"]["input_tokens"] == 3
        assert completed[1]["usage"]["output_tokens"] == 11


class TestStreamingCapturesResponseId:
    @pytest.mark.asyncio
    async def test_translate_populates_captured_response_id(self) -> None:
        upstream_events = [
            _sse({"type": "response.created", "response": {"id": "resp_stream_id"}}),
            _sse(
                {
                    "type": "response.image_generation_call.partial_image",
                    "item_id": "ig_zz",
                    "output_index": 0,
                    "partial_image_b64": "AAAA",
                    "partial_image_index": 0,
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "opaque",
                    "output_format": "png",
                    "sequence_number": 1,
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_zz",
                        "status": "completed",
                        "result": "BBBB",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_stream_id", "tool_usage": {"image_gen": {"input_tokens": 1}}},
                }
            ),
        ]
        captured: dict[str, str] = {}
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(
                _stream(upstream_events), captured=captured
            )
        ]
        # translator must reach a terminal event before captured is read by
        # the caller; the response_id is captured on the first event whose
        # ``response.id`` is present (typically ``response.created``).
        assert captured.get("response_id") == "resp_stream_id"
        events = _events_from_translated_stream(translated_blocks)
        assert any(e["type"] == "image_generation.completed" for e in events)


# ---------------------------------------------------------------------------
# Misc utilities
# ---------------------------------------------------------------------------


class TestDecodeDataUrl:
    def test_round_trip(self) -> None:
        raw = b"hello world"
        encoded = base64.b64encode(raw).decode("ascii")
        decoded, mime = images_service.decode_data_url(f"data:image/png;base64,{encoded}")
        assert decoded == raw
        assert mime == "image/png"

    def test_rejects_non_data_url(self) -> None:
        with pytest.raises(ValueError):
            images_service.decode_data_url("https://example.com/x.png")

    def test_rejects_invalid_base64(self) -> None:
        with pytest.raises(ValueError):
            images_service.decode_data_url("data:image/png;base64,!!!")


class TestStreamingEditEventNames:
    @pytest.mark.asyncio
    async def test_translator_emits_image_edit_events_when_is_edit_true(self) -> None:
        """``/v1/images/edits`` callers must receive ``image_edit.*`` events,
        not ``image_generation.*``, to match the canonical OpenAI Images
        streaming vocabulary that SDKs listen for.
        """
        upstream_events = [
            _sse(
                {
                    "type": "response.image_generation_call.partial_image",
                    "item_id": "ig_e",
                    "output_index": 0,
                    "partial_image_b64": "EDITPARTIAL",
                    "partial_image_index": 0,
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_e",
                        "status": "completed",
                        "result": "EDITFINAL",
                    },
                }
            ),
            _sse({"type": "response.completed", "response": {"id": "resp_e"}}),
        ]
        translated_blocks = [
            block
            async for block in images_service.translate_responses_stream_to_images_stream(
                _stream(upstream_events), is_edit=True
            )
        ]
        events = _events_from_translated_stream(translated_blocks)
        types = [e["type"] for e in events]
        assert "image_edit.partial_image" in types
        assert "image_edit.completed" in types
        # Must NOT emit the generation event names on the edits path.
        assert not any(t.startswith("image_generation.") for t in types)

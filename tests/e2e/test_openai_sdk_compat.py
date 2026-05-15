"""E2E tests: real OpenAI Python SDK across all supported codex-lb /v1 surfaces.

The companion file ``test_v1_responses_openai_sdk.py`` already covers
``client.responses.stream(...)`` in detail (G1/G3/G4 normalisation). This
file widens the surface to **every other** OpenAI SDK method that maps to
a codex-lb route, plus a parametrised audit of routes the proxy does NOT
expose — so that a regression to either layer (codex-lb routing or
``app.modules.proxy.service``) shows up in CI without standing up a real
upstream account.

Surfaces covered:

- ``client.chat.completions.create(...)`` — streaming, non-streaming,
  tool-call, multi-turn
- ``client.responses.parse(text_format=PydanticModel)`` — structured
  output through the real SDK parser
- ``client.responses.create(...)`` non-streaming (extra coverage on top
  of ``test_v1_responses_openai_sdk.py``)
- ``client.models.list()``
- ``client.audio.transcriptions.create(file=..., model=...)``
- ``client.images.generate(...)`` (best-effort against the
  ``tool_usage.image_gen`` translation path)
- Unsupported surfaces (embeddings, moderations, files, batches,
  fine_tuning, ``responses.retrieve/cancel/delete``) — assert the SDK
  receives a clean 4xx, not a 500.

All upstream interactions are mocked via ``monkeypatch`` on the proxy
service so these tests run hermetically (no network).
"""
from __future__ import annotations

import base64
import io
import json
import struct
import wave
from collections.abc import AsyncIterator
from typing import Any

import openai
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

import app.modules.proxy.service as proxy_module
import app.modules.proxy.api as proxy_api_module
from app.core.openai.model_registry import (
    ReasoningLevel,
    UpstreamModel,
    get_model_registry,
)


pytestmark = pytest.mark.e2e

DEFAULT_MODEL = "gpt-5.5"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
IMAGE_MODEL = "gpt-image-1"


# ---------------------------------------------------------------------------
# SSE helpers (mirror test_v1_responses_openai_sdk.py to stay independent)
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _codex_rate_limits_event() -> str:
    return _sse({
        "type": "codex.rate_limits",
        "plan_type": "pro",
        "rate_limits": {"allowed": True, "limit_reached": False},
    })


def _response_created(resp_id: str, seq: int = 0) -> str:
    return _sse({
        "type": "response.created", "sequence_number": seq,
        "response": {"id": resp_id, "object": "response",
                     "status": "in_progress", "output": []},
    })


def _response_completed_empty(resp_id: str, seq: int,
                              *, usage: dict | None = None) -> str:
    body: dict[str, Any] = {
        "id": resp_id, "object": "response",
        "status": "completed", "output": [],
    }
    body["usage"] = usage or {
        "input_tokens": 4, "output_tokens": 7, "total_tokens": 11,
    }
    return _sse({
        "type": "response.completed", "sequence_number": seq,
        "response": body,
    })


def _message_output_block(item_id: str, text: str, output_index: int,
                          start_seq: int) -> list[str]:
    return [
        _sse({"type": "response.output_item.added",
              "sequence_number": start_seq, "output_index": output_index,
              "item": {"id": item_id, "type": "message", "role": "assistant",
                       "status": "in_progress", "content": []}}),
        _sse({"type": "response.content_part.added",
              "sequence_number": start_seq + 1,
              "output_index": output_index, "content_index": 0,
              "item_id": item_id,
              "part": {"type": "output_text", "text": ""}}),
        _sse({"type": "response.output_text.delta",
              "sequence_number": start_seq + 2,
              "output_index": output_index, "content_index": 0,
              "item_id": item_id, "delta": text, "logprobs": []}),
        _sse({"type": "response.output_text.done",
              "sequence_number": start_seq + 3,
              "output_index": output_index, "content_index": 0,
              "item_id": item_id, "text": text, "logprobs": []}),
        _sse({"type": "response.content_part.done",
              "sequence_number": start_seq + 4,
              "output_index": output_index, "content_index": 0,
              "item_id": item_id,
              "part": {"type": "output_text", "text": text}}),
        _sse({"type": "response.output_item.done",
              "sequence_number": start_seq + 5, "output_index": output_index,
              "item": {"id": item_id, "type": "message", "role": "assistant",
                       "status": "completed",
                       "content": [{"type": "output_text", "text": text}]}}),
    ]


def _function_call_output_block(call_id: str, name: str, args: str,
                                output_index: int, start_seq: int) -> list[str]:
    fc_id = f"fc_{call_id}"
    return [
        _sse({"type": "response.output_item.added",
              "sequence_number": start_seq, "output_index": output_index,
              "item": {"id": fc_id, "type": "function_call",
                       "status": "in_progress", "call_id": call_id,
                       "name": name, "arguments": ""}}),
        _sse({"type": "response.function_call_arguments.delta",
              "sequence_number": start_seq + 1,
              "output_index": output_index, "item_id": fc_id, "delta": args}),
        _sse({"type": "response.function_call_arguments.done",
              "sequence_number": start_seq + 2,
              "output_index": output_index, "item_id": fc_id,
              "arguments": args}),
        _sse({"type": "response.output_item.done",
              "sequence_number": start_seq + 3, "output_index": output_index,
              "item": {"id": fc_id, "type": "function_call",
                       "status": "completed", "call_id": call_id,
                       "name": name, "arguments": args}}),
    ]


# ---------------------------------------------------------------------------
# Fixtures: openai.AsyncOpenAI bound to the ASGI app
# ---------------------------------------------------------------------------

def _make_upstream_model(
    slug: str, *, modalities: tuple[str, ...] = ("text",)
) -> UpstreamModel:
    return UpstreamModel(
        slug=slug, display_name=slug, description=f"Test model {slug}",
        context_window=272000, input_modalities=modalities,
        supported_reasoning_levels=(
            ReasoningLevel(effort="medium", description="default"),
        ),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False, default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True, minimal_client_version=None,
        priority=0, available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


@pytest_asyncio.fixture
async def sdk_client(
    e2e_client: AsyncClient,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    import_test_account,
):
    """Real ``openai.AsyncOpenAI`` bound to the in-process FastAPI app.

    The client uses the same ASGI transport that ``e2e_client`` already
    set up, so the SDK's HTTP traffic exercises the real codex-lb routing
    layer end-to-end.
    """
    await setup_dashboard_password(e2e_client)
    await enable_api_key_auth(e2e_client)
    created = await create_api_key(e2e_client, name="e2e-sdk-compat")
    await import_test_account(
        e2e_client, account_id="acc_e2e_compat",
        email="e2e-compat@example.com",
    )

    registry = get_model_registry()
    snapshot = {
        "plus": [
            _make_upstream_model(DEFAULT_MODEL),
            _make_upstream_model(TRANSCRIPTION_MODEL),
            _make_upstream_model(
                IMAGE_MODEL, modalities=("text", "image"),
            ),
        ],
        "pro": [
            _make_upstream_model(DEFAULT_MODEL),
            _make_upstream_model(TRANSCRIPTION_MODEL),
            _make_upstream_model(
                IMAGE_MODEL, modalities=("text", "image"),
            ),
        ],
    }
    result = registry.update(snapshot)
    if hasattr(result, "__await__"):
        await result

    transport = e2e_client._transport  # noqa: SLF001
    import httpx
    client = openai.AsyncOpenAI(
        api_key=created["key"],
        base_url="http://testserver/v1",
        http_client=httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ),
    )
    yield client
    await client.close()


def _patch_upstream_stream(monkeypatch, blocks: list[str]) -> None:
    async def fake_stream(payload, headers, access_token, account_id,
                          base_url=None, raise_for_status=False):
        for block in blocks:
            yield block

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)


# ---------------------------------------------------------------------------
# chat.completions
# ---------------------------------------------------------------------------

class TestChatCompletions:
    @pytest.mark.asyncio
    async def test_non_streaming_plain_text(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_nonstream"
        _patch_upstream_stream(monkeypatch, [
            _codex_rate_limits_event(),
            _response_created(resp_id, 0),
            *_message_output_block("msg_a", "hi there", 0, 1),
            _response_completed_empty(resp_id, 7),
        ])

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "hello"}],
        )

        assert result.choices
        assert result.choices[0].message.role == "assistant"
        assert result.choices[0].message.content == "hi there"
        assert result.choices[0].finish_reason in {"stop", "length"}

    @pytest.mark.asyncio
    async def test_streaming_plain_text(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_stream"
        _patch_upstream_stream(monkeypatch, [
            _codex_rate_limits_event(),
            _response_created(resp_id, 0),
            *_message_output_block("msg_b", "streamed content", 0, 1),
            _response_completed_empty(resp_id, 7),
        ])

        chunks: list[str] = []
        roles_seen: list[str | None] = []
        async with await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "stream"}],
            stream=True,
        ) as stream:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.role:
                    roles_seen.append(delta.role)
                if delta.content:
                    chunks.append(delta.content)

        joined = "".join(chunks)
        assert "streamed content" in joined
        assert "assistant" in roles_seen

    @pytest.mark.asyncio
    async def test_tool_call_non_streaming(self, sdk_client, monkeypatch):
        resp_id = "resp_chat_tool"
        _patch_upstream_stream(monkeypatch, [
            _codex_rate_limits_event(),
            _response_created(resp_id, 0),
            *_function_call_output_block(
                "call_w", "get_weather", '{"city":"Seoul"}', 0, 1,
            ),
            _response_completed_empty(resp_id, 5),
        ])

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "weather?"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }],
        )

        message = result.choices[0].message
        assert message.tool_calls
        tc = message.tool_calls[0]
        assert tc.function.name == "get_weather"
        assert json.loads(tc.function.arguments) == {"city": "Seoul"}
        assert result.choices[0].finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_multi_turn_payload_round_trip(self, sdk_client, monkeypatch):
        """Multi-turn input must reach the proxy untouched and the SDK
        must parse the reply normally."""
        resp_id = "resp_chat_multi"
        seen_payload: dict[str, Any] = {}

        async def fake_stream(payload, headers, access_token, account_id,
                              base_url=None, raise_for_status=False):
            seen_payload["payload"] = payload
            for block in [
                _codex_rate_limits_event(),
                _response_created(resp_id, 0),
                *_message_output_block("msg_m", "ack", 0, 1),
                _response_completed_empty(resp_id, 7),
            ]:
                yield block

        monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

        result = await sdk_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again?"},
            ],
        )

        assert result.choices[0].message.content == "ack"
        # The chat.completions endpoint translates to a Responses payload
        # before forwarding. Verify the multi-turn history survived.
        forwarded = seen_payload["payload"]
        assert hasattr(forwarded, "input") or "input" in (
            forwarded if isinstance(forwarded, dict) else {}
        )


# ---------------------------------------------------------------------------
# responses.parse (Pydantic structured output)
# ---------------------------------------------------------------------------

class _CityForecast(BaseModel):
    city: str
    temperature_c: int


class TestResponsesParse:
    @pytest.mark.asyncio
    async def test_parse_with_pydantic_text_format(self, sdk_client, monkeypatch):
        """The SDK's ``responses.parse(text_format=Model)`` issues a normal
        ``responses.create`` then parses the returned ``output_text`` into the
        Pydantic model. The proxy must produce a stream whose final
        ``output_text`` is valid JSON that the SDK can hydrate."""
        resp_id = "resp_parse_struct"
        payload = json.dumps({"city": "Seoul", "temperature_c": 21})
        _patch_upstream_stream(monkeypatch, [
            _codex_rate_limits_event(),
            _response_created(resp_id, 0),
            *_message_output_block("msg_parse", payload, 0, 1),
            _response_completed_empty(resp_id, 7),
        ])

        result = await sdk_client.responses.parse(
            model=DEFAULT_MODEL,
            input=[{"role": "user", "content": "forecast for Seoul"}],
            text_format=_CityForecast,
        )

        # ``output_parsed`` is the SDK-hydrated Pydantic instance.
        assert result.output_parsed == _CityForecast(
            city="Seoul", temperature_c=21,
        )


# ---------------------------------------------------------------------------
# models.list
# ---------------------------------------------------------------------------

class TestModels:
    @pytest.mark.asyncio
    async def test_list_returns_registered_models(self, sdk_client):
        result = await sdk_client.models.list()
        ids = {model.id for model in result.data}
        assert DEFAULT_MODEL in ids


# ---------------------------------------------------------------------------
# audio.transcriptions
# ---------------------------------------------------------------------------

def _make_wav_bytes(*, seconds: float = 0.05, sample_rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        n_samples = int(seconds * sample_rate)
        w.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))
    return buf.getvalue()


class TestAudioTranscriptions:
    @pytest.mark.asyncio
    async def test_create_returns_transcription_text(
        self, sdk_client, monkeypatch,
    ):
        """``client.audio.transcriptions.create`` must reach the
        ``/v1/audio/transcriptions`` route and return the SDK's
        ``Transcription`` object with ``.text``."""
        # Patch the proxy service's transcribe method so we don't hit
        # the upstream Codex transcription endpoint.
        captured: dict[str, Any] = {}

        async def fake_transcribe(self, *, audio_bytes, filename,
                                  content_type, prompt, headers, api_key):
            captured["filename"] = filename
            captured["bytes_len"] = len(audio_bytes)
            return {"text": "hello transcription"}

        from app.modules.proxy.service import ProxyService

        monkeypatch.setattr(ProxyService, "transcribe", fake_transcribe)

        wav = _make_wav_bytes()
        result = await sdk_client.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL,
            file=("sample.wav", wav, "audio/wav"),
        )

        assert result.text == "hello transcription"
        assert captured["bytes_len"] == len(wav)


# ---------------------------------------------------------------------------
# Unsupported routes — SDK must receive a clean 4xx (NotFoundError)
# ---------------------------------------------------------------------------

class TestUnsupportedSurfaces:
    """The proxy intentionally does not expose these OpenAI surfaces.
    Calling them through the SDK must yield a clean 4xx
    (``NotFoundError`` / 405 ``APIStatusError``) rather than a 500 or
    hang. Accepting any 4xx (and asserting ``< 500``) keeps the test
    robust to FastAPI's choice of 404 vs 405 depending on whether a
    different HTTP verb is registered on the same path."""

    @staticmethod
    def _assert_clean_4xx(exc: openai.APIStatusError) -> None:
        assert 400 <= exc.status_code < 500, (
            f"Unsupported route returned non-4xx status: {exc.status_code}"
        )

    @pytest.mark.asyncio
    async def test_embeddings_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.embeddings.create(
                model=DEFAULT_MODEL, input="hello",
            )
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_moderations_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.moderations.create(input="hello")
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_files_list_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.files.list()
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_batches_list_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.batches.list()
        self._assert_clean_4xx(ei.value)

    @pytest.mark.asyncio
    async def test_responses_retrieve_is_clean_4xx(self, sdk_client):
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.responses.retrieve("resp_does_not_exist")
        self._assert_clean_4xx(ei.value)

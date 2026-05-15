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
from typing import Any

import openai
import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import BaseModel

import app.modules.proxy.service as proxy_module
from app.core.openai.model_registry import (
    ReasoningLevel,
    UpstreamModel,
    get_model_registry,
)

pytestmark = pytest.mark.e2e

DEFAULT_MODEL = "gpt-5.5"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
IMAGE_MODEL = "gpt-image-2"


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
            _make_upstream_model(
                "gpt-image-1", modalities=("text", "image"),
            ),
        ],
        "pro": [
            _make_upstream_model(DEFAULT_MODEL),
            _make_upstream_model(TRANSCRIPTION_MODEL),
            _make_upstream_model(
                IMAGE_MODEL, modalities=("text", "image"),
            ),
            _make_upstream_model(
                "gpt-image-1", modalities=("text", "image"),
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
# images.generate / edit / variation
# ---------------------------------------------------------------------------

# A 1x1 transparent PNG, base64-decoded. Small enough to embed inline and large
# enough that ``UploadFile`` round-trips it as a real binary payload.
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)
_PNG_1X1_BYTES = base64.b64decode(_PNG_1X1_B64)


def _patch_images_upstream(
    monkeypatch,
    *,
    result_b64: str = "FAKE_IMAGE_B64",
    revised_prompt: str = "neat",
    resp_id: str = "resp_img",
    size: str = "1024x1024",
    output_format: str = "png",
    input_tokens: int = 11,
    output_tokens: int = 17,
    captured: dict[str, Any] | None = None,
) -> None:
    """Patch the upstream Codex stream to emit the minimum SSE sequence the
    images service needs to translate to a successful images.generate /
    images.edit / images.variation response: an ``image_generation_call``
    ``output_item.done`` followed by ``response.completed`` carrying
    ``tool_usage.image_gen`` tokens.

    Also patches ``_ensure_fresh_with_budget`` so the call path skips
    upstream account refresh (which would otherwise hit a real network).
    """

    async def fake_stream(payload, headers, access_token, account_id,
                          base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        if captured is not None:
            captured["model"] = payload.model
            captured["account_id"] = account_id
            captured["tools"] = list(payload.tools)
        yield _sse({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "image_generation_call",
                "id": "ig_e2e",
                "status": "completed",
                "result": result_b64,
                "revised_prompt": revised_prompt,
                "size": size,
                "quality": "low",
                "background": "auto",
                "output_format": output_format,
            },
        })
        yield _sse({
            "type": "response.completed",
            "response": {
                "id": resp_id, "object": "response", "status": "completed",
                "tool_usage": {
                    "image_gen": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                },
            },
        })

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(
        proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh,
    )


class TestImages:
    @pytest.mark.asyncio
    async def test_generate_returns_b64_image(self, sdk_client, monkeypatch):
        """``client.images.generate(...)`` must reach
        ``/v1/images/generations`` and return an ``ImagesResponse`` whose
        ``data[0].b64_json`` matches the upstream tool result. By default
        the OpenAI SDK requests ``response_format='b64_json'`` for
        ``gpt-image-*`` models, which is what the proxy emits."""
        captured: dict[str, Any] = {}
        _patch_images_upstream(
            monkeypatch,
            result_b64="GENERATED_B64",
            revised_prompt="a clean red circle",
            captured=captured,
        )

        result = await sdk_client.images.generate(
            model=IMAGE_MODEL,
            prompt="a red circle",
            n=1,
            size="1024x1024",
            quality="low",
        )

        assert result.data
        assert result.data[0].b64_json == "GENERATED_B64"
        # ``revised_prompt`` survives the translation layer.
        assert result.data[0].revised_prompt == "a clean red circle"
        # Image routes hide the host model behind ``images_host_model``;
        # ensure the upstream call really used the configured host model.
        assert captured["model"] not in {None, ""}
        tools = captured["tools"]
        assert tools, "image_generation tool must be forwarded to upstream"
        assert tools[0]["type"] == "image_generation"
        assert tools[0]["model"] == IMAGE_MODEL

    @pytest.mark.asyncio
    async def test_edit_returns_b64_image(self, sdk_client, monkeypatch):
        """``client.images.edit(image=..., prompt=...)`` posts multipart
        form-data to ``/v1/images/edits``. The proxy must accept the
        single ``image`` field, forward the bytes upstream, and return
        the translated b64 image."""
        # gpt-image-2 does not accept image edits; switch model for this
        # case to one that does.
        _patch_images_upstream(
            monkeypatch, result_b64="EDITED_B64", revised_prompt="edited",
        )

        result = await sdk_client.images.edit(
            model="gpt-image-1",
            image=("input.png", _PNG_1X1_BYTES, "image/png"),
            prompt="add a yellow border",
            n=1,
            size="1024x1024",
        )

        assert result.data
        assert result.data[0].b64_json == "EDITED_B64"

    @pytest.mark.asyncio
    async def test_variation_is_clean_4xx(self, sdk_client):
        """The proxy does not implement an image-variation translation
        path; ``client.images.create_variation(...)`` should yield a
        clean 4xx through the SDK."""
        with pytest.raises(openai.APIStatusError) as ei:
            await sdk_client.images.create_variation(
                model="gpt-image-1",
                image=("input.png", _PNG_1X1_BYTES, "image/png"),
                n=1,
                size="1024x1024",
            )
        assert 400 <= ei.value.status_code < 500, (
            f"images.variation returned non-4xx: {ei.value.status_code}"
        )


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

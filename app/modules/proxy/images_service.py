"""OpenAI Images API translation layer.

This module turns ``POST /v1/images/generations`` and ``POST /v1/images/edits``
requests into internal ``/v1/responses`` requests with the built-in
``image_generation`` tool, then folds the upstream Responses output (or SSE
event stream) back into the OpenAI Images response shape.

The intent is to keep all auth/account/sticky/usage logic in
``ProxyService.stream_responses`` (and friends) and only do data-shape
translation here.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from collections.abc import AsyncIterator, Mapping
from typing import Final, cast

from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import (
    V1ImageData,
    V1ImageResponse,
    V1ImagesEditsForm,
    V1ImagesGenerationsRequest,
    V1ImageUsage,
    is_supported_image_model,
    validate_image_request_parameters,
)
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_event, parse_sse_data_json

logger = logging.getLogger(__name__)

#: Compact instruction used to deterministically force exactly one
#: ``image_generation`` tool call from the host Responses model. The string
#: is intentionally short and self-contained to keep history-cost minimal.
_IMAGE_GENERATION_INSTRUCTIONS: Final[str] = (
    "You are an image generator. When asked, you MUST call the image_generation "
    "tool exactly once and return only that tool call. Do not produce any "
    "additional text output. Mirror the user's request verbatim into the tool's "
    "prompt argument."
)

#: Instruction tail appended to edit prompts so the host model knows that any
#: trailing input_image acts as a mask (since OpenAI's Images Edits API has a
#: distinct ``mask`` slot but the Responses image_generation tool does not).
_IMAGE_EDIT_MASK_HINT: Final[str] = (
    "\n\n(The final attached image is a transparent mask: only modify the regions where the mask is non-transparent.)"
)

#: SSE event types we *consume* from the upstream Responses stream.
_UPSTREAM_PARTIAL_IMAGE_EVENT: Final[str] = "response.image_generation_call.partial_image"
_UPSTREAM_OUTPUT_ITEM_DONE_EVENT: Final[str] = "response.output_item.done"
_UPSTREAM_RESPONSE_COMPLETED_EVENT: Final[str] = "response.completed"
_UPSTREAM_RESPONSE_FAILED_EVENT: Final[str] = "response.failed"
_UPSTREAM_RESPONSE_INCOMPLETE_EVENT: Final[str] = "response.incomplete"
_UPSTREAM_ERROR_EVENT: Final[str] = "error"

#: OpenAI Images SSE event names we *emit* to the client.
# Downstream OpenAI Images SSE event names. ``/v1/images/generations``
# emits ``image_generation.*`` and ``/v1/images/edits`` emits
# ``image_edit.*`` to match the canonical OpenAI Images streaming
# vocabulary; the route handler picks the prefix when invoking the
# translator.
_GENERATION_PARTIAL_EVENT: Final[str] = "image_generation.partial_image"
_GENERATION_COMPLETED_EVENT: Final[str] = "image_generation.completed"
_EDIT_PARTIAL_EVENT: Final[str] = "image_edit.partial_image"
_EDIT_COMPLETED_EVENT: Final[str] = "image_edit.completed"
_DOWNSTREAM_ERROR_EVENT: Final[str] = "error"

_DATA_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------


def _build_image_generation_tool(
    *,
    model: str,
    n: int,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    output_compression: int,
    moderation: str,
    partial_images: int | None,
    input_fidelity: str | None,
    streaming: bool,
    is_edit: bool = False,
) -> dict[str, JsonValue]:
    # NOTE: the upstream ``image_generation`` tool config does not accept
    # ``n``. ``validate_image_request_parameters`` unconditionally
    # rejects ``n > 1`` because client-side fan-out is not implemented
    # yet, so this function is only ever called with ``n == 1``. The
    # assert below catches a future regression where the API-boundary
    # cap is loosened without also adding fan-out, instead of silently
    # dropping the requested count.
    assert n == 1, "image_generation tool does not accept n; fan-out is not implemented"
    del n  # rejected upstream of this call (fan-out not yet implemented)
    tool: dict[str, JsonValue] = {
        "type": "image_generation",
        "model": model,
        "size": size,
        "quality": quality,
        "background": background,
        "output_format": output_format,
        "output_compression": output_compression,
        "moderation": moderation,
    }
    if is_edit:
        # Force the edit code path so the host model treats the attached
        # input_image(s) as a source/mask pair instead of inspiration for
        # a fresh generation. Without this the default "auto" action lets
        # the model decide between generation and editing, which can
        # silently break the edits contract.
        tool["action"] = "edit"
    if input_fidelity is not None:
        tool["input_fidelity"] = input_fidelity
    if streaming and partial_images is not None and partial_images > 0:
        tool["partial_images"] = partial_images
    return tool


def _build_user_message_input(
    prompt: str, *, attached_images: list[dict[str, JsonValue]] | None = None
) -> list[JsonValue]:
    content: list[JsonValue] = [{"type": "input_text", "text": prompt}]
    if attached_images:
        content.extend(attached_images)
    return [
        {
            "type": "message",
            "role": "user",
            "content": content,
        }
    ]


def _build_input_image_part(image_bytes: bytes, *, mime_type: str | None) -> dict[str, JsonValue]:
    """Build a Responses ``input_image`` content part as a base64 data URL."""
    resolved_mime = (mime_type or "image/png").strip() or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{resolved_mime};base64,{encoded}",
    }


def images_generation_to_responses_request(
    payload: V1ImagesGenerationsRequest,
    *,
    host_model: str,
) -> ResponsesRequest:
    """Translate a ``/v1/images/generations`` request into a Responses request.

    The upstream Responses backend rejects non-streaming requests that include
    the ``image_generation`` tool (the partial-image and final ``result``
    payloads are only delivered through SSE). We therefore always force
    ``stream=True`` on the internal request and let the caller drain the
    upstream stream into a JSON envelope when the public client did not
    request streaming.
    """
    streaming = bool(payload.stream)
    # ``validate_generations_payload`` resolves ``payload.model`` to a
    # concrete ``gpt-image-*`` value before this is ever called.
    assert payload.model is not None, "payload.model must be resolved before translation"
    tool = _build_image_generation_tool(
        model=payload.model,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        background=payload.background,
        output_format=payload.output_format,
        output_compression=payload.output_compression,
        moderation=payload.moderation,
        partial_images=payload.partial_images,
        input_fidelity=None,
        streaming=streaming,
    )
    return ResponsesRequest.model_validate(
        {
            "model": host_model,
            "instructions": _IMAGE_GENERATION_INSTRUCTIONS,
            "input": _build_user_message_input(payload.prompt),
            "tools": [tool],
            # Force the host model to invoke the image_generation tool
            # so it cannot return a refusal or plain text. Without this
            # the auto choice would surface as a 5xx through this
            # adapter even though the request shape was valid.
            "tool_choice": {"type": "image_generation"},
            "stream": True,
            "store": False,
        }
    )


def images_edit_to_responses_request(
    payload: V1ImagesEditsForm,
    *,
    host_model: str,
    images: list[tuple[bytes, str | None]],
    mask: tuple[bytes, str | None] | None,
) -> ResponsesRequest:
    """Translate a ``/v1/images/edits`` request into a Responses request.

    ``images`` is a non-empty list of ``(bytes, content_type)`` tuples
    representing the multipart ``image`` parts. ``mask`` is the optional
    ``mask`` part with the same shape; when provided, it is appended after
    the source images and the prompt is amended with a deterministic hint
    so the host model treats it correctly.
    """
    if not images:
        # Caller is expected to validate this beforehand, but guard so we
        # never silently produce an image-less Responses request.
        raise ValueError("/v1/images/edits requires at least one image part")

    streaming = bool(payload.stream)
    attached: list[dict[str, JsonValue]] = []
    for image_bytes, mime_type in images:
        attached.append(_build_input_image_part(image_bytes, mime_type=mime_type))
    if mask is not None:
        mask_bytes, mask_mime = mask
        attached.append(_build_input_image_part(mask_bytes, mime_type=mask_mime))

    prompt_text = payload.prompt
    if mask is not None:
        prompt_text = f"{prompt_text}{_IMAGE_EDIT_MASK_HINT}"

    # ``validate_edits_payload`` resolves ``payload.model`` to a concrete
    # ``gpt-image-*`` value before this is ever called.
    assert payload.model is not None, "payload.model must be resolved before translation"
    tool = _build_image_generation_tool(
        model=payload.model,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        background=payload.background,
        output_format=payload.output_format,
        output_compression=payload.output_compression,
        moderation=payload.moderation,
        partial_images=payload.partial_images,
        input_fidelity=payload.input_fidelity,
        streaming=streaming,
        is_edit=True,
    )
    return ResponsesRequest.model_validate(
        {
            "model": host_model,
            "instructions": _IMAGE_GENERATION_INSTRUCTIONS,
            "input": _build_user_message_input(prompt_text, attached_images=attached),
            "tools": [tool],
            # Force the host model to invoke the image_generation tool.
            # Leaving this on "auto" lets the model return a refusal or
            # plain text instead, which would surface as a 5xx through
            # this adapter even though the request shape was valid. See
            # the matching forced tool call in
            # ``images_generation_to_responses_request``.
            "tool_choice": {"type": "image_generation"},
            # See ``images_generation_to_responses_request`` for why this is
            # always True regardless of what the public client requested.
            "stream": True,
            "store": False,
        }
    )


# ---------------------------------------------------------------------------
# Public-request validation helpers wired by the route handlers.
# ---------------------------------------------------------------------------


def resolve_public_image_model(requested: str | None) -> str:
    """Return the publicly-effective ``gpt-image-*`` model.

    Falls back to the configured ``images_default_model`` when the client
    omits ``model``. The returned value is always validated against the
    ``gpt-image-*`` allowlist to catch a misconfigured default early.
    """
    settings = get_settings()
    resolved = requested or settings.images_default_model
    if not is_supported_image_model(resolved):
        raise ClientPayloadError(
            f"Unsupported image model '{resolved}'. Use a 'gpt-image-*' model.",
            param="model",
            code="invalid_request_error",
            error_type="invalid_request_error",
        )
    return resolved


def validate_generations_payload(payload: V1ImagesGenerationsRequest) -> V1ImagesGenerationsRequest:
    """Apply the cross-field validation matrix and return the payload with
    ``model`` populated to the configured default when the client omitted it.
    """
    settings = get_settings()
    resolved_model = resolve_public_image_model(payload.model)
    # Forward ``payload.input_fidelity`` so the validator rejects it on the
    # generations path (it is an edit-only parameter). Without this the
    # field would be silently dropped via the schema's ``extra=ignore``
    # and an invalid request would 200 instead of 400.
    validate_image_request_parameters(
        model=resolved_model,
        quality=payload.quality,
        size=payload.size,
        background=payload.background,
        output_format=payload.output_format,
        moderation=payload.moderation,
        input_fidelity=payload.input_fidelity,
        is_edit=False,
        n=payload.n,
        partial_images=payload.partial_images,
        output_compression=payload.output_compression,
        images_max_partial_images=settings.images_max_partial_images,
    )
    if payload.model != resolved_model:
        # Pydantic models are immutable by default; build a copy with the
        # resolved model so downstream code can rely on ``payload.model``
        # always being a concrete ``gpt-image-*`` value.
        return payload.model_copy(update={"model": resolved_model})
    return payload


def validate_edits_payload(payload: V1ImagesEditsForm) -> V1ImagesEditsForm:
    """Apply the cross-field validation matrix and return the payload with
    ``model`` populated to the configured default when the client omitted it.
    """
    settings = get_settings()
    resolved_model = resolve_public_image_model(payload.model)
    validate_image_request_parameters(
        model=resolved_model,
        quality=payload.quality,
        size=payload.size,
        background=payload.background,
        output_format=payload.output_format,
        moderation=payload.moderation,
        input_fidelity=payload.input_fidelity,
        is_edit=True,
        n=payload.n,
        partial_images=payload.partial_images,
        output_compression=payload.output_compression,
        images_max_partial_images=settings.images_max_partial_images,
    )
    if payload.model != resolved_model:
        return payload.model_copy(update={"model": resolved_model})
    return payload


# ---------------------------------------------------------------------------
# Non-streaming response translation
# ---------------------------------------------------------------------------


def _select_image_items(output: list[JsonValue]) -> list[Mapping[str, JsonValue]]:
    items: list[Mapping[str, JsonValue]] = []
    for entry in output:
        if not is_json_mapping(entry):
            continue
        if entry.get("type") == "image_generation_call":
            items.append(entry)
    return items


def _coerce_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


_USAGE_TOKEN_FIELDS: Final[frozenset[str]] = frozenset({"input_tokens", "output_tokens", "total_tokens"})


def _extract_image_usage(response: Mapping[str, JsonValue]) -> V1ImageUsage | None:
    tool_usage = response.get("tool_usage")
    if not is_json_mapping(tool_usage):
        return None
    image_usage = tool_usage.get("image_gen")
    if not is_json_mapping(image_usage):
        return None
    input_tokens = _coerce_int(image_usage.get("input_tokens"))
    output_tokens = _coerce_int(image_usage.get("output_tokens"))
    total_tokens = _coerce_int(image_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    input_details_raw = image_usage.get("input_tokens_details")
    output_details_raw = image_usage.get("output_tokens_details")
    input_details = dict(input_details_raw) if is_json_mapping(input_details_raw) else None
    output_details = dict(output_details_raw) if is_json_mapping(output_details_raw) else None
    # Forward any other usage detail keys upstream may add (e.g. cached
    # token counters) so the public response keeps the OpenAI Images
    # usage shape rather than silently dropping new fields.
    extra_usage: dict[str, JsonValue] = {}
    for key, value in image_usage.items():
        if key in _USAGE_TOKEN_FIELDS:
            continue
        if key in ("input_tokens_details", "output_tokens_details"):
            continue
        extra_usage[key] = value
    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and input_details is None
        and output_details is None
        and not extra_usage
    ):
        return None
    return V1ImageUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_tokens_details=input_details,
        output_tokens_details=output_details,
        **extra_usage,
    )


def images_response_from_responses(response: Mapping[str, JsonValue]) -> V1ImageResponse | OpenAIErrorEnvelope:
    """Build the public Images response from a completed Responses payload.

    Returns an :class:`OpenAIErrorEnvelope` (TypedDict) when the upstream
    response indicates the image generation failed; otherwise returns a
    :class:`V1ImageResponse`.
    """
    output_value = response.get("output")
    if not isinstance(output_value, list):
        return openai_error(
            "image_generation_failed",
            "Upstream response did not include an output array",
            error_type="server_error",
        )
    items = _select_image_items(cast(list[JsonValue], output_value))
    if not items:
        return openai_error(
            "image_generation_failed",
            "Upstream response did not include any image_generation_call items",
            error_type="server_error",
        )

    # Surface the first failed image_generation_call as an error envelope.
    for item in items:
        status = item.get("status")
        if isinstance(status, str) and status == "failed":
            error = item.get("error")
            if is_json_mapping(error):
                message = error.get("message")
                code = error.get("code")
                error_type = error.get("type")
                return openai_error(
                    code if isinstance(code, str) and code else "image_generation_failed",
                    message if isinstance(message, str) and message else "Image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            return openai_error(
                "image_generation_failed",
                "Upstream image_generation_call reported status=failed",
                error_type="server_error",
            )

    data_entries: list[V1ImageData] = []
    for item in items:
        result = item.get("result")
        if not isinstance(result, str) or not result:
            continue
        revised_prompt = item.get("revised_prompt")
        data_entries.append(
            V1ImageData(
                b64_json=result,
                revised_prompt=revised_prompt if isinstance(revised_prompt, str) and revised_prompt else None,
            )
        )

    if not data_entries:
        return openai_error(
            "image_generation_failed",
            "Upstream image_generation_call items contained no image data",
            error_type="server_error",
        )

    usage = _extract_image_usage(response)
    return V1ImageResponse(
        created=int(time.time()),
        data=data_entries,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Streaming translation
# ---------------------------------------------------------------------------


def _build_partial_image_event(payload: Mapping[str, JsonValue], *, event_type: str) -> dict[str, JsonValue] | None:
    partial_b64 = payload.get("partial_image_b64")
    if not isinstance(partial_b64, str) or not partial_b64:
        return None
    event: dict[str, JsonValue] = {
        "type": event_type,
        "b64_json": partial_b64,
        # OpenAI Images stream event schemas expose ``created_at`` on
        # both partial and completed events. Forward the upstream value
        # if present, otherwise stamp a synthesized one so consumers
        # that deserialize against the official model do not reject the
        # event for a missing field.
        "created_at": _coerce_created_at(payload.get("created_at")),
    }
    for key in ("partial_image_index", "size", "quality", "background", "output_format", "output_index"):
        value = payload.get(key)
        if value is not None:
            event[key] = value
    return event


def _build_completed_event(item: Mapping[str, JsonValue], *, event_type: str) -> dict[str, JsonValue] | None:
    if item.get("type") != "image_generation_call":
        return None
    result = item.get("result")
    if not isinstance(result, str) or not result:
        return None
    event: dict[str, JsonValue] = {
        "type": event_type,
        "b64_json": result,
        # See ``_build_partial_image_event`` for the rationale: keep the
        # upstream ``created_at`` if it is present, otherwise synthesize
        # so the OpenAI Images stream-event schema validates.
        "created_at": _coerce_created_at(item.get("created_at")),
    }
    for key in ("revised_prompt", "size", "quality", "background", "output_format"):
        value = item.get(key)
        if value is not None:
            event[key] = value
    return event


def _coerce_created_at(value: JsonValue | None) -> int:
    """Return ``value`` as an int when upstream supplied it, otherwise
    a synthesized current Unix timestamp."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return int(time.time())


def _build_error_event(
    code: str,
    message: str,
    *,
    error_type: str = "server_error",
    param: str | None = None,
) -> dict[str, JsonValue]:
    envelope = openai_error(code, message, error_type=error_type)
    if param:
        envelope["error"]["param"] = param
    event: dict[str, JsonValue] = {"type": _DOWNSTREAM_ERROR_EVENT}
    for key, value in envelope.items():
        event[key] = cast(JsonValue, value)
    return event


def _failed_image_item_error_event(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    error_value = item.get("error")
    if is_json_mapping(error_value):
        code = error_value.get("code")
        message = error_value.get("message")
        error_type = error_value.get("type")
        return _build_error_event(
            code if isinstance(code, str) and code else "image_generation_failed",
            message if isinstance(message, str) and message else "Image generation failed",
            error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
        )
    return _build_error_event(
        "image_generation_failed",
        "Upstream image_generation_call reported status=failed",
    )


def _response_failed_to_error_event(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    response = payload.get("response")
    if is_json_mapping(response):
        error_value = response.get("error")
        if is_json_mapping(error_value):
            code = error_value.get("code")
            message = error_value.get("message")
            error_type = error_value.get("type")
            return _build_error_event(
                code if isinstance(code, str) and code else "upstream_error",
                message if isinstance(message, str) and message else "Upstream image generation failed",
                error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
            )
    return _build_error_event("upstream_error", "Upstream image generation failed")


def _error_event_to_error_event(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    error_value = payload.get("error")
    if is_json_mapping(error_value):
        code = error_value.get("code")
        message = error_value.get("message")
        error_type = error_value.get("type")
        return _build_error_event(
            code if isinstance(code, str) and code else "upstream_error",
            message if isinstance(message, str) and message else "Upstream image generation failed",
            error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
        )
    return _build_error_event("upstream_error", "Upstream image generation failed")


async def translate_responses_stream_to_images_stream(
    upstream: AsyncIterator[str],
    *,
    captured: dict[str, str] | None = None,
    is_edit: bool = False,
) -> AsyncIterator[str]:
    """Convert a Responses SSE event stream into an OpenAI Images SSE stream.

    Yields formatted SSE event blocks (terminated by ``\\n\\n``) suitable
    for streaming directly to the client. Always emits exactly one terminal
    event (``image_generation.completed`` / ``image_edit.completed`` or
    ``error``) followed by ``data: [DONE]``.

    The ``is_edit`` flag selects the canonical OpenAI event prefix:
    ``/v1/images/generations`` callers leave it ``False`` and receive
    ``image_generation.partial_image`` / ``image_generation.completed``;
    ``/v1/images/edits`` callers pass ``True`` and receive
    ``image_edit.partial_image`` / ``image_edit.completed`` so SDKs that
    listen for the edit-specific event names see the stream.

    When ``captured`` is provided, the translator stores the upstream
    Responses ``id`` (under the ``response_id`` key) the first time it sees
    one, so route handlers can correlate the resulting request log with the
    public effective model after the stream completes.
    """
    partial_event_type = _EDIT_PARTIAL_EVENT if is_edit else _GENERATION_PARTIAL_EVENT
    completed_event_type = _EDIT_COMPLETED_EVENT if is_edit else _GENERATION_COMPLETED_EVENT
    terminal_emitted = False
    completion_pending = True
    # The Responses backend emits ``response.output_item.done`` for each
    # ``image_generation_call`` *before* the final ``response.completed``
    # event that carries ``tool_usage``. We buffer prepared completed
    # events so we can attach ``usage`` once ``response.completed``
    # arrives, and we keep them in arrival order so multi-image responses
    # do not silently drop earlier completions. If the upstream stream
    # ends without a ``response.completed`` we still flush whatever we
    # have buffered (without ``usage``).
    pending_completed_events: list[dict[str, JsonValue]] = []

    async for line in upstream:
        if not line:
            continue
        stripped = line.strip()
        if stripped == "data: [DONE]":
            # We emit our own [DONE] after a terminal event below.
            continue
        payload = parse_sse_data_json(line)
        if payload is None:
            continue
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            continue

        # Capture the upstream Responses id once so the route handler can
        # rewrite the request log with the public effective model after the
        # stream completes.
        if captured is not None and "response_id" not in captured:
            response_value = payload.get("response")
            if is_json_mapping(response_value):
                response_id = response_value.get("id")
                if isinstance(response_id, str) and response_id:
                    captured["response_id"] = response_id

        if event_type == _UPSTREAM_PARTIAL_IMAGE_EVENT:
            event = _build_partial_image_event(payload, event_type=partial_event_type)
            if event is not None:
                yield format_sse_event(event)
            continue

        if event_type == _UPSTREAM_OUTPUT_ITEM_DONE_EVENT:
            item = payload.get("item")
            if not is_json_mapping(item):
                continue
            if item.get("type") != "image_generation_call":
                continue
            status = item.get("status")
            if isinstance(status, str) and status == "failed":
                yield format_sse_event(_failed_image_item_error_event(item))
                terminal_emitted = True
                completion_pending = False
                break
            event = _build_completed_event(item, event_type=completed_event_type)
            if event is not None:
                # Buffer every completion (NOT a single overwrite-on-update
                # variable) so multi-image responses do not silently drop
                # earlier images. Flushing is deferred until the trailing
                # ``response.completed`` arrives so we can attach
                # ``tool_usage.image_gen`` as ``usage`` to the final event,
                # matching the canonical OpenAI Images streaming shape.
                pending_completed_events.append(event)
            continue

        if event_type == _UPSTREAM_RESPONSE_COMPLETED_EVENT:
            response_obj = payload.get("response")
            usage = _extract_image_usage(response_obj) if is_json_mapping(response_obj) else None
            if pending_completed_events:
                # Emit every buffered completion in arrival order so
                # multi-image responses do not silently drop earlier
                # images. Only the last completion carries ``usage``.
                last_index = len(pending_completed_events) - 1
                for idx, event in enumerate(pending_completed_events):
                    if idx == last_index and usage is not None:
                        event["usage"] = usage.model_dump(mode="json", exclude_none=True)
                    yield format_sse_event(event)
                pending_completed_events.clear()
                terminal_emitted = True
            elif not terminal_emitted:
                yield format_sse_event(
                    _build_error_event(
                        "image_generation_failed",
                        "Upstream stream completed without an image_generation_call result",
                    )
                )
                terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_RESPONSE_INCOMPLETE_EVENT:
            if not terminal_emitted:
                yield format_sse_event(
                    _build_error_event(
                        "image_generation_failed",
                        "Upstream stream ended before the image was generated",
                    )
                )
                terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_RESPONSE_FAILED_EVENT:
            yield format_sse_event(_response_failed_to_error_event(payload))
            terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_ERROR_EVENT:
            yield format_sse_event(_error_event_to_error_event(payload))
            terminal_emitted = True
            completion_pending = False
            break

        # All other event types (response.created, reasoning, content_part,
        # output_text, image_generation_call.in_progress / .generating,
        # codex.rate_limits, etc.) are intentionally dropped.
        continue

    # If the upstream stream ended without a ``response.completed`` (e.g.
    # truncation), still flush whatever we have buffered so the client sees
    # a terminal event before [DONE]. ``usage`` is unknown in this case so
    # we omit it.
    if pending_completed_events and not terminal_emitted:
        for event in pending_completed_events:
            yield format_sse_event(event)
        pending_completed_events.clear()
        terminal_emitted = True

    if completion_pending and not terminal_emitted:
        yield format_sse_event(
            _build_error_event(
                "image_generation_failed",
                "Upstream stream truncated before a terminal image event",
            )
        )

    yield "data: [DONE]\n\n"


async def collect_responses_stream_for_images(
    upstream: AsyncIterator[str],
    *,
    captured: dict[str, str] | None = None,
) -> tuple[dict[str, JsonValue] | None, OpenAIErrorEnvelope | None]:
    """Drain a Responses SSE stream and return the final ``response`` payload.

    Returns ``(response_mapping, None)`` when the upstream stream emits a
    ``response.completed`` event; ``(None, error_envelope)`` when it emits
    ``response.failed`` / ``error`` / closes early.

    When ``captured`` is provided, stores the upstream Responses ``id`` the
    first time it appears in any event, so the caller can correlate the
    resulting request log with the public effective model.
    """
    output_items: dict[int, dict[str, JsonValue]] = {}
    fallback_items: list[dict[str, JsonValue]] = []
    final_response: dict[str, JsonValue] | None = None
    terminal_error: OpenAIErrorEnvelope | None = None

    async for line in upstream:
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            continue
        payload = parse_sse_data_json(line)
        if payload is None:
            continue
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            continue

        # Capture the upstream Responses id once so the route handler can
        # rewrite the request log with the public effective model after the
        # stream completes.
        if captured is not None and "response_id" not in captured:
            response_value = payload.get("response")
            if is_json_mapping(response_value):
                response_id = response_value.get("id")
                if isinstance(response_id, str) and response_id:
                    captured["response_id"] = response_id

        if event_type == _UPSTREAM_OUTPUT_ITEM_DONE_EVENT:
            output_index = payload.get("output_index")
            item = payload.get("item")
            if not is_json_mapping(item):
                continue
            if isinstance(output_index, int):
                output_items[output_index] = dict(item)
            else:
                # Some upstream paths omit ``output_index``; preserve the
                # arrival order so we can still surface the item.
                fallback_items.append(dict(item))
            continue

        if event_type == _UPSTREAM_RESPONSE_COMPLETED_EVENT and final_response is None:
            response_value = payload.get("response")
            base: dict[str, JsonValue]
            if is_json_mapping(response_value):
                base = dict(response_value)
            else:
                base = {}
            existing_output = base.get("output")
            if not (isinstance(existing_output, list) and existing_output):
                merged_output: list[JsonValue] = [item for _, item in sorted(output_items.items())]
                merged_output.extend(fallback_items)
                base["output"] = merged_output
            final_response = base
            # Don't break — keep draining so the upstream stream_responses
            # generator finalizes (which writes the request log) before we
            # return control to the caller. Once we have ``final_response``
            # we still skip processing of any subsequent events.
            continue

        if event_type == _UPSTREAM_RESPONSE_INCOMPLETE_EVENT and terminal_error is None:
            # Match the streaming translator: an incomplete upstream
            # response is surfaced as an error envelope rather than a
            # 200 response carrying a half-finished image.
            terminal_error = openai_error(
                "image_generation_failed",
                "Upstream response was incomplete before the image was generated",
                error_type="server_error",
            )
            break

        if event_type == _UPSTREAM_RESPONSE_FAILED_EVENT:
            # Once a successful ``response.completed`` is captured, ignore
            # any trailing transport-level failure events: they cannot
            # invalidate an already-received image result. Without this
            # guard a late ``response.failed`` could turn a successful
            # 200 envelope into a spurious 502.
            if final_response is not None:
                continue
            response_value = payload.get("response")
            error_value: JsonValue | None = None
            if is_json_mapping(response_value):
                error_value = response_value.get("error")
            if is_json_mapping(error_value):
                code = error_value.get("code")
                message = error_value.get("message")
                error_type = error_value.get("type")
                envelope = openai_error(
                    code if isinstance(code, str) and code else "upstream_error",
                    message if isinstance(message, str) and message else "Upstream image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            else:
                envelope = openai_error(
                    "upstream_error",
                    "Upstream image generation failed",
                    error_type="server_error",
                )
            terminal_error = envelope
            break

        if event_type == _UPSTREAM_ERROR_EVENT:
            # Same rationale as the ``response.failed`` branch above:
            # ignore late error events once a complete response has
            # already been captured.
            if final_response is not None:
                continue
            error_value = payload.get("error")
            if is_json_mapping(error_value):
                code = error_value.get("code")
                message = error_value.get("message")
                error_type = error_value.get("type")
                envelope = openai_error(
                    code if isinstance(code, str) and code else "upstream_error",
                    message if isinstance(message, str) and message else "Upstream image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            else:
                envelope = openai_error(
                    "upstream_error",
                    "Upstream image generation failed",
                    error_type="server_error",
                )
            terminal_error = envelope
            break

    if terminal_error is not None:
        return None, terminal_error
    if final_response is None:
        return None, openai_error(
            "image_generation_failed",
            "Upstream stream truncated before a terminal event",
            error_type="server_error",
        )
    return final_response, None


# ---------------------------------------------------------------------------
# Misc utilities used by the API handlers
# ---------------------------------------------------------------------------


def decode_data_url(data_url: str) -> tuple[bytes, str | None]:
    """Decode a ``data:<mime>;base64,<...>`` URL into raw bytes plus mime.

    Raises ``ValueError`` if the URL does not match the expected shape or
    the base64 payload is malformed.
    """
    match = _DATA_URL_PATTERN.match(data_url.strip())
    if match is None:
        raise ValueError("Expected a data:<mime>;base64,<payload> URL")
    mime_type = match.group("mime") or None
    try:
        return base64.b64decode(match.group("b64"), validate=True), mime_type
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image payload") from exc


def make_invalid_request_error(
    message: str,
    *,
    param: str | None = None,
    code: str = "invalid_request_error",
) -> OpenAIErrorEnvelope:
    envelope = openai_error(code, message, error_type="invalid_request_error")
    if param:
        envelope["error"]["param"] = param
    return envelope


def make_not_found_error(message: str) -> OpenAIErrorEnvelope:
    return openai_error("not_found_error", message, error_type="invalid_request_error")


__all__ = [
    "collect_responses_stream_for_images",
    "decode_data_url",
    "images_edit_to_responses_request",
    "images_generation_to_responses_request",
    "images_response_from_responses",
    "is_supported_image_model",
    "make_invalid_request_error",
    "make_not_found_error",
    "resolve_public_image_model",
    "translate_responses_stream_to_images_stream",
    "validate_edits_payload",
    "validate_generations_payload",
]

"""OpenAI Images API (`/v1/images/*`) request/response schemas.

These schemas describe the *public* surface that codex-lb exposes. They mirror
the OpenAI Images API request shape so SDKs (e.g. ``openai`` Python client,
the codex CLI image fallback) can target codex-lb without modification.

The endpoints themselves are implemented as a thin translation layer over
``/v1/responses`` with the built-in ``image_generation`` tool — see
``app.modules.proxy.images_service``.

Per-model validation matrix (kept here so request validation rejects the
request *before* any upstream call is opened):

- ``gpt-image-2`` (default):
    * ``quality`` in ``{low, medium, high, auto}``
    * ``size`` is either ``auto`` or ``WIDTHxHEIGHT`` where width and height
      are multiples of 16, max edge is 3840 px, the aspect ratio is at most
      3:1, and total pixel count is in ``[655_360, 8_294_400]``.
    * ``input_fidelity`` MUST NOT be sent (rejected on both generations and
      edits).
    * ``background = "transparent"`` is rejected.
- ``gpt-image-1.5`` / ``gpt-image-1`` / ``gpt-image-1-mini`` (legacy):
    * ``size`` in ``{1024x1024, 1536x1024, 1024x1536, auto}``.
    * ``input_fidelity`` in ``{low, high}`` is allowed but only on
      ``/v1/images/edits``.
- Any other ``model`` value: rejected with OpenAI ``invalid_request_error``
  and ``param: model``.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.openai.exceptions import ClientPayloadError

#: Allowed public ``gpt-image-*`` family. Any other model is rejected up-front.
GPT_IMAGE_MODEL_PREFIX: Final[str] = "gpt-image-"

#: Models that take the constrained gpt-image-2 parameter matrix.
_GPT_IMAGE_2_MODELS: Final[frozenset[str]] = frozenset({"gpt-image-2"})

#: Models that take the legacy fixed-size matrix and allow ``input_fidelity``
#: (only on edits).
_LEGACY_GPT_IMAGE_MODELS: Final[frozenset[str]] = frozenset({"gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"})

_GPT_IMAGE_2_QUALITY: Final[frozenset[str]] = frozenset({"low", "medium", "high", "auto"})
_LEGACY_QUALITY: Final[frozenset[str]] = frozenset({"low", "medium", "high", "standard", "hd", "auto"})
_LEGACY_FIXED_SIZES: Final[frozenset[str]] = frozenset({"1024x1024", "1536x1024", "1024x1536", "auto"})
_BACKGROUND_VALUES: Final[frozenset[str]] = frozenset({"transparent", "opaque", "auto"})
_OUTPUT_FORMATS: Final[frozenset[str]] = frozenset({"png", "jpeg", "webp"})
_MODERATION_VALUES: Final[frozenset[str]] = frozenset({"auto", "low"})
_INPUT_FIDELITY_VALUES: Final[frozenset[str]] = frozenset({"low", "high"})

# gpt-image-2 size limits (per the upstream image_generation tool contract).
_GPT_IMAGE_2_MAX_EDGE: Final[int] = 3840
_GPT_IMAGE_2_MIN_PIXELS: Final[int] = 655_360
_GPT_IMAGE_2_MAX_PIXELS: Final[int] = 8_294_400
_GPT_IMAGE_2_RATIO_MAX: Final[float] = 3.0
_GPT_IMAGE_2_DIM_MULTIPLE: Final[int] = 16

_SIZE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d+)x(\d+)$")


def _images_invalid(
    message: str, *, param: str | None = None, code: str = "invalid_request_error"
) -> ClientPayloadError:
    """Build a ``ClientPayloadError`` shaped as an OpenAI invalid_request_error."""
    return ClientPayloadError(
        message,
        param=param,
        code=code,
        error_type="invalid_request_error",
    )


def is_supported_image_model(model: str) -> bool:
    return model.startswith(GPT_IMAGE_MODEL_PREFIX) and (
        model in _GPT_IMAGE_2_MODELS or model in _LEGACY_GPT_IMAGE_MODELS
    )


def validate_image_size(model: str, size: str) -> None:
    """Validate ``size`` for the requested public image model.

    ``"auto"`` is always accepted. For gpt-image-2 the explicit
    ``WIDTHxHEIGHT`` form is parsed and constrained; for legacy gpt-image
    models the explicit form must match one of the fixed allowed sizes.
    """
    if size == "auto":
        return
    match = _SIZE_PATTERN.match(size)
    if match is None:
        raise _images_invalid(
            f"Invalid size '{size}'. Expected 'auto' or 'WIDTHxHEIGHT'.",
            param="size",
        )
    width = int(match.group(1))
    height = int(match.group(2))
    if model in _GPT_IMAGE_2_MODELS:
        _validate_gpt_image_2_size(width, height)
        return
    # Legacy models: only the canonical fixed sizes are allowed.
    if size not in _LEGACY_FIXED_SIZES:
        raise _images_invalid(
            f"Invalid size '{size}' for model '{model}'. Allowed sizes: 1024x1024, 1536x1024, 1024x1536, auto.",
            param="size",
        )


def _validate_gpt_image_2_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise _images_invalid("size dimensions must be positive integers", param="size")
    if width % _GPT_IMAGE_2_DIM_MULTIPLE != 0 or height % _GPT_IMAGE_2_DIM_MULTIPLE != 0:
        raise _images_invalid(
            f"size dimensions must be multiples of {_GPT_IMAGE_2_DIM_MULTIPLE} for gpt-image-2",
            param="size",
        )
    if max(width, height) > _GPT_IMAGE_2_MAX_EDGE:
        raise _images_invalid(
            f"size edges must be <= {_GPT_IMAGE_2_MAX_EDGE} px for gpt-image-2",
            param="size",
        )
    long_edge = max(width, height)
    short_edge = min(width, height)
    if short_edge == 0 or (long_edge / short_edge) > _GPT_IMAGE_2_RATIO_MAX:
        raise _images_invalid(
            f"size aspect ratio must be at most {int(_GPT_IMAGE_2_RATIO_MAX)}:1 for gpt-image-2",
            param="size",
        )
    pixels = width * height
    if pixels < _GPT_IMAGE_2_MIN_PIXELS or pixels > _GPT_IMAGE_2_MAX_PIXELS:
        raise _images_invalid(
            f"size total pixels must be between {_GPT_IMAGE_2_MIN_PIXELS} and "
            f"{_GPT_IMAGE_2_MAX_PIXELS} for gpt-image-2",
            param="size",
        )


def validate_image_request_parameters(
    *,
    model: str,
    quality: str,
    size: str,
    background: str,
    output_format: str,
    moderation: str,
    input_fidelity: str | None,
    is_edit: bool,
    n: int,
    partial_images: int | None,
    output_compression: int,
    images_max_partial_images: int,
) -> None:
    """Apply the cross-field per-model validation matrix.

    Raises :class:`ClientPayloadError` (which the API layer renders as an
    OpenAI ``invalid_request_error`` envelope) on the first violation.
    """
    if not is_supported_image_model(model):
        raise _images_invalid(
            f"Unsupported image model '{model}'. Use a 'gpt-image-*' model.",
            param="model",
        )

    # ``n`` is unconditionally capped at 1: the upstream
    # ``image_generation`` tool accepts only a single image per call and
    # codex-lb does not yet implement client-side fan-out (multiple
    # internal Responses calls whose ``image_generation_call`` results
    # are concatenated into one public envelope). The cap will be
    # relaxed in the same change that introduces fan-out, alongside a
    # new configuration knob; we deliberately do not expose a
    # ``images_max_n`` setting today since honoring it without fan-out
    # would silently return fewer images than requested.
    if n < 1 or n > 1:
        raise _images_invalid(
            "n must be 1; multiple images per request are not supported by the "
            "upstream image_generation tool yet. Issue the request multiple "
            "times to get more images.",
            param="n",
        )

    if background not in _BACKGROUND_VALUES:
        raise _images_invalid(
            f"Invalid background '{background}'. Expected one of: " + ", ".join(sorted(_BACKGROUND_VALUES)),
            param="background",
        )

    if output_format not in _OUTPUT_FORMATS:
        raise _images_invalid(
            f"Invalid output_format '{output_format}'. Expected one of: png, jpeg, webp.",
            param="output_format",
        )

    if not 0 <= output_compression <= 100:
        raise _images_invalid(
            "output_compression must be between 0 and 100",
            param="output_compression",
        )

    if moderation not in _MODERATION_VALUES:
        raise _images_invalid(
            f"Invalid moderation '{moderation}'. Expected one of: auto, low.",
            param="moderation",
        )

    if partial_images is not None:
        if partial_images < 0 or partial_images > images_max_partial_images:
            raise _images_invalid(
                f"partial_images must be between 0 and {images_max_partial_images}",
                param="partial_images",
            )

    if model in _GPT_IMAGE_2_MODELS:
        if quality not in _GPT_IMAGE_2_QUALITY:
            raise _images_invalid(
                f"Invalid quality '{quality}' for gpt-image-2. Expected one of: low, medium, high, auto.",
                param="quality",
            )
        if background == "transparent":
            raise _images_invalid(
                "background='transparent' is not supported by gpt-image-2",
                param="background",
            )
        if input_fidelity is not None:
            raise _images_invalid(
                "input_fidelity is not supported by gpt-image-2",
                param="input_fidelity",
            )
    else:
        # Legacy gpt-image-{1,1-mini,1.5}.
        if quality not in _LEGACY_QUALITY:
            raise _images_invalid(
                f"Invalid quality '{quality}' for model '{model}'.",
                param="quality",
            )
        if input_fidelity is not None:
            if not is_edit:
                raise _images_invalid(
                    "input_fidelity is only supported on /v1/images/edits",
                    param="input_fidelity",
                )
            if input_fidelity not in _INPUT_FIDELITY_VALUES:
                raise _images_invalid(
                    f"Invalid input_fidelity '{input_fidelity}'. Expected one of: low, high.",
                    param="input_fidelity",
                )

    validate_image_size(model, size)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class V1ImagesGenerationsRequest(BaseModel):
    """OpenAI-compatible request body for ``POST /v1/images/generations``."""

    model_config = ConfigDict(extra="ignore")

    #: Public model id. Optional; route handlers fall back to the configured
    #: ``images_default_model`` when omitted. When provided, it must be a
    #: ``gpt-image-*`` model.
    model: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    n: int = 1
    size: str = "auto"
    quality: str = "auto"
    background: str = "auto"
    output_format: str = "png"
    output_compression: int = 100
    moderation: str = "auto"
    partial_images: int | None = None
    stream: bool = False
    user: str | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_supported_image_model(value):
            raise ValueError(f"Unsupported image model '{value}'. Use a 'gpt-image-*' model.")
        return value


class V1ImagesEditsForm(BaseModel):
    """Form-encoded fields accepted by ``POST /v1/images/edits``.

    The ``image`` and ``mask`` parts are bound separately by the FastAPI
    route handler (since they are file uploads, not Pydantic-bound fields).
    """

    model_config = ConfigDict(extra="ignore")

    #: Public model id. Optional; route handlers fall back to the configured
    #: ``images_default_model`` when omitted. When provided, it must be a
    #: ``gpt-image-*`` model.
    model: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1)
    n: int = 1
    size: str = "auto"
    quality: str = "auto"
    background: str = "auto"
    output_format: str = "png"
    output_compression: int = 100
    moderation: str = "auto"
    partial_images: int | None = None
    stream: bool = False
    input_fidelity: str | None = None
    user: str | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_supported_image_model(value):
            raise ValueError(f"Unsupported image model '{value}'. Use a 'gpt-image-*' model.")
        return value


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class V1ImageData(BaseModel):
    """One generated image entry on a non-streaming images response."""

    model_config = ConfigDict(extra="ignore")

    b64_json: str
    revised_prompt: str | None = None


class V1ImageUsage(BaseModel):
    """Usage block on a non-streaming images response.

    Sourced from upstream ``response.tool_usage.image_gen`` when present.
    Total tokens are derived (``input_tokens + output_tokens``) when both
    components are known.
    """

    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class V1ImageResponse(BaseModel):
    """OpenAI-compatible non-streaming response for ``/v1/images/*``.

    Shape matches the documented OpenAI Images response so SDKs can decode it
    directly: ``{"created": ..., "data": [...], "usage": {...} }``.
    """

    model_config = ConfigDict(extra="ignore")

    created: int
    data: list[V1ImageData]
    usage: V1ImageUsage | None = None


__all__ = [
    "GPT_IMAGE_MODEL_PREFIX",
    "V1ImageData",
    "V1ImageResponse",
    "V1ImageUsage",
    "V1ImagesEditsForm",
    "V1ImagesGenerationsRequest",
    "is_supported_image_model",
    "validate_image_request_parameters",
    "validate_image_size",
]

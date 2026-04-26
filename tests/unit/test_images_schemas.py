"""Unit tests for the OpenAI Images API schema and validation matrix."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import (
    V1ImageData,
    V1ImageResponse,
    V1ImagesEditsForm,
    V1ImagesGenerationsRequest,
    V1ImageUsage,
    is_supported_image_model,
    validate_image_request_parameters,
    validate_image_size,
)

# ---------------------------------------------------------------------------
# Pydantic shape validation
# ---------------------------------------------------------------------------


class TestV1ImagesGenerationsRequest:
    def test_minimal_request_defaults_apply(self) -> None:
        req = V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-2", "prompt": "a red circle"})
        assert req.model == "gpt-image-2"
        assert req.prompt == "a red circle"
        assert req.n == 1
        assert req.size == "auto"
        assert req.quality == "auto"
        assert req.background == "auto"
        assert req.output_format == "png"
        assert req.output_compression == 100
        assert req.moderation == "auto"
        assert req.partial_images is None
        assert req.stream is False
        assert req.user is None

    def test_unsupported_model_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            V1ImagesGenerationsRequest.model_validate({"model": "gpt-5.2", "prompt": "hi"})

    def test_unknown_gpt_image_variant_raises(self) -> None:
        with pytest.raises(ValidationError):
            V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-99", "prompt": "hi"})

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-2", "prompt": ""})

    def test_extra_fields_are_ignored(self) -> None:
        req = V1ImagesGenerationsRequest.model_validate({"model": "gpt-image-2", "prompt": "hi", "wormhole": True})
        assert not hasattr(req, "wormhole")


class TestV1ImagesEditsForm:
    def test_default_input_fidelity_is_none(self) -> None:
        form = V1ImagesEditsForm.model_validate({"model": "gpt-image-1", "prompt": "edit me"})
        assert form.input_fidelity is None

    def test_input_fidelity_high_round_trips(self) -> None:
        form = V1ImagesEditsForm.model_validate({"model": "gpt-image-1", "prompt": "edit", "input_fidelity": "high"})
        assert form.input_fidelity == "high"


class TestV1ImageResponse:
    def test_full_response_round_trips(self) -> None:
        response = V1ImageResponse(
            created=1700000000,
            data=[
                V1ImageData(b64_json="aGVsbG8=", revised_prompt="a red square"),
                V1ImageData(b64_json="d29ybGQ=", revised_prompt=None),
            ],
            usage=V1ImageUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        )
        dumped = response.model_dump(mode="json", exclude_none=True)
        assert dumped["created"] == 1700000000
        assert dumped["data"][0] == {"b64_json": "aGVsbG8=", "revised_prompt": "a red square"}
        assert dumped["data"][1] == {"b64_json": "d29ybGQ="}
        assert dumped["usage"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


# ---------------------------------------------------------------------------
# is_supported_image_model
# ---------------------------------------------------------------------------


class TestIsSupportedImageModel:
    @pytest.mark.parametrize(
        "model",
        ["gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"],
    )
    def test_supported_models(self, model: str) -> None:
        assert is_supported_image_model(model) is True

    @pytest.mark.parametrize(
        "model",
        ["gpt-5.2", "gpt-5.4", "gpt-image-3", "dall-e-3", "image-2", ""],
    )
    def test_unsupported_models(self, model: str) -> None:
        assert is_supported_image_model(model) is False


# ---------------------------------------------------------------------------
# validate_image_size
# ---------------------------------------------------------------------------


class TestValidateImageSize:
    def test_auto_always_allowed_for_gpt_image_2(self) -> None:
        validate_image_size("gpt-image-2", "auto")

    def test_auto_always_allowed_for_legacy(self) -> None:
        validate_image_size("gpt-image-1", "auto")

    @pytest.mark.parametrize("size", ["1024x1024", "1536x1024", "1024x1536"])
    def test_legacy_fixed_sizes_allowed(self, size: str) -> None:
        validate_image_size("gpt-image-1", size)
        validate_image_size("gpt-image-1-mini", size)
        validate_image_size("gpt-image-1.5", size)

    @pytest.mark.parametrize("size", ["1280x720", "2048x2048", "1024x1024 ", "garbage"])
    def test_legacy_other_sizes_rejected(self, size: str) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            validate_image_size("gpt-image-1", size)
        assert excinfo.value.param == "size"

    def test_gpt_image_2_accepts_well_formed_size(self) -> None:
        validate_image_size("gpt-image-2", "1024x1024")
        validate_image_size("gpt-image-2", "2048x2048")
        validate_image_size("gpt-image-2", "1024x1536")
        # Exactly the 3:1 ratio should be allowed (3072x1024 = 3145728 px).
        validate_image_size("gpt-image-2", "3072x1024")

    @pytest.mark.parametrize(
        "size,expected_param",
        [
            # Edges that aren't multiples of 16
            ("1000x1000", "size"),
            ("1024x1023", "size"),
            # Exceeds max edge of 3840
            ("3856x1024", "size"),
            # Aspect ratio > 3:1 (e.g. 3328:1024 ≈ 3.25:1)
            ("3328x1024", "size"),
            # Below min pixels
            ("512x512", "size"),
            # Above max pixels (2880x2880 = 8_294_400 ✓ at exact boundary,
            # so push slightly higher)
            ("2896x2896", "size"),
            # Malformed
            ("1024", "size"),
            ("not-a-size", "size"),
        ],
    )
    def test_gpt_image_2_rejects_invalid_sizes(self, size: str, expected_param: str) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            validate_image_size("gpt-image-2", size)
        assert excinfo.value.param == expected_param


# ---------------------------------------------------------------------------
# validate_image_request_parameters (cross-field matrix)
# ---------------------------------------------------------------------------


def _validate_default(**overrides: object) -> None:
    """Convenience wrapper that fills in every required argument."""
    kwargs: dict[str, object] = {
        "model": "gpt-image-2",
        "quality": "auto",
        "size": "auto",
        "background": "auto",
        "output_format": "png",
        "moderation": "auto",
        "input_fidelity": None,
        "is_edit": False,
        "n": 1,
        "partial_images": None,
        "output_compression": 100,
        "images_max_n": 4,
        "images_max_partial_images": 3,
    }
    kwargs.update(overrides)
    validate_image_request_parameters(**kwargs)  # type: ignore[arg-type]


class TestValidateImageRequestParameters:
    def test_default_request_is_valid(self) -> None:
        _validate_default()

    def test_unsupported_model_param_is_model(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(model="gpt-5.2")
        assert excinfo.value.param == "model"

    @pytest.mark.parametrize("n,expect_ok", [(1, True), (0, False), (2, False), (5, False)])
    def test_n_bounds(self, n: int, expect_ok: bool) -> None:
        """``n`` is hard-capped at 1 today regardless of ``images_max_n``
        because client-side fan-out is not implemented yet. The cap is
        relaxed in the same change that introduces fan-out.
        """
        if expect_ok:
            _validate_default(n=n)
        else:
            with pytest.raises(ClientPayloadError) as excinfo:
                _validate_default(n=n)
            assert excinfo.value.param == "n"

    def test_n_greater_than_one_is_rejected_even_when_images_max_n_is_higher(self) -> None:
        """``images_max_n`` must NOT be a path to silently dropping images."""
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(n=2, images_max_n=4)
        assert excinfo.value.param == "n"

    @pytest.mark.parametrize(
        "background,expect_ok",
        [("auto", True), ("opaque", True), ("transparent", False), ("plaid", False)],
    )
    def test_gpt_image_2_rejects_transparent_background(self, background: str, expect_ok: bool) -> None:
        if expect_ok:
            _validate_default(background=background)
        else:
            with pytest.raises(ClientPayloadError) as excinfo:
                _validate_default(background=background)
            assert excinfo.value.param == "background"

    def test_gpt_image_2_rejects_input_fidelity(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(input_fidelity="high", is_edit=True)
        assert excinfo.value.param == "input_fidelity"

    @pytest.mark.parametrize("quality", ["low", "medium", "high", "auto"])
    def test_gpt_image_2_quality_accepts(self, quality: str) -> None:
        _validate_default(quality=quality)

    @pytest.mark.parametrize("quality", ["standard", "hd", "max"])
    def test_gpt_image_2_quality_rejects(self, quality: str) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(quality=quality)
        assert excinfo.value.param == "quality"

    def test_legacy_input_fidelity_rejected_on_generations(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(model="gpt-image-1", input_fidelity="high", is_edit=False)
        assert excinfo.value.param == "input_fidelity"

    def test_legacy_input_fidelity_allowed_on_edits(self) -> None:
        _validate_default(model="gpt-image-1", input_fidelity="high", is_edit=True, size="1024x1024")

    def test_legacy_input_fidelity_invalid_value(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(model="gpt-image-1", input_fidelity="extreme", is_edit=True, size="1024x1024")
        assert excinfo.value.param == "input_fidelity"

    @pytest.mark.parametrize(
        "size,expect_ok",
        [
            ("1024x1024", True),
            ("1536x1024", True),
            ("1024x1536", True),
            ("auto", True),
            ("2048x2048", False),
            ("1280x720", False),
        ],
    )
    def test_legacy_size_constraint(self, size: str, expect_ok: bool) -> None:
        kwargs: dict[str, object] = {"model": "gpt-image-1", "size": size}
        if expect_ok:
            _validate_default(**kwargs)
        else:
            with pytest.raises(ClientPayloadError) as excinfo:
                _validate_default(**kwargs)
            assert excinfo.value.param == "size"

    @pytest.mark.parametrize("output_format", ["png", "jpeg", "webp"])
    def test_output_format_allowed(self, output_format: str) -> None:
        _validate_default(output_format=output_format)

    def test_output_format_rejected(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(output_format="bmp")
        assert excinfo.value.param == "output_format"

    @pytest.mark.parametrize("compression,expect_ok", [(0, True), (50, True), (100, True), (-1, False), (101, False)])
    def test_output_compression_bounds(self, compression: int, expect_ok: bool) -> None:
        if expect_ok:
            _validate_default(output_compression=compression)
        else:
            with pytest.raises(ClientPayloadError) as excinfo:
                _validate_default(output_compression=compression)
            assert excinfo.value.param == "output_compression"

    @pytest.mark.parametrize("partial,expect_ok", [(0, True), (1, True), (3, True), (-1, False), (4, False)])
    def test_partial_images_bounds(self, partial: int, expect_ok: bool) -> None:
        if expect_ok:
            _validate_default(partial_images=partial)
        else:
            with pytest.raises(ClientPayloadError) as excinfo:
                _validate_default(partial_images=partial)
            assert excinfo.value.param == "partial_images"

    @pytest.mark.parametrize("moderation", ["auto", "low"])
    def test_moderation_allowed(self, moderation: str) -> None:
        _validate_default(moderation=moderation)

    def test_moderation_rejected(self) -> None:
        with pytest.raises(ClientPayloadError) as excinfo:
            _validate_default(moderation="strict")
        assert excinfo.value.param == "moderation"

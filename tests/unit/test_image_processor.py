from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

import app.core.clients.image_processor as image_processor
from app.core.clients.image_processor import ImageProcessingError, PromptImageMode, process_for_prompt_bytes

pytestmark = pytest.mark.unit


def _image_bytes(*, image_format: str, size: tuple[int, int], color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = BytesIO()
    save_kwargs: dict[str, bool | int] = {}
    if image_format == "JPEG":
        save_kwargs["quality"] = 92
    elif image_format == "WEBP":
        save_kwargs["lossless"] = True
    image.save(buffer, format=image_format, **save_kwargs)
    return buffer.getvalue()


def _gif_bytes(size: tuple[int, int]) -> bytes:
    image = Image.new("P", size)
    buffer = BytesIO()
    image.save(buffer, format="GIF")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
    ],
)
def test_process_for_prompt_bytes_passthrough_for_small_supported_formats(image_format: str, mime: str) -> None:
    raw = _image_bytes(image_format=image_format, size=(512, 256))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.bytes == raw
    assert encoded.mime == mime
    assert encoded.width == 512
    assert encoded.height == 256


def test_process_for_prompt_bytes_resizes_wide_png() -> None:
    raw = _image_bytes(image_format="PNG", size=(4096, 2048))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.mime == "image/png"
    assert encoded.width == 2048
    assert encoded.height == 1024


def test_process_for_prompt_bytes_resizes_tall_png() -> None:
    raw = _image_bytes(image_format="PNG", size=(1024, 4096))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.mime == "image/png"
    assert encoded.width == 512
    assert encoded.height == 2048


def test_process_for_prompt_bytes_resizes_jpeg_with_quality_85() -> None:
    raw = _image_bytes(image_format="JPEG", size=(4096, 2048))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.mime == "image/jpeg"
    assert encoded.width == 2048
    assert encoded.height == 1024


def test_process_for_prompt_bytes_resizes_webp_lossless() -> None:
    raw = _image_bytes(image_format="WEBP", size=(4096, 2048))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.mime == "image/webp"
    assert encoded.width == 2048
    assert encoded.height == 1024


def test_process_for_prompt_bytes_original_mode_preserves_large_png() -> None:
    raw = _image_bytes(image_format="PNG", size=(4096, 2048))

    encoded = process_for_prompt_bytes(raw, PromptImageMode.ORIGINAL)

    assert encoded.bytes == raw
    assert encoded.mime == "image/png"
    assert encoded.width == 4096
    assert encoded.height == 2048


def test_process_for_prompt_bytes_reencodes_gif_to_png() -> None:
    raw = _gif_bytes((320, 160))

    encoded = process_for_prompt_bytes(raw)

    assert encoded.mime == "image/png"
    assert encoded.width == 320
    assert encoded.height == 160
    assert encoded.bytes != raw


@pytest.mark.parametrize("image_format", ["BMP", "TIFF"])
def test_process_for_prompt_bytes_rejects_unsupported_formats(image_format: str) -> None:
    raw = _image_bytes(image_format=image_format, size=(32, 32))

    with pytest.raises(ImageProcessingError):
        process_for_prompt_bytes(raw)


def test_process_for_prompt_bytes_rejects_garbage_bytes() -> None:
    with pytest.raises(ImageProcessingError):
        process_for_prompt_bytes(b"not-an-image")


def test_process_for_prompt_bytes_rejects_decompression_bomb() -> None:
    # Pillow flags any image whose decoded pixel count would exceed the
    # configured ``MAX_IMAGE_PIXELS`` (default ~178 MP) with
    # ``DecompressionBombError``. Pin the limit very low so a small,
    # validly encoded fixture trips the same code path as a hostile
    # multi-gigapixel attachment.
    original_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 16
    try:
        with pytest.raises(ImageProcessingError):
            process_for_prompt_bytes(_image_bytes(image_format="PNG", size=(64, 64)))
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit


def test_process_for_prompt_bytes_cache_returns_same_instance() -> None:
    image_processor._CACHE.clear()
    raw = _image_bytes(image_format="PNG", size=(64, 64))

    first = process_for_prompt_bytes(raw)
    second = process_for_prompt_bytes(raw)

    assert first is second

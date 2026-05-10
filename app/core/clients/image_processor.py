from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha1
from io import BytesIO
from threading import RLock

from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError

MAX_DIMENSION = 2048
_CACHE_CAPACITY = 32

_SOURCE_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP"})
_PASSTHROUGH_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class EncodedImage:
    bytes: bytes
    mime: str
    width: int
    height: int

    def into_data_url(self) -> str:
        encoded = base64.b64encode(self.bytes).decode("ascii")
        return f"data:{self.mime};base64,{encoded}"


class PromptImageMode(StrEnum):
    RESIZE_TO_FIT = "resize_to_fit"
    ORIGINAL = "original"


class ImageProcessingError(Exception):
    """Raised when prompt image bytes cannot be decoded or encoded safely."""


_CACHE_LOCK = RLock()
_CACHE: OrderedDict[tuple[str, PromptImageMode], EncodedImage] = OrderedDict()


def process_for_prompt_bytes(
    file_bytes: bytes,
    mode: PromptImageMode = PromptImageMode.RESIZE_TO_FIT,
) -> EncodedImage:
    cache_key = (sha1(file_bytes).hexdigest(), mode)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            return cached

    encoded = _process_for_prompt_bytes_uncached(file_bytes, mode)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            return cached
        _CACHE[cache_key] = encoded
        if len(_CACHE) > _CACHE_CAPACITY:
            _CACHE.popitem(last=False)
    return encoded


def _process_for_prompt_bytes_uncached(file_bytes: bytes, mode: PromptImageMode) -> EncodedImage:
    try:
        with Image.open(BytesIO(file_bytes)) as image:
            source_format = (image.format or "").upper()
            if source_format not in _SOURCE_FORMATS:
                raise ImageProcessingError("UnsupportedImageFormat")
            width, height = image.size
            if mode == PromptImageMode.ORIGINAL or (width <= MAX_DIMENSION and height <= MAX_DIMENSION):
                if source_format in _PASSTHROUGH_FORMATS:
                    return EncodedImage(
                        bytes=file_bytes,
                        mime=_FORMAT_TO_MIME[source_format],
                        width=width,
                        height=height,
                    )
                return _encode_image(image, target_format="PNG", width=width, height=height)

            scale = min(MAX_DIMENSION / width, MAX_DIMENSION / height)
            target_width = max(1, min(MAX_DIMENSION, int(width * scale)))
            target_height = max(1, min(MAX_DIMENSION, int(height * scale)))
            # Pillow does not expose a Triangle filter. LANCZOS tracks the
            # upstream Triangle output better than BILINEAR for this path.
            resized = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            try:
                target_format = source_format if source_format in _PASSTHROUGH_FORMATS else "PNG"
                return _encode_image(
                    resized,
                    target_format=target_format,
                    width=target_width,
                    height=target_height,
                )
            finally:
                resized.close()
    except ImageProcessingError:
        raise
    except (UnidentifiedImageError, DecompressionBombError, OSError) as exc:
        raise ImageProcessingError("ImageDecodeFailed") from exc


def _encode_image(image: Image.Image, *, target_format: str, width: int, height: int) -> EncodedImage:
    buffer = BytesIO()
    save_kwargs: dict[str, bool | int] = {}
    if target_format == "JPEG":
        save_kwargs["quality"] = 85
    elif target_format == "WEBP":
        save_kwargs["lossless"] = True
    try:
        image.save(buffer, format=target_format, **save_kwargs)
    except OSError as exc:
        raise ImageProcessingError("ImageEncodeFailed") from exc
    return EncodedImage(
        bytes=buffer.getvalue(),
        mime=_FORMAT_TO_MIME[target_format],
        width=width,
        height=height,
    )

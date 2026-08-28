from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Collection
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from app.core.multipart_fields import raise_upload_validation

__all__ = [
    "ACCOUNT_IMPORT_MULTIPART_POLICY",
    "IMAGE_EDITS_MULTIPART_POLICY",
    "TRANSCRIPTION_MULTIPART_POLICY",
    "MultipartPayloadTooLarge",
    "MultipartPolicy",
    "bounded_multipart_form",
    "read_bounded_upload",
]

_MIB = 1024 * 1024
_PAYLOAD_TOO_LARGE_MESSAGE = "Multipart payload exceeds the allowed size."
_INVALID_MULTIPART_MESSAGE = "Invalid multipart request."


@dataclass(frozen=True, slots=True)
class MultipartPolicy:
    max_body_bytes: int
    max_file_bytes: int
    max_aggregate_file_bytes: int
    max_files: int
    max_fields: int
    max_text_part_bytes: int

    def __post_init__(self) -> None:
        limits = (
            self.max_body_bytes,
            self.max_file_bytes,
            self.max_aggregate_file_bytes,
            self.max_files,
            self.max_fields,
            self.max_text_part_bytes,
        )
        if any(limit < 0 for limit in limits):
            raise ValueError("Multipart limits must be non-negative")


ACCOUNT_IMPORT_MULTIPART_POLICY = MultipartPolicy(
    max_body_bytes=2 * _MIB,
    max_file_bytes=_MIB,
    max_aggregate_file_bytes=_MIB,
    max_files=1,
    max_fields=0,
    max_text_part_bytes=0,
)

TRANSCRIPTION_MULTIPART_POLICY = MultipartPolicy(
    max_body_bytes=32 * _MIB,
    max_file_bytes=25_000_000,
    max_aggregate_file_bytes=25_000_000,
    max_files=1,
    max_fields=32,
    max_text_part_bytes=256 * 1024,
)

IMAGE_EDITS_MULTIPART_POLICY = MultipartPolicy(
    max_body_bytes=64 * _MIB,
    max_file_bytes=49_999_999,
    max_aggregate_file_bytes=49_999_999,
    max_files=17,
    max_fields=32,
    max_text_part_bytes=256 * 1024,
)


def _normalize_param(param: str | None) -> str | None:
    if param == "image[]":
        return "image"
    return param


class MultipartPayloadTooLarge(MultiPartException):
    def __init__(self, *, param: str | None = None) -> None:
        super().__init__(_PAYLOAD_TOO_LARGE_MESSAGE)
        self.param = _normalize_param(param)


async def _counted_request_stream(request: Request, max_body_bytes: int) -> AsyncGenerator[bytes, None]:
    streamed_bytes = 0
    async for chunk in request.stream():
        streamed_bytes += len(chunk)
        if streamed_bytes > max_body_bytes:
            raise MultipartPayloadTooLarge()
        yield chunk


def _usable_content_length(headers: Headers) -> int | None:
    raw_length = headers.get("content-length")
    if raw_length is None:
        return None
    try:
        declared_length = int(raw_length)
    except ValueError:
        return None
    return declared_length if declared_length >= 0 else None


def _is_multipart_form_data(headers: Headers) -> bool:
    content_type = headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "multipart/form-data"


class _BoundedMultiPartParser(MultiPartParser):
    def __init__(
        self,
        headers: Headers,
        stream: AsyncGenerator[bytes, None],
        policy: MultipartPolicy,
        typed_upload_fields: Collection[str],
    ) -> None:
        super().__init__(
            headers,
            stream,
            max_files=policy.max_files,
            max_fields=policy.max_fields,
            max_part_size=policy.max_text_part_bytes,
        )
        self._policy = policy
        self._typed_upload_fields = frozenset(typed_upload_fields)
        self._current_file_bytes = 0
        self._aggregate_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_headers_finished(self) -> None:
        try:
            super().on_headers_finished()
        except MultiPartException:
            if self._current_fields > self.max_fields and self._current_part.field_name in self._typed_upload_fields:
                raise_upload_validation(self._current_part.field_name)
            raise

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part_bytes = end - start
        current_part = self._current_part
        if current_part.file is None:
            if len(current_part.data) + part_bytes > self._policy.max_text_part_bytes:
                raise MultipartPayloadTooLarge()
        else:
            param = _normalize_param(current_part.field_name)
            if self._current_file_bytes + part_bytes > self._policy.max_file_bytes:
                raise MultipartPayloadTooLarge(param=param)
            if self._aggregate_file_bytes + part_bytes > self._policy.max_aggregate_file_bytes:
                raise MultipartPayloadTooLarge(param=param)
            self._current_file_bytes += part_bytes
            self._aggregate_file_bytes += part_bytes
        super().on_part_data(data, start, end)

    def close_created_files(self) -> None:
        files = self._files_to_close_on_error
        self._files_to_close_on_error = []
        for file in files:
            with suppress(OSError):
                file.close()


def _multipart_bad_request(exc: MultiPartException | MultipartParseError | OSError) -> HTTPException:
    if isinstance(exc, MultiPartException):
        detail = exc.message
    else:
        detail = _INVALID_MULTIPART_MESSAGE
    return HTTPException(status_code=400, detail=detail)


async def _parse_with_cleanup(parser: _BoundedMultiPartParser) -> FormData:
    try:
        return await parser.parse()
    except BaseException:
        parser.close_created_files()
        raise


@asynccontextmanager
async def bounded_multipart_form(
    request: Request,
    policy: MultipartPolicy,
    *,
    typed_upload_fields: Collection[str] = (),
) -> AsyncIterator[FormData]:
    if not _is_multipart_form_data(request.headers):
        empty_form = FormData()
        try:
            yield empty_form
        finally:
            await empty_form.close()
        return

    declared_length = _usable_content_length(request.headers)
    if declared_length is not None and declared_length > policy.max_body_bytes:
        raise MultipartPayloadTooLarge()

    parser = _BoundedMultiPartParser(
        request.headers,
        _counted_request_stream(request, policy.max_body_bytes),
        policy,
        typed_upload_fields,
    )
    try:
        try:
            form = await _parse_with_cleanup(parser)
        except MultipartPayloadTooLarge:
            raise
        except (MultiPartException, MultipartParseError, OSError) as exc:
            raise _multipart_bad_request(exc) from exc

        try:
            yield form
        finally:
            await form.close()
    finally:
        parser.close_created_files()


async def read_bounded_upload(upload: UploadFile, max_bytes: int, param: str | None) -> bytes:
    if max_bytes < 0:
        raise ValueError("Upload limit must be non-negative")
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise MultipartPayloadTooLarge(param=param)
    return data

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass
from io import BytesIO
from tempfile import SpooledTemporaryFile
from typing import cast

import pytest
import starlette.formparsers as starlette_formparsers
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message, Receive, Scope

from app.core.multipart import (
    ACCOUNT_IMPORT_MULTIPART_POLICY,
    IMAGE_EDITS_MULTIPART_POLICY,
    TRANSCRIPTION_MULTIPART_POLICY,
    MultipartPayloadTooLarge,
    MultipartPolicy,
    bounded_multipart_form,
    read_bounded_upload,
)

pytestmark = pytest.mark.unit

_BOUNDARY = "codex-lb-boundary"
_MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    data: bytes
    filename: str | None = None
    content_type: str = "application/octet-stream"


class _TrackedSpool(SpooledTemporaryFile[bytes]):
    bytes_at_close: bytes | None

    def __init__(self, *, max_size: int) -> None:
        super().__init__(max_size=max_size)
        self.bytes_at_close = None

    def close(self) -> None:
        if not self.closed:
            position = self.tell()
            self.seek(0)
            self.bytes_at_close = self.read()
            self.seek(position)
        super().close()


class _RecordingUpload(UploadFile):
    def __init__(self, data: bytes) -> None:
        super().__init__(BytesIO(data), filename="upload.bin", size=len(data))
        self.requested_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        return await super().read(size)


def _policy(
    *,
    max_body_bytes: int = 4096,
    max_file_bytes: int = 64,
    max_aggregate_file_bytes: int = 128,
    max_files: int = 4,
    max_fields: int = 4,
    max_text_part_bytes: int = 64,
) -> MultipartPolicy:
    return MultipartPolicy(
        max_body_bytes=max_body_bytes,
        max_file_bytes=max_file_bytes,
        max_aggregate_file_bytes=max_aggregate_file_bytes,
        max_files=max_files,
        max_fields=max_fields,
        max_text_part_bytes=max_text_part_bytes,
    )


def _multipart_body(*parts: _Part, boundary: str = _BOUNDARY) -> bytes:
    body = bytearray()
    for part in parts:
        body.extend(f"--{boundary}\r\n".encode())
        disposition = f'Content-Disposition: form-data; name="{part.name}"'
        if part.filename is not None:
            disposition += f'; filename="{part.filename}"'
        body.extend(f"{disposition}\r\n".encode())
        if part.filename is not None:
            body.extend(f"Content-Type: {part.content_type}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(part.data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body)


def _request(
    chunks: list[bytes],
    *,
    content_length: int | str | None = None,
    content_type: str = f"multipart/form-data; boundary={_BOUNDARY}",
) -> tuple[Request, list[int]]:
    messages: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    receive_calls: list[int] = []

    async def receive() -> Message:
        receive_calls.append(1)
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    headers = [(b"content-type", content_type.encode("latin-1"))]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/upload",
            "raw_path": b"/upload",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
    )
    return Request(scope, receive=cast(Receive, receive)), receive_calls


def _interrupted_request(first_chunk: bytes, *, cancel: bool) -> Request:
    calls = 0

    async def receive() -> Message:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": first_chunk, "more_body": True}
        if cancel:
            raise asyncio.CancelledError
        return {"type": "http.disconnect"}

    request, _ = _request([b""])
    return Request(request.scope, receive=cast(Receive, receive))


def _track_spools(monkeypatch: pytest.MonkeyPatch) -> list[_TrackedSpool]:
    spools: list[_TrackedSpool] = []

    def create_spool(*, max_size: int) -> _TrackedSpool:
        spool = _TrackedSpool(max_size=max_size)
        spools.append(spool)
        return spool

    monkeypatch.setattr(starlette_formparsers, "SpooledTemporaryFile", create_spool)
    return spools


def test_route_policies_are_fixed_and_immutable() -> None:
    assert ACCOUNT_IMPORT_MULTIPART_POLICY == _policy(
        max_body_bytes=2 * _MIB,
        max_file_bytes=_MIB,
        max_aggregate_file_bytes=_MIB,
        max_files=1,
        max_fields=0,
        max_text_part_bytes=0,
    )
    assert TRANSCRIPTION_MULTIPART_POLICY == _policy(
        max_body_bytes=32 * _MIB,
        max_file_bytes=25_000_000,
        max_aggregate_file_bytes=25_000_000,
        max_files=1,
        max_fields=32,
        max_text_part_bytes=256 * 1024,
    )
    assert IMAGE_EDITS_MULTIPART_POLICY == _policy(
        max_body_bytes=64 * _MIB,
        max_file_bytes=49_999_999,
        max_aggregate_file_bytes=49_999_999,
        max_files=17,
        max_fields=32,
        max_text_part_bytes=256 * 1024,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(ACCOUNT_IMPORT_MULTIPART_POLICY, "max_files", 2)


@pytest.mark.asyncio
async def test_declared_body_limit_rejects_before_consuming_request() -> None:
    body = _multipart_body(_Part("file", b"ok", filename="audio.wav"))
    request, receive_calls = _request([body], content_length=len(body) + 1)

    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        async with bounded_multipart_form(request, _policy(max_body_bytes=len(body))):
            pytest.fail("declared oversized body should not parse")

    assert exc_info.value.param is None
    assert receive_calls == []


@pytest.mark.asyncio
async def test_invalid_declared_length_is_ignored_when_actual_body_is_bounded() -> None:
    body = _multipart_body(_Part("file", b"ok", filename="audio.wav"))
    request, receive_calls = _request([body], content_length="not-a-number")

    async with bounded_multipart_form(request, _policy(max_body_bytes=len(body))) as form:
        upload = form["file"]
        assert isinstance(upload, UploadFile)
        assert await upload.read() == b"ok"

    assert receive_calls == [1]
    assert upload.file.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [None, "application/json"])
async def test_non_multipart_media_yields_empty_form_without_consuming_body(
    content_type: str | None,
) -> None:
    request, receive_calls = _request(
        [b"body must remain unread"],
        content_length=10_000,
        content_type=content_type or "",
    )
    if content_type is None:
        request.scope["headers"] = [
            (name, value) for name, value in request.scope["headers"] if name != b"content-type"
        ]

    async with bounded_multipart_form(request, _policy(max_body_bytes=1)) as form:
        assert list(form.multi_items()) == []

    assert receive_calls == []


@pytest.mark.asyncio
async def test_actual_body_allows_exact_limit() -> None:
    body = _multipart_body(_Part("file", b"ok", filename="audio.wav"))
    split = len(body) // 2
    request, _ = _request([body[:split], body[split:]], content_length=1)

    async with bounded_multipart_form(request, _policy(max_body_bytes=len(body))) as form:
        upload = form["file"]
        assert isinstance(upload, UploadFile)
        assert await upload.read() == b"ok"


@pytest.mark.asyncio
async def test_understated_length_cannot_bypass_streamed_body_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _track_spools(monkeypatch)
    body = _multipart_body(_Part("file", b"x" * 32, filename="audio.wav"))
    header_end = body.index(b"\r\n\r\n") + 4
    crossing_at = header_end + 16
    request, receive_calls = _request([body[:crossing_at], body[crossing_at:]], content_length=1)

    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        async with bounded_multipart_form(request, _policy(max_body_bytes=crossing_at)):
            pytest.fail("actual streamed bytes should remain authoritative")

    assert exc_info.value.param is None
    assert receive_calls == [1, 1]
    assert len(spools) == 1
    assert spools[0].closed


@pytest.mark.asyncio
async def test_file_limit_allows_exact_bytes_and_closes_form() -> None:
    body = _multipart_body(_Part("image[]", b"1234", filename="image.png"))
    request, _ = _request([body])

    async with bounded_multipart_form(
        request,
        _policy(max_file_bytes=4, max_aggregate_file_bytes=4),
    ) as form:
        upload = form["image[]"]
        assert isinstance(upload, UploadFile)
        assert not upload.file.closed
        assert await read_bounded_upload(upload, 4, "image[]") == b"1234"

    assert upload.file.closed


@pytest.mark.asyncio
async def test_file_limit_rejects_crossing_bytes_before_spool_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _track_spools(monkeypatch)
    body = _multipart_body(_Part("image[]", b"12345", filename="image.png"))
    request, _ = _request([body])

    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        async with bounded_multipart_form(
            request,
            _policy(max_file_bytes=4, max_aggregate_file_bytes=10),
        ):
            pytest.fail("oversized file should not parse")

    assert exc_info.value.param == "image"
    assert len(spools) == 1
    assert spools[0].closed
    assert len(spools[0].bytes_at_close or b"") <= 4


@pytest.mark.asyncio
async def test_aggregate_file_limit_rejects_and_closes_every_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _track_spools(monkeypatch)
    body = _multipart_body(
        _Part("image", b"123", filename="first.png"),
        _Part("mask", b"456", filename="mask.png"),
    )
    request, _ = _request([body])

    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        async with bounded_multipart_form(
            request,
            _policy(max_file_bytes=4, max_aggregate_file_bytes=5),
        ):
            pytest.fail("aggregate oversized files should not parse")

    assert exc_info.value.param == "mask"
    assert len(spools) == 2
    assert all(spool.closed for spool in spools)
    assert sum(len(spool.bytes_at_close or b"") for spool in spools) <= 5


@pytest.mark.asyncio
async def test_aggregate_and_part_counts_allow_exact_limits() -> None:
    body = _multipart_body(
        _Part("first", b"12", filename="first.bin"),
        _Part("label", b"a"),
        _Part("second", b"345", filename="second.bin"),
        _Part("prompt", b"b"),
    )
    request, _ = _request([body])
    policy = _policy(
        max_file_bytes=3,
        max_aggregate_file_bytes=5,
        max_files=2,
        max_fields=2,
        max_text_part_bytes=1,
    )

    async with bounded_multipart_form(request, policy) as form:
        first = form["first"]
        second = form["second"]
        assert isinstance(first, UploadFile)
        assert isinstance(second, UploadFile)
        assert await first.read() == b"12"
        assert await second.read() == b"345"
        assert form["label"] == "a"
        assert form["prompt"] == "b"

    assert first.file.closed
    assert second.file.closed


@pytest.mark.asyncio
async def test_text_part_limit_allows_exact_and_rejects_crossing_bytes() -> None:
    exact_body = _multipart_body(_Part("prompt", b"1234"))
    exact_request, _ = _request([exact_body])

    async with bounded_multipart_form(exact_request, _policy(max_text_part_bytes=4)) as form:
        assert form["prompt"] == "1234"

    oversized_body = _multipart_body(_Part("prompt", b"12345"))
    oversized_request, _ = _request([oversized_body])
    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        async with bounded_multipart_form(oversized_request, _policy(max_text_part_bytes=4)):
            pytest.fail("oversized text part should not parse")
    assert exc_info.value.param is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parts", "policy", "detail"),
    [
        (
            (_Part("first", b"1", filename="1.bin"), _Part("second", b"2", filename="2.bin")),
            _policy(max_files=1),
            "Too many files",
        ),
        (
            (_Part("first", b"1"), _Part("second", b"2")),
            _policy(max_fields=1),
            "Too many fields",
        ),
    ],
)
async def test_part_count_failures_map_to_http_400(
    parts: tuple[_Part, ...],
    policy: MultipartPolicy,
    detail: str,
) -> None:
    request, _ = _request([_multipart_body(*parts)])

    with pytest.raises(HTTPException) as exc_info:
        async with bounded_multipart_form(request, policy):
            pytest.fail("excess parts should not parse")

    assert exc_info.value.status_code == 400
    assert detail in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_typed_upload_text_field_uses_validation_and_closes_prior_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _track_spools(monkeypatch)
    body = _multipart_body(
        _Part("other", b"file", filename="other.bin"),
        _Part("auth_json", b"{}"),
    )
    request, _ = _request([body])

    with pytest.raises(RequestValidationError) as exc_info:
        async with bounded_multipart_form(
            request,
            _policy(max_files=1, max_fields=0, max_text_part_bytes=0),
            typed_upload_fields=("auth_json",),
        ):
            pytest.fail("text-valued upload field should not parse")

    assert exc_info.value.errors()[0]["loc"] == ("body", "auth_json")
    assert len(spools) == 1
    assert spools[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"", "multipart/form-data"),
        (b"not-a-multipart-body", f"multipart/form-data; boundary={_BOUNDARY}"),
    ],
)
async def test_malformed_multipart_maps_to_http_400(body: bytes, content_type: str) -> None:
    request, _ = _request([body], content_type=content_type)

    with pytest.raises(HTTPException) as exc_info:
        async with bounded_multipart_form(request, _policy()):
            pytest.fail("malformed multipart should not parse")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_parser_oserror_maps_to_http_400_and_closes_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools = _track_spools(monkeypatch)

    async def fail_write(self: UploadFile, data: bytes) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(UploadFile, "write", fail_write)
    body = _multipart_body(_Part("file", b"audio", filename="audio.wav"))
    request, _ = _request([body])

    with pytest.raises(HTTPException) as exc_info:
        async with bounded_multipart_form(request, _policy()):
            pytest.fail("spool write failure should not parse")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid multipart request."
    assert len(spools) == 1
    assert spools[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["disconnect", "cancellation"])
async def test_transport_interruptions_propagate_and_close_partial_spools(
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    spools = _track_spools(monkeypatch)
    body = _multipart_body(_Part("file", b"x" * 64, filename="audio.wav"))
    header_end = body.index(b"\r\n\r\n") + 4
    request = _interrupted_request(body[: header_end + 32], cancel=cancel)
    expected_error = asyncio.CancelledError if cancel else ClientDisconnect

    with pytest.raises(expected_error):
        async with bounded_multipart_form(request, _policy()):
            pytest.fail("interrupted multipart should not parse")

    assert len(spools) == 1
    assert spools[0].closed


@pytest.mark.asyncio
async def test_form_cleanup_runs_when_consumer_raises() -> None:
    body = _multipart_body(_Part("file", b"ok", filename="audio.wav"))
    request, _ = _request([body])
    upload: UploadFile | None = None

    with pytest.raises(RuntimeError, match="consumer failed"):
        async with bounded_multipart_form(request, _policy()) as form:
            value = form["file"]
            assert isinstance(value, UploadFile)
            upload = value
            raise RuntimeError("consumer failed")

    assert upload is not None
    assert upload.file.closed


@pytest.mark.asyncio
async def test_large_file_rolls_to_disk_and_is_closed_after_context() -> None:
    payload = b"x" * (_MIB + 1)
    body = _multipart_body(_Part("file", payload, filename="large.bin"))
    request, _ = _request([body])
    policy = _policy(
        max_body_bytes=len(body),
        max_file_bytes=len(payload),
        max_aggregate_file_bytes=len(payload),
    )

    async with bounded_multipart_form(request, policy) as form:
        upload = form["file"]
        assert isinstance(upload, UploadFile)
        assert getattr(upload.file, "_rolled", False) is True
        assert await read_bounded_upload(upload, len(payload), "file") == payload

    assert upload.file.closed


@pytest.mark.asyncio
async def test_bounded_read_uses_limit_plus_one_and_normalizes_param() -> None:
    upload = _RecordingUpload(b"12345")

    with pytest.raises(MultipartPayloadTooLarge) as exc_info:
        await read_bounded_upload(upload, 4, "image[]")

    assert upload.requested_sizes == [5]
    assert exc_info.value.param == "image"

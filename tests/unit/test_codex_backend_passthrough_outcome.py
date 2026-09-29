"""Outcome classification of a relayed upstream stream (log seam).

Kept as a focused unit test by exception: an aiohttp ``ClientOSError`` raised
from the upstream body iterator is hard to provoke from a real server (aiohttp
wraps mid-body resets as ``ClientPayloadError``), yet ``ClientOSError`` also
subclasses ``OSError``, which the relay treats as the caller disconnecting.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import aiohttp
import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from app.core.clients.codex_backend import CodexBackendStream
from app.modules.proxy import codex_backend_passthrough as passthrough


class _UpstreamBodyFailingAfterFirstChunk:
    """Stand-in for the upstream HTTP response: one chunk, then a socket-level error."""

    def __init__(self, error: BaseException) -> None:
        self.content = self
        self._error = error
        self.status = 200
        self.headers = {"Content-Type": "text/event-stream"}

    def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        del size
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[bytes]:
        yield b"data: partial\\n\\n"
        raise self._error

    def release(self) -> None:
        return None


async def _relay(stream: CodexBackendStream, spec_version: str) -> None:
    response = passthrough._UpstreamStreamingResponse(stream, method="GET", path="wham/x", account_id="acc-1")

    async def receive() -> Message:
        await asyncio.Event().wait()  # the caller never disconnects
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        del message

    scope: Scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec_version}, "method": "GET"}
    await response(scope, receive, send)


# uvicorn reports ASGI 2.3 (the exception propagates as-is); under 2.4 Starlette
# re-raises an OSError from the body iterator as ClientDisconnect.
_SPEC_VERSIONS = pytest.mark.parametrize("spec_version", ["2.3", "2.4"])


@pytest.mark.asyncio
@_SPEC_VERSIONS
@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientOSError(104, "Connection reset by peer"), aiohttp.ClientPayloadError("payload not completed")],
    ids=["client-os-error", "client-payload-error"],
)
async def test_upstream_client_errors_are_logged_as_errors_not_disconnects(
    error: BaseException, spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=passthrough.__name__)
    stream = CodexBackendStream(status_code=200, headers={}, _response=_UpstreamBodyFailingAfterFirstChunk(error))

    with pytest.raises((type(error), ClientDisconnect)):
        await _relay(stream, spec_version)

    (record,) = [r for r in caplog.records if "outcome=" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "outcome=error" in record.getMessage()
    assert f"error={type(error).__name__}" in record.getMessage()


@pytest.mark.asyncio
@_SPEC_VERSIONS
async def test_caller_side_os_error_is_still_a_client_disconnect(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=passthrough.__name__)
    stream = CodexBackendStream(
        status_code=200, headers={}, _response=_UpstreamBodyFailingAfterFirstChunk(BrokenPipeError())
    )

    with pytest.raises((BrokenPipeError, ClientDisconnect)):
        await _relay(stream, spec_version)

    (record,) = [r for r in caplog.records if "outcome=" in r.getMessage()]
    assert record.levelno == logging.INFO
    assert "outcome=client_disconnect" in record.getMessage()

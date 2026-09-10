from __future__ import annotations

import asyncio
import contextlib
import gzip
import os
import zlib
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import anyio
import pytest
from aiohttp_retry import RetryClient

from app.core.clients import native_egress, usage
from app.core.clients.native_egress import NativeEgressTransportError, SubprocessNativeEgressClient
from tests.integration.test_native_sse_egress import _serve_http, _start_chunked_response, _write_chunk

pytestmark = pytest.mark.integration
_USAGE = b'{"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":12.5}}}'


@pytest.fixture
async def helper(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SubprocessNativeEgressClient]:
    binary = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not binary:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run native usage wire probes")
    assert Path(binary).is_file() and os.access(binary, os.X_OK), "native test binary must be executable"
    client = SubprocessNativeEgressClient(Path(binary))
    monkeypatch.setattr(usage, "discover_native_egress_client", lambda: client)
    monkeypatch.setattr(usage, "resolve_http_proxy_from_env", lambda _url: None)

    def unexpected_python(*_args, **_kwargs):
        raise AssertionError("native usage must not fall back to Python")

    monkeypatch.setattr(usage, "lease_retry_client", unexpected_python)
    try:
        yield client
    finally:
        await client.aclose()


async def _reply(
    writer: asyncio.StreamWriter,
    status: int = 200,
    body: bytes = _USAGE,
    content_type: str = "application/json",
    content_encoding: str | None = None,
) -> None:
    encoding_header = f"Content-Encoding: {content_encoding}\r\n" if content_encoding else ""
    writer.write(
        f"HTTP/1.1 {status} Test\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
        f"{encoding_header}Connection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _fetch(url: str, *, retries: int = 0, timeout: float = 2.0):
    return await usage.fetch_usage(
        access_token="usage-probe-token",
        account_id="usage-probe-account",
        base_url=url,
        max_retries=retries,
        timeout_seconds=timeout,
        allow_direct_egress=True,
    )


def _observe_headers(helper, monkeypatch):
    received = asyncio.Event()
    request = helper.request

    async def observe(value):
        response = await request(value)
        received.set()
        return response

    monkeypatch.setattr(helper, "request", observe)
    return received


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "content_type", "content_encoding"),
    [
        (200, _USAGE, "application/json", None),
        (401, b'{"error":{"code":"token_expired","message":"expired"}}', "application/json", None),
        (503, b"  temporarily unavailable\n", "text/plain", None),
        (401, b'["denied", 42]', "application/json", None),
        (200, b'{"plan_type":[]}', "application/json", None),
        (200, b"not JSON", "application/json", None),
        (502, b"bad utf8: \xff", "application/json", None),
        (503, b"  ", "application/json", None),
        (401, b'{"error":{"message":"caf\xe9"}}', "application/json; charset=iso-8859-1", None),
        (401, b"\xef\xbb\xbf{}", "application/json", None),
        pytest.param(200, gzip.compress(_USAGE), "application/json", "gzip", id="gzip"),
        pytest.param(200, zlib.compress(_USAGE), "application/json", "deflate", id="deflate"),
    ],
)
async def test_native_usage_matches_python_payload_and_error_mapping(
    helper, monkeypatch, status, body, content_type, content_encoding
):
    async def origin(reader, writer, head, request_body):
        assert head.startswith(b"GET /backend-api/wham/usage HTTP/1.1\r\n")
        assert b"authorization: bearer usage-probe-token" in head.lower()
        assert b"chatgpt-account-id: usage-probe-account" in head.lower()
        assert b"accept: application/json" in head.lower()
        assert not request_body
        await _reply(writer, status, body, content_type, content_encoding)

    async def outcome(url: str):
        try:
            return (await _fetch(url)).model_dump()
        except usage.UsageFetchError as exc:
            return exc.status_code, exc.message, exc.code
        except UnicodeDecodeError:
            return "invalid_encoding"

    async with _serve_http(origin) as url:
        native_result = await outcome(url)
        from app.core.clients.http import lease_retry_client

        monkeypatch.setattr(usage, "lease_retry_client", lease_retry_client)
        async with aiohttp.ClientSession(trust_env=False) as session, RetryClient(client_session=session) as client:
            try:
                python_result = (
                    await usage.fetch_usage(
                        access_token="usage-probe-token",
                        account_id="usage-probe-account",
                        base_url=url,
                        max_retries=0,
                        timeout_seconds=2,
                        allow_direct_egress=True,
                        client=client,
                    )
                ).model_dump()
            except usage.UsageFetchError as exc:
                python_result = exc.status_code, exc.message, exc.code
            except UnicodeDecodeError:
                python_result = "invalid_encoding"
    assert native_result == python_result
    assert not helper._streams


@pytest.mark.asyncio
async def test_native_usage_retries_status_without_waiting_for_body(helper):
    calls = 0
    closed = asyncio.Event()

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            await _start_chunked_response(writer, status="503 Busy", content_type="application/json")
            await reader.read()
            closed.set()
        else:
            assert closed.is_set(), "previous exchange must close before retry"
            await _reply(writer)

    async with _serve_http(origin) as url:
        assert (await _fetch(url, retries=1)).plan_type == "plus"
    assert calls == 2
    assert not helper._streams


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "body_timeout", "head_timeout"])
async def test_native_usage_preserves_transport_failures_and_peer(helper, failure):
    calls = 0

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        if calls > 1:
            await _reply(writer)
        elif failure == "truncated":
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n{"plan_type":')
            await writer.drain()
        else:
            if failure == "body_timeout":
                await _start_chunked_response(writer, content_type="application/json")
                await _write_chunk(writer, b'{"plan_type":')
            await reader.read()

    async with _serve_http(origin) as url:
        with pytest.raises(usage.UsageFetchError) as caught:
            await _fetch(url, retries=0 if failure == "head_timeout" else 2, timeout=0.15)
        assert caught.value.status_code == 0
        assert isinstance(caught.value.__cause__, NativeEgressTransportError)
        assert calls == 1, "body failures must not replay an already selected response"
        process = helper._process
        assert not helper._streams
        assert (await _fetch(url)).plan_type == "plus"
        assert helper._process is process


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["head", "body"])
@pytest.mark.parametrize("cancel_scope", [False, True], ids=["asyncio", "anyio"])
async def test_native_usage_cancellation_releases_only_its_exchange(helper, monkeypatch, phase, cancel_scope):
    entered = asyncio.Event()
    closed = asyncio.Event()
    calls = 0
    headers_received = _observe_headers(helper, monkeypatch)

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            if phase == "body":
                await _start_chunked_response(writer, content_type="application/json")
                await _write_chunk(writer, b'{"plan_type":')
            entered.set()
            await reader.read()
            closed.set()
        else:
            await _reply(writer)

    scope: anyio.CancelScope | None = None

    async def scoped_fetch(url: str):
        nonlocal scope
        with anyio.CancelScope() as scope:
            await _fetch(url, retries=2)
            pytest.fail("cancelled usage must not return a result")

    async with _serve_http(origin) as url:
        task = asyncio.create_task(scoped_fetch(url) if cancel_scope else _fetch(url, retries=2))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if phase == "body":
                await asyncio.wait_for(headers_received.wait(), 2)
            process = helper._process
            # An independent query remains usable while the first body is stalled.
            assert (await _fetch(url)).plan_type == "plus"
            if cancel_scope:
                assert scope is not None
                scope.cancel()
                await asyncio.wait_for(task, 2)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.wait_for(closed.wait(), 2)
            assert not helper._streams
            assert (await _fetch(url)).plan_type == "plus"
            assert helper._process is process
            assert calls == 3
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_native_usage_helper_exit_during_body_does_not_replay(helper, monkeypatch):
    entered = asyncio.Event()
    calls = 0
    headers_received = _observe_headers(helper, monkeypatch)

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        if calls == 1:
            await _start_chunked_response(writer, content_type="application/json")
            await _write_chunk(writer, b'{"plan_type":')
            entered.set()
            await reader.read()
        else:
            await _reply(writer)

    async with _serve_http(origin) as url:
        task = asyncio.create_task(_fetch(url, retries=2))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(headers_received.wait(), 2)
            process = helper._process
            assert process is not None
            process.kill()
            await process.wait()
            with pytest.raises(usage.UsageFetchError) as caught:
                await task
            assert caught.value.status_code == 0
            assert calls == 1
            assert (await _fetch(url)).plan_type == "plus"
            assert helper._process is not process
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, usage.UsageFetchError):
                await task


@pytest.mark.asyncio
async def test_native_usage_uses_environment_http_proxy(helper, monkeypatch):
    seen: list[bytes] = []

    async def proxy(reader, writer, head, body):
        seen.append(head)
        await _reply(writer)

    from app.core.utils.proxy_env import resolve_http_proxy_from_env

    async with _serve_http(proxy) as proxy_url:
        monkeypatch.setattr(
            usage,
            "resolve_http_proxy_from_env",
            lambda url: resolve_http_proxy_from_env(url, {"http_proxy": proxy_url}),
        )
        assert (await _fetch("http://usage.invalid")).plan_type == "plus"
    assert seen[0].startswith(b"GET http://usage.invalid/backend-api/wham/usage HTTP/1.1\r\n")


@pytest.mark.asyncio
async def test_native_usage_incompatible_helper_fails_before_dispatch(helper, monkeypatch):
    monkeypatch.setattr(
        native_egress,
        "_REQUIRED_NATIVE_CAPABILITIES",
        native_egress._REQUIRED_NATIVE_CAPABILITIES | {"usage-test-unsupported-capability"},
    )
    calls = 0

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        await _reply(writer)

    async with _serve_http(origin) as url:
        with pytest.raises(usage.UsageFetchError) as caught:
            await _fetch(url, retries=2)
    assert caught.value.status_code == 0
    assert isinstance(caught.value.__cause__, native_egress.NativeEgressProtocolError)
    assert calls == 0


@pytest.mark.asyncio
async def test_native_usage_missing_executable_falls_back_before_dispatch(helper, monkeypatch, tmp_path):
    missing = SubprocessNativeEgressClient(tmp_path / "missing-helper")
    monkeypatch.setattr(usage, "discover_native_egress_client", lambda: missing)
    calls = 0

    async def origin(reader, writer, head, body):
        nonlocal calls
        calls += 1
        await _reply(writer)

    async with _serve_http(origin) as url, aiohttp.ClientSession(trust_env=False) as session:
        async with RetryClient(client_session=session) as python_client:
            monkeypatch.setattr(usage, "lease_retry_client", lambda _: python_client)
            assert (await _fetch(url)).plan_type == "plus"
    await missing.aclose()
    assert missing._process is None
    assert calls == 1

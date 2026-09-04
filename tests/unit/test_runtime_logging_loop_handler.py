from __future__ import annotations

import asyncio
import gc
import logging
from collections.abc import Callable, Iterator

import pytest
import uvloop

from app.core import runtime_logging
from app.core.runtime_logging import install_redacting_loop_exception_handler
from tests.unit._proxy_test_helpers import runtime_basic_auth_url

pytestmark = pytest.mark.unit

_LOOP_FACTORIES = [
    pytest.param(asyncio.new_event_loop, id="asyncio"),
    pytest.param(uvloop.new_event_loop, id="uvloop"),
]
_PROXY_AUTHORITY = "183.110.26.193:6014"


class _LeakyConnection:
    password = "SECRETPW"

    def __repr__(self) -> str:
        proxy_url = runtime_basic_auth_url("smart-user", self.password, _PROXY_AUTHORITY)
        return f"Connection<ConnectionKey(host='chatgpt.com', port=443, proxy=URL('{proxy_url}'), proxy_auth=None)>"


class _ApostropheLeakyConnection(_LeakyConnection):
    # yarl leaves the RFC 3986 sub-delim ' unencoded in userinfo, so this is
    # the exact repr aiohttp produces for such a password.
    password = "S'ECRETPW"


class _ExplodingRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failure")


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


@pytest.fixture
def asyncio_log() -> Iterator[_Recorder]:
    logger = logging.getLogger("asyncio")
    recorder = _Recorder()
    logger.addHandler(recorder)
    previous_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield recorder
    finally:
        logger.removeHandler(recorder)
        logger.setLevel(previous_level)


@pytest.fixture(params=_LOOP_FACTORIES)
def loop(request: pytest.FixtureRequest) -> Iterator[asyncio.AbstractEventLoop]:
    factory: Callable[[], asyncio.AbstractEventLoop] = request.param
    loop = factory()
    try:
        yield loop
    finally:
        loop.close()


def test_handler_redacts_credentialed_context_reprs(loop, asyncio_log) -> None:
    install_redacting_loop_exception_handler(loop)

    loop.call_exception_handler({"message": "Unclosed connection", "client_connection": _LeakyConnection()})

    (message,) = asyncio_log.messages
    assert message.startswith("Unclosed connection\nclient_connection: Connection<ConnectionKey(")
    assert "SECRETPW" not in message
    assert f"[REDACTED]@{_PROXY_AUTHORITY}" in message


def test_handler_redacts_apostrophe_password_in_context_repr(loop, asyncio_log) -> None:
    install_redacting_loop_exception_handler(loop)

    loop.call_exception_handler({"message": "Unclosed connection", "client_connection": _ApostropheLeakyConnection()})

    (message,) = asyncio_log.messages
    assert "S'ECRETPW" not in message
    assert "ECRETPW" not in message
    assert f"proxy=URL('http://[REDACTED]@{_PROXY_AUTHORITY}')" in message


def test_handler_keeps_secret_free_context_byte_identical(loop, asyncio_log) -> None:
    exc = RuntimeError("boom")
    context = {"message": "Task exception was never retrieved", "exception": exc, "future": object()}

    loop.default_exception_handler(dict(context))
    install_redacting_loop_exception_handler(loop)
    loop.call_exception_handler(context)

    baseline, redacted = asyncio_log.records
    assert redacted.getMessage() == baseline.getMessage()
    assert redacted.exc_info == baseline.exc_info
    assert redacted.exc_info is not None and redacted.exc_info[1] is exc


def test_install_is_idempotent(loop) -> None:
    install_redacting_loop_exception_handler(loop)
    handler = loop.get_exception_handler()

    install_redacting_loop_exception_handler(loop)

    assert loop.get_exception_handler() is handler


def test_handler_chains_previously_installed_handler(loop) -> None:
    seen: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: seen.append(dict(context)))
    install_redacting_loop_exception_handler(loop)

    loop.call_exception_handler({"message": "Unclosed connection", "client_connection": _LeakyConnection()})

    (context,) = seen
    assert context["message"] == "Unclosed connection"
    assert "SECRETPW" not in repr(context["client_connection"])
    assert f"[REDACTED]@{_PROXY_AUTHORITY}" in repr(context["client_connection"])


def test_handler_replaces_exploding_repr_with_opaque_stand_in(loop, asyncio_log) -> None:
    install_redacting_loop_exception_handler(loop)

    loop.call_exception_handler({"message": "Unclosed connection", "client_connection": _ExplodingRepr()})

    (message,) = asyncio_log.messages
    # The default handler never sees the raw object, so the message line
    # survives instead of asyncio's generic "Unhandled error in exception handler".
    assert message == "Unclosed connection\nclient_connection: <_ExplodingRepr repr failed: RuntimeError>"


def test_handler_fails_closed_when_redaction_pass_raises(loop, asyncio_log, monkeypatch) -> None:
    def _broken_redaction(text: str, **_kwargs: object) -> str:
        raise ValueError("redaction broke")

    monkeypatch.setattr(runtime_logging, "redact_rendered_log_text", _broken_redaction)
    install_redacting_loop_exception_handler(loop)
    exc = RuntimeError("boom")

    loop.call_exception_handler(
        {"message": "Unclosed connection", "exception": exc, "client_connection": _LeakyConnection()}
    )

    (record,) = asyncio_log.records
    message = record.getMessage()
    assert message.startswith("Unclosed connection\n")
    assert "SECRETPW" not in message
    assert "client_connection: [REDACTED: loop context redaction failed]" in message
    assert record.exc_info is not None and record.exc_info[1] is exc


@pytest.mark.parametrize("loop_factory", _LOOP_FACTORIES)
def test_unretrieved_task_exception_repr_is_redacted(loop_factory, asyncio_log) -> None:
    async def _main() -> None:
        install_redacting_loop_exception_handler(asyncio.get_running_loop())

        async def _boom() -> None:
            raise RuntimeError(runtime_basic_auth_url("u", "TASKPW", "h") + "/")

        task = asyncio.get_running_loop().create_task(_boom())
        await asyncio.sleep(0)
        assert task.done()
        del task
        gc.collect()
        await asyncio.sleep(0)

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(_main())

    messages = [message for message in asyncio_log.messages if "Task exception was never retrieved" in message]
    assert messages, asyncio_log.messages
    assert all("TASKPW" not in message for message in messages)
    assert any("http://[REDACTED]@h/" in message for message in messages)


@pytest.mark.parametrize("loop_factory", _LOOP_FACTORIES)
def test_unretrieved_task_client_http_proxy_error_repr_is_redacted(loop_factory, asyncio_log) -> None:
    # Task repr embeds repr(exception); aiohttp's ClientHttpProxyError repr
    # carries the CONNECT Proxy-Authorization header, a reversible Basic token.
    import base64

    import aiohttp
    from aiohttp.client_reqrep import ClientRequest
    from yarl import URL

    from app.core.upstream_proxy import ResolvedProxyEndpoint

    token = base64.b64encode(b"smart-user:SECRETPW").decode()

    async def _main() -> None:
        running = asyncio.get_running_loop()
        install_redacting_loop_exception_handler(running)
        endpoint = ResolvedProxyEndpoint("ep", "https", "proxy.test", 8080, "smart-user", "SECRETPW")
        proxy_headers = endpoint.aiohttp_proxy_kwargs()["proxy_headers"]

        async def _boom() -> None:
            request = ClientRequest("CONNECT", URL("https://chatgpt.com/"), headers=proxy_headers, loop=running)
            raise aiohttp.ClientHttpProxyError(request.request_info, (), status=502, message="nope")

        task = running.create_task(_boom())
        await asyncio.sleep(0)
        assert task.done()
        assert token in repr(task)
        del task
        gc.collect()
        await asyncio.sleep(0)

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(_main())

    messages = [message for message in asyncio_log.messages if "Task exception was never retrieved" in message]
    assert messages, asyncio_log.messages
    assert all(token not in message and "SECRETPW" not in message for message in messages)
    assert any("'Proxy-Authorization': 'Basic [REDACTED]'" in message for message in messages)
